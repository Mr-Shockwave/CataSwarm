"""
CataSwarm - Phone Alpha (Edge Brain)
Asynchronous multi-threaded Python daemon for Termux
Requirements: 7-10 from .kiro/specs/cataswarm-system/requirements.md

Architecture:
  - Thread 1: BLE listener (bleak) — parses ALPHA_SENSORS telemetry
  - Thread 2: 7-second telemetry snap loop (camera + cloud POST)
  - Thread 3: FastAPI server on port 8080 (subordinate sync endpoint)
  - Thread 4: Gemma intercept engine (LLM decision loop)
"""

import asyncio
import base64
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
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
PHONE_BETA_IP = os.environ.get("PHONE_BETA_IP", "192.168.1.101")
PHONE_BETA_OVERRIDE_URL = f"http://{PHONE_BETA_IP}:8080/command-override"
GEMMA_URL = os.environ.get("GEMMA_URL", "http://localhost:11434/api/generate")
ALPHA_HUB_NAME = os.environ.get("ALPHA_HUB_NAME", "Pybricks Hub")
DB_PATH = "leader_buffer.db"
CAMERA_PATH = "alpha_view.jpg"
TELEMETRY_INTERVAL = 7  # seconds
CLOUD_TIMEOUT = 10  # seconds
MAX_BUFFERED_IMAGES = 50
BLE_RECONNECT_INTERVAL = 2  # seconds

# BLE UART Service UUIDs (Nordic UART)
UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to hub
UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Read from hub

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("alpha_daemon")

# ---------------------------------------------------------------------------
# Shared State
# ---------------------------------------------------------------------------
alpha_coords = {"x": 0.0, "y": 0.0}
alpha_sensors = {"distance_mm": -1, "color": "none"}
beta_coords = {"x": 0.0, "y": 0.0}
beta_sensors = {"distance_mm": -1, "color": "none"}
intercept_in_progress = False
intercept_lock = threading.Lock()
ble_client = None
ble_connected = threading.Event()

# Dead-reckoning state
_heading_deg = 0.0  # degrees, 0 = forward/north
_STEP_MM = 10.0     # approximate distance per telemetry tick at cruise speed


# ---------------------------------------------------------------------------
# 1. Database State
# ---------------------------------------------------------------------------
def init_database():
    """Initialize SQLite with telemetry tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS my_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            position_x REAL NOT NULL,
            position_y REAL NOT NULL,
            distance_mm INTEGER,
            color TEXT,
            image_b64 TEXT,
            synced INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS subordinate_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            robot_id TEXT NOT NULL,
            position_x REAL NOT NULL,
            position_y REAL NOT NULL,
            distance_mm INTEGER,
            color TEXT,
            image_b64 TEXT
        )
    """)
    conn.commit()
    conn.close()
    log.info("Database initialized: %s", DB_PATH)


# ---------------------------------------------------------------------------
# 2. BLE Interface Core
# ---------------------------------------------------------------------------
import math

def update_dead_reckoning(distance_mm, color):
    """Update Alpha's (x, y) position estimate from telemetry."""
    global _heading_deg
    # Simple dead-reckoning: assume robot moved _STEP_MM forward since last tick
    rad = math.radians(_heading_deg)
    alpha_coords["x"] += _STEP_MM * math.sin(rad)
    alpha_coords["y"] += _STEP_MM * math.cos(rad)
    alpha_sensors["distance_mm"] = distance_mm
    alpha_sensors["color"] = color


def parse_telemetry(data: bytes):
    """Parse ALPHA_SENSORS:<distance>:<color> packet."""
    try:
        text = data.decode("utf-8").strip()
        if not text.startswith("ALPHA_SENSORS:"):
            return
        parts = text.split(":")
        if len(parts) != 3:
            return
        distance = int(parts[1])
        color = parts[2].lower()
        update_dead_reckoning(distance, color)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("Telemetry parse error: %s", e)


async def ble_listener():
    """Persistent BLE connection to Alpha Hub with auto-reconnect."""
    global ble_client

    while True:
        try:
            log.info("Scanning for Alpha Hub: %s", ALPHA_HUB_NAME)
            device = await BleakScanner.find_device_by_name(ALPHA_HUB_NAME, timeout=10)

            if device is None:
                log.warning("Hub not found, retrying in %ds...", BLE_RECONNECT_INTERVAL)
                await asyncio.sleep(BLE_RECONNECT_INTERVAL)
                continue

            async with BleakClient(device) as client:
                ble_client = client
                ble_connected.set()
                log.info("Connected to Alpha Hub: %s", device.address)

                def notification_handler(sender, data):
                    parse_telemetry(data)

                await client.start_notify(UART_TX_UUID, notification_handler)

                # Stay connected until disconnect
                while client.is_connected:
                    await asyncio.sleep(1)

                ble_connected.clear()
                log.warning("BLE disconnected, reconnecting...")

        except Exception as e:
            ble_connected.clear()
            log.error("BLE error: %s, retrying in %ds", e, BLE_RECONNECT_INTERVAL)
            await asyncio.sleep(BLE_RECONNECT_INTERVAL)


async def send_ble_command(command: str):
    """Send a command string to Alpha Hub over BLE UART."""
    if ble_client and ble_client.is_connected:
        payload = (command + "\n").encode("utf-8")
        await ble_client.write_gatt_char(UART_RX_UUID, payload)
        log.info("BLE TX: %s", command)
    else:
        log.warning("BLE not connected, cannot send: %s", command)


# ---------------------------------------------------------------------------
# 3. 7-Second Telemetry Snap Loop
# ---------------------------------------------------------------------------
async def capture_camera():
    """Capture image via termux-camera-photo, return base64 string or None."""
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


async def post_to_cloud(payload: dict) -> bool:
    """POST telemetry to cloud endpoint. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as client:
            resp = await client.post(CLOUD_ENDPOINT, json=payload)
            if resp.status_code in (200, 201):
                return True
            log.warning("Cloud POST returned %d", resp.status_code)
            return False
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        log.warning("Cloud POST failed: %s", e)
        return False


def store_locally(timestamp, image_b64):
    """Buffer unsent telemetry in SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Enforce max buffer size
    c.execute("SELECT COUNT(*) FROM my_telemetry WHERE synced = 0")
    count = c.fetchone()[0]
    if count >= MAX_BUFFERED_IMAGES:
        c.execute("""
            DELETE FROM my_telemetry WHERE id IN (
                SELECT id FROM my_telemetry WHERE synced = 0
                ORDER BY id ASC LIMIT 1
            )
        """)

    c.execute("""
        INSERT INTO my_telemetry (timestamp, position_x, position_y,
                                  distance_mm, color, image_b64, synced)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (
        timestamp,
        alpha_coords["x"],
        alpha_coords["y"],
        alpha_sensors["distance_mm"],
        alpha_sensors["color"],
        image_b64,
    ))
    conn.commit()
    conn.close()


async def retry_pending():
    """Forward oldest pending image to cloud."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, timestamp, position_x, position_y, distance_mm, color, image_b64
        FROM my_telemetry WHERE synced = 0 ORDER BY id ASC LIMIT 1
    """)
    row = c.fetchone()
    conn.close()

    if row is None:
        return

    row_id, ts, px, py, dist, col, img = row
    payload = {
        "robot_id": "robot_alpha",
        "timestamp": ts,
        "coords": {"x": px, "y": py},
        "sensor_data": {"distance_mm": dist, "color": col},
        "image_b64": img,
    }

    if await post_to_cloud(payload):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE my_telemetry SET synced = 1 WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        log.info("Retried pending row %d successfully", row_id)


async def telemetry_snap_loop():
    """Main 7-second capture-and-forward loop."""
    retry_counter = 0

    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        timestamp = datetime.now(timezone.utc).isoformat()
        image_b64 = await capture_camera()

        payload = {
            "robot_id": "robot_alpha",
            "timestamp": timestamp,
            "coords": {"x": alpha_coords["x"], "y": alpha_coords["y"]},
            "sensor_data": {
                "distance_mm": alpha_sensors["distance_mm"],
                "color": alpha_sensors["color"],
            },
            "image_b64": image_b64,
        }

        success = await post_to_cloud(payload)

        if not success and image_b64:
            store_locally(timestamp, image_b64)

        # Retry pending every ~15 seconds (every 2nd cycle)
        retry_counter += 1
        if retry_counter % 2 == 0:
            await retry_pending()


# ---------------------------------------------------------------------------
# 4. Local Subordinate Communication Server (FastAPI)
# ---------------------------------------------------------------------------
app = FastAPI(title="CataSwarm Alpha Edge Brain")


@app.post("/subordinate/sync")
async def subordinate_sync(request: Request):
    """Receive telemetry from Phone Beta."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON")

    required_fields = ["robot_id", "distance_mm", "color_string", "position_x", "position_y"]
    missing = [f for f in required_fields if f not in body]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required fields: {missing}",
        )

    # Type validation
    try:
        robot_id = str(body["robot_id"])
        distance_mm = int(body["distance_mm"])
        color_string = str(body["color_string"]).lower()
        position_x = float(body["position_x"])
        position_y = float(body["position_y"])
        image_b64 = body.get("image_base64", None)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Field type error: {e}")

    # Update shared state
    beta_coords["x"] = position_x
    beta_coords["y"] = position_y
    beta_sensors["distance_mm"] = distance_mm
    beta_sensors["color"] = color_string

    # Store in database
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO subordinate_telemetry
            (timestamp, robot_id, position_x, position_y, distance_mm, color, image_b64)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, robot_id, position_x, position_y, distance_mm, color_string, image_b64))
    conn.commit()
    conn.close()

    log.info("Subordinate sync: Beta at (%.1f, %.1f) color=%s", position_x, position_y, color_string)
    return {"status": "ok"}


def run_fastapi():
    """Run FastAPI server in a dedicated thread."""
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")


# ---------------------------------------------------------------------------
# 5. Gemma Intercept & Dual-Actuation Engine
# ---------------------------------------------------------------------------
INTERCEPT_PROMPT_TEMPLATE = (
    "Subordinate Robot Beta discovered the target object at coordinates [{bx}, {by}]. "
    "Your current location matrix is [{ax}, {ay}]. "
    "Provide a strict JSON tracking object defining the relative navigation commands "
    "to intercept Beta's location and close your arm gripper. "
    "Return JSON with keys: commands (array of objects with type and value). "
    "Valid types: TURN (value=degrees), DRIVE (value=speed_mm_s), ARM_CLOSE, STOP."
)


async def query_gemma(prompt: str) -> dict | None:
    """Send prompt to local Gemma via llama.cpp API, return parsed JSON or None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(GEMMA_URL, json={
                "model": "gemma",
                "prompt": prompt,
                "stream": False,
            })
            if resp.status_code != 200:
                log.warning("Gemma returned %d", resp.status_code)
                return None

            result = resp.json()
            response_text = result.get("response", "")

            # Extract JSON from response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start == -1 or end == 0:
                log.warning("Gemma response has no JSON block")
                return None

            return json.loads(response_text[start:end])

    except (httpx.TimeoutException, httpx.ConnectError) as e:
        log.error("Gemma unavailable: %s", e)
        return None
    except json.JSONDecodeError as e:
        log.error("Gemma JSON parse error: %s", e)
        return None


async def send_override_to_beta(command: str):
    """Send override command to Phone Beta's /command-override endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                PHONE_BETA_OVERRIDE_URL,
                json={"command": command},
            )
            log.info("Override sent to Beta: %s", command)
    except Exception as e:
        log.warning("Failed to send override to Beta: %s", e)


async def execute_intercept_commands(commands: list):
    """Translate Gemma output into BLE commands for Alpha and overrides for Beta."""
    global intercept_in_progress

    # First, halt Beta's search loop
    await send_override_to_beta("OVERRIDE_STOP")

    for cmd in commands:
        cmd_type = cmd.get("type", "").upper()
        value = cmd.get("value", "")

        if cmd_type == "TURN":
            await send_ble_command(f"TURN:{int(value)}")
            # Wait proportional to turn angle
            await asyncio.sleep(abs(int(value)) / 90.0)

        elif cmd_type == "DRIVE":
            await send_ble_command(f"DRIVE:{int(value)}")
            await asyncio.sleep(2)  # Drive for 2 seconds

        elif cmd_type == "ARM_CLOSE":
            await send_ble_command("ARM_CLOSE")
            await asyncio.sleep(1)

        elif cmd_type == "STOP":
            await send_ble_command("STOP")

    # Signal Beta to prepare for cooperative push
    await send_override_to_beta("OVERRIDE_ARM_CLOSE")

    with intercept_lock:
        intercept_in_progress = False


async def intercept_engine_loop():
    """
    Every 7 seconds, check if Beta reported 'blue'.
    If so, invoke Gemma for navigation commands.
    """
    global intercept_in_progress

    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        # Skip if intercept already running
        with intercept_lock:
            if intercept_in_progress:
                continue

        # Check if Beta detected blue target
        if beta_sensors["color"] != "blue":
            continue

        log.info("BLUE TARGET DETECTED by Beta at (%.1f, %.1f)!",
                 beta_coords["x"], beta_coords["y"])

        with intercept_lock:
            intercept_in_progress = True

        # Build prompt
        prompt = INTERCEPT_PROMPT_TEMPLATE.format(
            bx=round(beta_coords["x"], 1),
            by=round(beta_coords["y"], 1),
            ax=round(alpha_coords["x"], 1),
            ay=round(alpha_coords["y"], 1),
        )

        # Query Gemma
        result = await query_gemma(prompt)

        if result is None or "commands" not in result:
            log.warning("Intercept engine: invalid Gemma response, aborting")
            with intercept_lock:
                intercept_in_progress = False
            continue

        commands = result["commands"]
        if not isinstance(commands, list):
            log.warning("Intercept engine: commands is not a list")
            with intercept_lock:
                intercept_in_progress = False
            continue

        # Execute command sequence with 30-second max timeout
        try:
            await asyncio.wait_for(
                execute_intercept_commands(commands),
                timeout=30,
            )
        except asyncio.TimeoutError:
            log.warning("Intercept sequence timed out after 30s")
            with intercept_lock:
                intercept_in_progress = False


# ---------------------------------------------------------------------------
# 6. Main Entry Point — Orchestrate all threads/tasks
# ---------------------------------------------------------------------------
async def main():
    """Launch all async tasks concurrently."""
    log.info("=== CataSwarm Alpha Edge Brain starting ===")
    init_database()

    # Start FastAPI in a background thread
    api_thread = threading.Thread(target=run_fastapi, daemon=True, name="FastAPI")
    api_thread.start()
    log.info("FastAPI server started on port 8080")

    # Run async tasks concurrently
    await asyncio.gather(
        ble_listener(),
        telemetry_snap_loop(),
        intercept_engine_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
