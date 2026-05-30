"""
CataSwarm - Phone Beta (Drone Broker)
Lightweight single-threaded Python passthrough for Termux
Requirements: 11-13 from .kiro/specs/cataswarm-system/requirements.md

Architecture:
  - Single asyncio event loop
  - BLE listener for BETA_SENSORS telemetry
  - 7-second camera capture & dual-stream forward
  - FastAPI override listener on port 8080
  - In-memory queue with 50-entry cap for resilience
"""

import asyncio
import base64
import json
import logging
import math
import os
import threading
from collections import deque
from datetime import datetime, timezone

import httpx
import uvicorn
from bleak import BleakClient, BleakScanner
from fastapi import FastAPI, HTTPException, Request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLOUD_ENDPOINT = os.environ.get("CLOUD_URL", "http://localhost:3000/api/telemetry")
PHONE_ALPHA_IP = os.environ.get("PHONE_ALPHA_IP", "192.168.1.100")
PHONE_ALPHA_SYNC_URL = f"http://{PHONE_ALPHA_IP}:8080/subordinate/sync"
BETA_HUB_NAME = os.environ.get("BETA_HUB_NAME", "Pybricks Hub Beta")
CAMERA_PATH = "beta_view.jpg"
TELEMETRY_INTERVAL = 7  # seconds
NETWORK_TIMEOUT = 5  # seconds
QUEUE_MAX_SIZE = 50
BLE_RECONNECT_INTERVAL = 2  # seconds

# BLE UART UUIDs (Nordic UART)
UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to hub
UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Read from hub

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Beta] %(levelname)s: %(message)s",
)
log = logging.getLogger("beta_broker")

# ---------------------------------------------------------------------------
# Shared State
# ---------------------------------------------------------------------------
beta_coords = {"x": 0.0, "y": 0.0}
beta_sensors = {"distance_mm": -1, "color": "none"}
_heading_deg = 0.0
_STEP_MM = 10.0  # approximate distance per telemetry tick

# In-memory volatile queue
telemetry_queue = deque(maxlen=QUEUE_MAX_SIZE)

# BLE client reference
ble_client = None
ble_connected = asyncio.Event()


# ---------------------------------------------------------------------------
# 1. Dead-Reckoning & Telemetry Parsing
# ---------------------------------------------------------------------------
def update_dead_reckoning(distance_mm, color):
    """Update Beta's (x, y) position estimate."""
    global _heading_deg
    rad = math.radians(_heading_deg)
    beta_coords["x"] += _STEP_MM * math.sin(rad)
    beta_coords["y"] += _STEP_MM * math.cos(rad)
    beta_sensors["distance_mm"] = distance_mm
    beta_sensors["color"] = color


def parse_telemetry(data: bytes):
    """Parse BETA_SENSORS:<distance>:<color> packet."""
    try:
        text = data.decode("utf-8").strip()
        if not text.startswith("BETA_SENSORS:"):
            log.warning("Unexpected packet: %s", text[:40])
            return
        parts = text.split(":")
        if len(parts) != 3:
            log.warning("Malformed packet field count: %d", len(parts))
            return
        distance = int(parts[1])
        color = parts[2].lower()
        update_dead_reckoning(distance, color)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("Telemetry parse error: %s", e)


# ---------------------------------------------------------------------------
# 2. BLE Interface — Sensor Capture Integration
# ---------------------------------------------------------------------------
async def ble_listener():
    """Persistent BLE connection to Beta Hub with auto-reconnect."""
    global ble_client

    while True:
        try:
            log.info("Scanning for Beta Hub: %s", BETA_HUB_NAME)
            device = await BleakScanner.find_device_by_name(BETA_HUB_NAME, timeout=10)

            if device is None:
                log.warning("Hub not found, retrying in %ds...", BLE_RECONNECT_INTERVAL)
                await asyncio.sleep(BLE_RECONNECT_INTERVAL)
                continue

            async with BleakClient(device) as client:
                ble_client = client
                ble_connected.set()
                log.info("Connected to Beta Hub: %s", device.address)

                def notification_handler(sender, data):
                    parse_telemetry(data)

                await client.start_notify(UART_TX_UUID, notification_handler)

                while client.is_connected:
                    await asyncio.sleep(1)

                ble_connected.clear()
                log.warning("BLE disconnected, reconnecting...")

        except Exception as e:
            ble_connected.clear()
            log.error("BLE error: %s, retrying in %ds", e, BLE_RECONNECT_INTERVAL)
            await asyncio.sleep(BLE_RECONNECT_INTERVAL)


async def send_ble_command(command: str):
    """Send override command to Beta Hub over BLE UART."""
    if ble_client and ble_client.is_connected:
        payload = (command + "\n").encode("utf-8")
        await ble_client.write_gatt_char(UART_RX_UUID, payload)
        log.info("BLE TX: %s", command)
        return True
    else:
        log.warning("BLE not connected, cannot send: %s", command)
        return False


# ---------------------------------------------------------------------------
# 3. Capture & Forward Loop (7-Second Window)
# ---------------------------------------------------------------------------
async def capture_camera() -> str | None:
    """Capture image via termux-camera-photo, return base64 or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "termux-camera-photo", "-c", "1", CAMERA_PATH,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0:
            log.warning("Camera capture failed (exit %d)", proc.returncode)
            return None

        with open(CAMERA_PATH, "rb") as f:
            img_data = f.read()

        return base64.b64encode(img_data).decode("ascii")

    except Exception as e:
        log.warning("Camera error: %s", e)
        return None


async def post_to_endpoint(url: str, payload: dict) -> bool:
    """POST JSON to a URL. Returns True on success (2xx)."""
    try:
        async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code in (200, 201)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        log.warning("POST to %s failed: %s", url, e)
        return False


async def forward_telemetry_loop():
    """Main 7-second capture-and-dual-stream loop."""
    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        timestamp = datetime.now(timezone.utc).isoformat()
        image_b64 = await capture_camera()

        payload = {
            "robot_id": "robot_beta",
            "timestamp": timestamp,
            "coords": {"x": beta_coords["x"], "y": beta_coords["y"]},
            "sensor_data": {
                "distance_mm": beta_sensors["distance_mm"],
                "color": beta_sensors["color"],
            },
            "image_b64": image_b64,
            # Fields for Phone Alpha's /subordinate/sync schema
            "distance_mm": beta_sensors["distance_mm"],
            "color_string": beta_sensors["color"],
            "position_x": beta_coords["x"],
            "position_y": beta_coords["y"],
        }

        # Dual-stream: fire both POSTs concurrently
        cloud_ok, alpha_ok = await asyncio.gather(
            post_to_endpoint(CLOUD_ENDPOINT, payload),
            post_to_endpoint(PHONE_ALPHA_SYNC_URL, payload),
        )

        if not cloud_ok:
            log.info("Cloud unreachable, queuing payload")
            telemetry_queue.append(("cloud", payload.copy()))

        if not alpha_ok:
            log.info("Alpha unreachable, queuing payload")
            telemetry_queue.append(("alpha", payload.copy()))

        # Retry queued items
        await retry_queued()


async def retry_queued():
    """Attempt to flush queued payloads."""
    retries = min(5, len(telemetry_queue))  # Process up to 5 per cycle
    for _ in range(retries):
        if not telemetry_queue:
            break
        dest, payload = telemetry_queue[0]

        if dest == "cloud":
            success = await post_to_endpoint(CLOUD_ENDPOINT, payload)
        else:
            success = await post_to_endpoint(PHONE_ALPHA_SYNC_URL, payload)

        if success:
            telemetry_queue.popleft()
            log.info("Retried queued %s payload successfully", dest)
        else:
            break  # Stop retrying if still failing


# ---------------------------------------------------------------------------
# 4. Override Directive Listener (FastAPI)
# ---------------------------------------------------------------------------
app = FastAPI(title="CataSwarm Beta Broker")

# Command mapping: JSON command field -> BLE override string
COMMAND_MAP = {
    "ARM_OPEN": "OVERRIDE_ARM_OPEN",
    "ARM_CLOSE": "OVERRIDE_ARM_CLOSE",
    "STOP": "OVERRIDE_STOP",
}

# Queue for commands when BLE is disconnected
override_queue = deque(maxlen=20)


@app.post("/command-override")
async def command_override(request: Request):
    """Receive override commands from Phone Alpha and route to Beta Hub."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON")

    command = body.get("command")
    if not command or not isinstance(command, str):
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid 'command' field",
        )

    # Translate command to BLE override string
    command_upper = command.upper()

    if command_upper in COMMAND_MAP:
        ble_cmd = COMMAND_MAP[command_upper]
    elif command_upper.startswith("OVERRIDE_MOVE:"):
        # Already formatted: OVERRIDE_MOVE:<speed>:<steering>
        ble_cmd = command_upper
    elif command_upper.startswith("MOVE:"):
        # Short form: MOVE:<speed>:<steering> -> OVERRIDE_MOVE:<speed>:<steering>
        parts = command_upper.split(":")
        if len(parts) == 3:
            ble_cmd = f"OVERRIDE_MOVE:{parts[1]}:{parts[2]}"
        else:
            raise HTTPException(status_code=400, detail="Invalid MOVE command format")
    elif command_upper == "OVERRIDE_STOP":
        ble_cmd = "OVERRIDE_STOP"
    elif command_upper == "OVERRIDE_ARM_OPEN":
        ble_cmd = "OVERRIDE_ARM_OPEN"
    elif command_upper == "OVERRIDE_ARM_CLOSE":
        ble_cmd = "OVERRIDE_ARM_CLOSE"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unrecognized command: {command}",
        )

    # Attempt BLE send
    if ble_connected.is_set():
        success = await send_ble_command(ble_cmd)
        if success:
            return {"status": "ok", "sent": ble_cmd}
        else:
            override_queue.append(ble_cmd)
            raise HTTPException(
                status_code=503,
                detail="BLE write failed, command queued",
            )
    else:
        override_queue.append(ble_cmd)
        raise HTTPException(
            status_code=503,
            detail="BLE not connected, command queued for delivery",
        )


async def flush_override_queue():
    """Periodically flush queued override commands when BLE reconnects."""
    while True:
        await asyncio.sleep(1)
        if ble_connected.is_set() and override_queue:
            cmd = override_queue.popleft()
            await send_ble_command(cmd)


def run_fastapi():
    """Run FastAPI server in a background thread."""
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


# ---------------------------------------------------------------------------
# 5. Main Entry Point
# ---------------------------------------------------------------------------
async def main():
    """Launch all async tasks."""
    log.info("=== CataSwarm Beta Broker starting ===")

    # Start FastAPI in background thread
    api_thread = threading.Thread(target=run_fastapi, daemon=True, name="FastAPI")
    api_thread.start()
    log.info("FastAPI override listener started on port 8080")

    # Run async tasks concurrently
    await asyncio.gather(
        ble_listener(),
        forward_telemetry_loop(),
        flush_override_queue(),
    )


if __name__ == "__main__":
    asyncio.run(main())
