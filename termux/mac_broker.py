"""
CataSwarm — Mac-in-the-Middle BLE Broker
=========================================
Runs on macOS. Owns ALL physical Bluetooth connections to both Pybricks hubs
via CoreBluetooth (bleak). The Android phones are completely decoupled from BLE
and communicate with this process over Wi-Fi only.

Architecture
------------
  BLE loop A  ──► Robot Alpha Hub  (name: "Pybricks Hub")
  BLE loop B  ──► Robot Beta Hub   (name: "Pybricks Hub Beta")
  FastAPI     ──► POST /api/alpha/command   (phone leader → Alpha chassis)
              ──► POST /api/beta/command    (phone beta   → Beta chassis)
              ──► POST /api/telemetry       (passthrough  → cloud server)
              ──► GET  /api/status          (health / sim-mode flags)

Single-brick protection
-----------------------
Each BLE loop is fully independent. If a hub is not found within 10 s, that
robot is marked is_simulated=True and the loop falls back to software telemetry.
The other robot's loop is completely unaffected.

Environment variables
---------------------
  CLOUD_URL          Cloud Express server  (default: http://localhost:3000/api/telemetry)
  PHONE_ALPHA_IP     Leader phone IP       (default: 192.168.1.100)
  BROKER_PORT        This FastAPI port     (default: 8888)
  ALPHA_HUB_NAME     BLE name for Alpha    (default: Pybricks Hub)
  BETA_HUB_NAME      BLE name for Beta     (default: Pybricks Hub Beta)
"""

import asyncio
import base64
import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import uvicorn
from bleak import BleakClient, BleakScanner
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLOUD_URL        = os.environ.get("CLOUD_URL",       "http://localhost:3000/api/telemetry")
PHONE_ALPHA_IP   = os.environ.get("PHONE_ALPHA_IP",  "192.168.1.100")
BROKER_PORT      = int(os.environ.get("BROKER_PORT", "8888"))
ALPHA_HUB_NAME   = os.environ.get("ALPHA_HUB_NAME", "Pybricks Hub")
BETA_HUB_NAME    = os.environ.get("BETA_HUB_NAME",  "Pybricks Hub Beta")

BLE_SCAN_TIMEOUT      = 10.0   # seconds per scan attempt
BLE_RECONNECT_DELAY   = 3.0    # seconds between reconnect attempts
TELEMETRY_INTERVAL    = 7      # seconds between simulated telemetry ticks
NETWORK_TIMEOUT       = 5      # seconds for outbound HTTP calls
COMMAND_QUEUE_MAX     = 50

# Nordic UART UUIDs (same on both hubs)
UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write → hub
UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify ← hub

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MacBroker] %(levelname)s: %(message)s",
)
log = logging.getLogger("mac_broker")

# ---------------------------------------------------------------------------
# Per-robot state containers
# ---------------------------------------------------------------------------
class RobotState:
    """Holds live state for one physical robot."""

    def __init__(self, robot_id: str, hub_name: str):
        self.robot_id    = robot_id          # "robot_alpha" | "robot_beta"
        self.hub_name    = hub_name          # BLE advertisement name
        self.is_simulated = True             # True until BLE connects
        self.ble_client: BleakClient | None = None
        self.connected   = asyncio.Event()

        # Dead-reckoning
        self.coords      = {"x": 0.0, "y": 0.0}
        self.sensors     = {"distance_mm": -1, "color": "none"}
        self._heading    = 0.0
        self._step_mm    = 10.0

        # Outbound command queue (used when BLE is momentarily unavailable)
        self.cmd_queue: deque[str] = deque(maxlen=COMMAND_QUEUE_MAX)

    # ------------------------------------------------------------------
    # Telemetry parsing
    # ------------------------------------------------------------------
    def parse_notification(self, data: bytes):
        """Parse ALPHA_SENSORS / BETA_SENSORS packet from hub."""
        try:
            text = data.decode("utf-8").strip()
            prefix = self.robot_id.upper().replace("ROBOT_", "") + "_SENSORS:"
            if not text.startswith(prefix):
                return
            parts = text.split(":")
            if len(parts) != 3:
                return
            distance = int(parts[1])
            color    = parts[2].lower()
            self._update_dead_reckoning(distance, color)
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("[%s] Telemetry parse error: %s", self.robot_id, exc)

    def _update_dead_reckoning(self, distance_mm: int, color: str):
        rad = math.radians(self._heading)
        self.coords["x"] += self._step_mm * math.sin(rad)
        self.coords["y"] += self._step_mm * math.cos(rad)
        self.sensors["distance_mm"] = distance_mm
        self.sensors["color"]       = color

    # ------------------------------------------------------------------
    # BLE write
    # ------------------------------------------------------------------
    async def send_command(self, command: str) -> bool:
        """
        Write a command string to the hub over BLE UART.
        Returns True on success, False if not connected (queues the command).
        """
        if self.ble_client and self.ble_client.is_connected:
            try:
                payload = (command + "\n").encode("utf-8")
                await self.ble_client.write_gatt_char(UART_RX_UUID, payload)
                log.info("[%s] BLE TX: %s", self.robot_id, command)
                return True
            except Exception as exc:
                log.warning("[%s] BLE write failed: %s", self.robot_id, exc)

        # Not connected — queue for later delivery
        self.cmd_queue.append(command)
        log.warning("[%s] BLE not connected — queued: %s", self.robot_id, command)
        return False

    # ------------------------------------------------------------------
    # Simulated telemetry snapshot
    # ------------------------------------------------------------------
    def simulated_telemetry_payload(self) -> dict:
        """Generate a software-only telemetry frame when hub is offline."""
        self.coords["x"] += 5.0  # advance along a straight line
        return {
            "robot_id":   self.robot_id,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "coords":     {"x": self.coords["x"], "y": self.coords["y"]},
            "sensor_data": {
                "distance_mm": 120,
                "color":       "none",
            },
            "image_b64": _fallback_image_b64(),
        }


# ---------------------------------------------------------------------------
# Shared robot instances
# ---------------------------------------------------------------------------
alpha = RobotState("robot_alpha", ALPHA_HUB_NAME)
beta  = RobotState("robot_beta",  BETA_HUB_NAME)


# ---------------------------------------------------------------------------
# Fallback image helper
# ---------------------------------------------------------------------------
def _fallback_image_b64() -> str:
    """Return a 1×1 transparent PNG as the emergency image fallback."""
    return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
async def post_json(url: str, payload: dict) -> bool:
    """Fire-and-forget POST. Returns True on 2xx."""
    try:
        async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code in (200, 201)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        log.warning("POST %s failed: %s", url, exc)
        return False


# ---------------------------------------------------------------------------
# BLE connection loop — one per robot, fully independent
# ---------------------------------------------------------------------------
async def ble_loop(robot: RobotState):
    """
    Persistent BLE connection loop for a single robot.

    SINGLE-BRICK PROTECTION: wrapped in its own try/except. A failure here
    never propagates to the other robot's loop. If the hub is not found within
    BLE_SCAN_TIMEOUT seconds, is_simulated stays True and we retry indefinitely
    in the background without blocking anything else.
    """
    log.info("[%s] BLE loop starting — scanning for '%s'", robot.robot_id, robot.hub_name)

    while True:
        try:
            device = await BleakScanner.find_device_by_name(
                robot.hub_name, timeout=BLE_SCAN_TIMEOUT
            )

            if device is None:
                log.warning(
                    "[%s] Hub '%s' not found after %.0fs scan — staying in simulation mode, retrying...",
                    robot.robot_id, robot.hub_name, BLE_SCAN_TIMEOUT,
                )
                robot.is_simulated = True
                await asyncio.sleep(BLE_RECONNECT_DELAY)
                continue

            log.info("[%s] Hub found: %s [%s]", robot.robot_id, device.name, device.address)

            async with BleakClient(device) as client:
                robot.ble_client  = client
                robot.is_simulated = False
                robot.connected.set()
                log.info("[%s] ✅ BLE connected — live mode active", robot.robot_id)

                # Flush any queued commands that arrived while disconnected
                while robot.cmd_queue:
                    cmd = robot.cmd_queue.popleft()
                    await robot.send_command(cmd)

                # Subscribe to sensor notifications
                await client.start_notify(
                    UART_TX_UUID,
                    lambda _sender, data: robot.parse_notification(data),
                )

                # Hold connection until hub disconnects
                while client.is_connected:
                    await asyncio.sleep(1)

                robot.connected.clear()
                robot.is_simulated = True
                log.warning("[%s] BLE disconnected — falling back to simulation", robot.robot_id)

        except Exception as exc:
            # SINGLE-BRICK PROTECTION: catch everything so this loop never dies
            robot.connected.clear()
            robot.is_simulated = True
            log.error(
                "[%s] BLE error (simulation mode active): %s — retrying in %.0fs",
                robot.robot_id, exc, BLE_RECONNECT_DELAY,
            )
            await asyncio.sleep(BLE_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# Telemetry forward loop — runs for both robots, posts to cloud
# ---------------------------------------------------------------------------
async def telemetry_loop(robot: RobotState):
    """
    Every TELEMETRY_INTERVAL seconds, build a telemetry payload and POST it
    to the cloud server. Uses live BLE sensor data when connected, otherwise
    generates a simulated frame.
    """
    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        if robot.is_simulated:
            payload = robot.simulated_telemetry_payload()
            log.debug("[%s] Sending simulated telemetry", robot.robot_id)
        else:
            payload = {
                "robot_id":   robot.robot_id,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "coords":     {"x": robot.coords["x"], "y": robot.coords["y"]},
                "sensor_data": {
                    "distance_mm": robot.sensors["distance_mm"],
                    "color":       robot.sensors["color"],
                },
                "image_b64": None,
            }

        # Device-state ping for the dashboard edge-node card
        color = robot.sensors["color"]
        if color == "blue":
            dev_status, dev_cmd = "TARGET_LOCKED", "STARTUP"
        elif not robot.is_simulated:
            dev_status, dev_cmd = "INITIALIZING", "STARTUP"
        else:
            dev_status, dev_cmd = "STABLE_HOLD", "HOLD"

        state_payload = {
            "device_id":      robot.robot_id,
            "status":         dev_status,
            "target_command": dev_cmd,
            "timestamp":      time.time(),
        }

        await asyncio.gather(
            post_json(CLOUD_URL, payload),
            post_json(CLOUD_URL, state_payload),
        )


# ---------------------------------------------------------------------------
# FastAPI — command relay + health
# ---------------------------------------------------------------------------
app = FastAPI(title="CataSwarm Mac Broker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _parse_command_body(body: dict) -> str:
    """
    Accept either:
      { "action": "DRIVE", "value": 500 }   → "DRIVE:500"
      { "action": "TURN",  "value": 90  }   → "TURN:90"
      { "action": "STOP"               }    → "STOP"
      { "action": "ARM_CLOSE"          }    → "ARM_CLOSE"
      { "command": "OVERRIDE_STOP"     }    → "OVERRIDE_STOP"  (legacy shape)
    """
    # Legacy shape from old broker
    if "command" in body:
        return str(body["command"]).strip()

    action = body.get("action")
    if not action or not isinstance(action, str):
        raise ValueError("Missing or invalid 'action' field")

    action = action.upper().strip()
    value  = body.get("value")

    if value is not None:
        return f"{action}:{int(value)}"
    return action


@app.post("/api/alpha/command")
async def alpha_command(request: Request):
    """
    Receive a motor action from the phone leader and relay it to Robot Alpha.

    If Alpha is live on BLE → write to UART characteristic immediately.
    If Alpha is simulated   → log the command to console (no crash).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON")

    try:
        cmd = _parse_command_body(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if alpha.is_simulated:
        log.info("[SIMULATED ALPHA] Command received (no BLE): %s", cmd)
        return {"status": "simulated", "command": cmd}

    success = await alpha.send_command(cmd)
    if success:
        return {"status": "sent", "command": cmd}

    # send_command already queued it
    return {"status": "queued", "command": cmd}


@app.post("/api/beta/command")
async def beta_command(request: Request):
    """
    Relay a command to Robot Beta chassis.
    Mirrors /api/alpha/command — used by the phone beta broker if needed.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON")

    try:
        cmd = _parse_command_body(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if beta.is_simulated:
        log.info("[SIMULATED BETA] Command received (no BLE): %s", cmd)
        return {"status": "simulated", "command": cmd}

    success = await beta.send_command(cmd)
    if success:
        return {"status": "sent", "command": cmd}

    return {"status": "queued", "command": cmd}


@app.post("/api/beta/command-override")
async def beta_command_override(request: Request):
    """Legacy endpoint shape used by phone_alpha daemon's send_override_to_beta."""
    return await beta_command(request)


@app.get("/api/status")
async def status():
    """Health + simulation-mode flags for both robots."""
    return {
        "alpha": {
            "robot_id":     alpha.robot_id,
            "is_simulated": alpha.is_simulated,
            "ble_connected": not alpha.is_simulated,
            "coords":       alpha.coords,
            "sensors":      alpha.sensors,
            "queued_cmds":  len(alpha.cmd_queue),
        },
        "beta": {
            "robot_id":     beta.robot_id,
            "is_simulated": beta.is_simulated,
            "ble_connected": not beta.is_simulated,
            "coords":       beta.coords,
            "sensors":      beta.sensors,
            "queued_cmds":  len(beta.cmd_queue),
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def main():
    log.info("=== CataSwarm Mac-in-the-Middle Broker starting ===")
    log.info("Cloud endpoint : %s", CLOUD_URL)
    log.info("Broker port    : %d", BROKER_PORT)
    log.info("Alpha hub name : %s", ALPHA_HUB_NAME)
    log.info("Beta hub name  : %s", BETA_HUB_NAME)

    # Uvicorn config — run in same event loop as bleak
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=BROKER_PORT,
        log_level="warning",
        loop="none",   # use the already-running asyncio loop
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        # Independent BLE loops — single-brick protection
        ble_loop(alpha),
        ble_loop(beta),
        # Telemetry forward loops
        telemetry_loop(alpha),
        telemetry_loop(beta),
        # HTTP API
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
