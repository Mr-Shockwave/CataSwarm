"""
CataSwarm — Phone Alpha Leader Daemon (Wi-Fi relay edition)
===========================================================
Runs on the Android phone (Termux). All Bluetooth logic has been removed.
The Mac handles every physical BLE connection. This daemon is the high-level
decision node: it captures camera frames, queries the local Gemma 2B model,
and fires navigation commands to the Mac broker over Wi-Fi.

Architecture
------------
  Thread 1 (async): 7-second telemetry snap loop
                    → capture camera
                    → POST sensor frame to cloud
                    → buffer locally on failure
  Thread 2 (async): Gemma intercept engine
                    → watches for Beta's blue-target detection
                    → queries Gemma 2B at localhost:11434
                    → POSTs navigation commands to Mac broker
  Thread 3 (sync):  FastAPI server on port 8080
                    → /subordinate/sync  (receives Beta telemetry)

Environment variables
---------------------
  CLOUD_URL        Cloud Express server  (default: http://localhost:3000/api/telemetry)
  MAC_BROKER_IP    Mac broker IP         (default: 192.168.1.50)
  MAC_BROKER_PORT  Mac broker port       (default: 8888)
  PHONE_BETA_IP    Beta phone IP         (default: 192.168.1.101)
  GEMMA_URL        Ollama endpoint       (default: http://localhost:11434/api/generate)
"""

import asyncio
import base64
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLOUD_ENDPOINT    = os.environ.get("CLOUD_URL",        "http://localhost:3000/api/telemetry")
MAC_BROKER_IP     = os.environ.get("MAC_BROKER_IP",    "192.168.1.50")
MAC_BROKER_PORT   = int(os.environ.get("MAC_BROKER_PORT", "8888"))
MAC_ALPHA_CMD_URL = f"http://{MAC_BROKER_IP}:{MAC_BROKER_PORT}/api/alpha/command"
MAC_BETA_CMD_URL  = f"http://{MAC_BROKER_IP}:{MAC_BROKER_PORT}/api/beta/command"
PHONE_BETA_IP     = os.environ.get("PHONE_BETA_IP",    "192.168.1.101")
GEMMA_URL         = os.environ.get("GEMMA_URL",        "http://localhost:11434/api/generate")

DB_PATH           = "leader_buffer.db"
CAMERA_PATH       = "alpha_view.jpg"
TELEMETRY_INTERVAL = 7    # seconds
CLOUD_TIMEOUT      = 10   # seconds
MAC_CMD_TIMEOUT    = 5    # seconds
MAX_BUFFERED_ROWS  = 50

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LeaderDaemon] %(levelname)s: %(message)s",
)
log = logging.getLogger("leader_daemon")

# ---------------------------------------------------------------------------
# Shared state  (no BLE — updated only via /subordinate/sync from Beta)
# ---------------------------------------------------------------------------
alpha_coords  = {"x": 0.0, "y": 0.0}
alpha_sensors = {"distance_mm": -1, "color": "none"}
beta_coords   = {"x": 0.0, "y": 0.0}
beta_sensors  = {"distance_mm": -1, "color": "none"}

intercept_in_progress = False
intercept_lock        = threading.Lock()

# Local buffer for commands that failed to reach the Mac broker
pending_commands: deque[dict] = deque(maxlen=50)


# ---------------------------------------------------------------------------
# 1. Database
# ---------------------------------------------------------------------------
def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS my_telemetry (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            position_x   REAL    NOT NULL,
            position_y   REAL    NOT NULL,
            distance_mm  INTEGER,
            color        TEXT,
            image_b64    TEXT,
            synced       INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS subordinate_telemetry (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            robot_id     TEXT    NOT NULL,
            position_x   REAL    NOT NULL,
            position_y   REAL    NOT NULL,
            distance_mm  INTEGER,
            color        TEXT,
            image_b64    TEXT
        )
    """)
    conn.commit()
    conn.close()
    log.info("Database initialised: %s", DB_PATH)


def store_locally(timestamp: str, image_b64: str | None):
    """Buffer an unsent telemetry frame in SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Enforce max buffer size — drop oldest unsynced row
    c.execute("SELECT COUNT(*) FROM my_telemetry WHERE synced = 0")
    if c.fetchone()[0] >= MAX_BUFFERED_ROWS:
        c.execute("""
            DELETE FROM my_telemetry WHERE id IN (
                SELECT id FROM my_telemetry WHERE synced = 0
                ORDER BY id ASC LIMIT 1
            )
        """)

    c.execute("""
        INSERT INTO my_telemetry
            (timestamp, position_x, position_y, distance_mm, color, image_b64, synced)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (
        timestamp,
        alpha_coords["x"], alpha_coords["y"],
        alpha_sensors["distance_mm"], alpha_sensors["color"],
        image_b64,
    ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 2. HTTP helpers
# ---------------------------------------------------------------------------
async def post_to_cloud(payload: dict) -> bool:
    """POST telemetry to cloud. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as client:
            resp = await client.post(CLOUD_ENDPOINT, json=payload)
            return resp.status_code in (200, 201)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        log.warning("Cloud POST failed: %s", exc)
        return False


async def post_to_mac(url: str, payload: dict) -> bool:
    """
    POST a command to the Mac broker over Wi-Fi.
    Returns True on success. On failure, buffers the command locally.
    """
    try:
        async with httpx.AsyncClient(timeout=MAC_CMD_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201):
                log.info("Mac broker ← %s  %s", url.split("/")[-1], payload)
                return True
            log.warning("Mac broker returned %d for %s", resp.status_code, url)
            return False
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        log.warning("Mac broker unreachable (%s): %s — buffering command", url, exc)
        pending_commands.append({"url": url, "payload": payload})
        return False


async def retry_pending_commands():
    """Flush locally buffered commands that previously failed to reach the Mac."""
    retries = min(5, len(pending_commands))
    for _ in range(retries):
        if not pending_commands:
            break
        item = pending_commands[0]
        if await post_to_mac(item["url"], item["payload"]):
            pending_commands.popleft()
        else:
            break  # Still unreachable — stop retrying this cycle


async def retry_pending_telemetry():
    """Forward the oldest unsynced telemetry row to the cloud."""
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
        "robot_id":    "robot_alpha",
        "timestamp":   ts,
        "coords":      {"x": px, "y": py},
        "sensor_data": {"distance_mm": dist, "color": col},
        "image_b64":   img,
    }
    if await post_to_cloud(payload):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE my_telemetry SET synced = 1 WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        log.info("Retried pending telemetry row %d", row_id)


# ---------------------------------------------------------------------------
# 3. Camera capture
# ---------------------------------------------------------------------------
async def capture_camera() -> str | None:
    """Capture a JPEG via termux-camera-photo, return base64 or None."""
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
            return base64.b64encode(f.read()).decode("ascii")
    except Exception as exc:
        log.warning("Camera error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 4. 7-second telemetry snap loop
# ---------------------------------------------------------------------------
async def telemetry_snap_loop():
    """Capture camera + sensors every 7 s and POST to cloud."""
    retry_counter = 0

    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        timestamp = datetime.now(timezone.utc).isoformat()
        image_b64 = await capture_camera()

        payload = {
            "robot_id":    "robot_alpha",
            "timestamp":   timestamp,
            "coords":      {"x": alpha_coords["x"], "y": alpha_coords["y"]},
            "sensor_data": {
                "distance_mm": alpha_sensors["distance_mm"],
                "color":       alpha_sensors["color"],
            },
            "image_b64": image_b64,
        }

        success = await post_to_cloud(payload)
        if not success and image_b64:
            store_locally(timestamp, image_b64)

        # Retry pending every ~14 s (every 2nd cycle)
        retry_counter += 1
        if retry_counter % 2 == 0:
            await retry_pending_telemetry()
            await retry_pending_commands()


# ---------------------------------------------------------------------------
# 5. Gemma intercept engine
# ---------------------------------------------------------------------------
INTERCEPT_PROMPT = (
    "Subordinate Robot Beta discovered the target object at coordinates [{bx}, {by}]. "
    "Your current location matrix is [{ax}, {ay}]. "
    "Provide a strict JSON tracking object defining the relative navigation commands "
    "to intercept Beta's location and close your arm gripper. "
    "Return JSON with keys: commands (array of objects with type and value). "
    "Valid types: TURN (value=degrees), DRIVE (value=speed_mm_s), ARM_CLOSE, STOP."
)


async def query_gemma(prompt: str) -> dict | None:
    """Query local Gemma 2B via Ollama. Returns parsed JSON or None."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(GEMMA_URL, json={
                "model":  "gemma",
                "prompt": prompt,
                "stream": False,
            })
            if resp.status_code != 200:
                log.warning("Gemma returned HTTP %d", resp.status_code)
                return None

            text = resp.json().get("response", "")
            start = text.find("{")
            end   = text.rfind("}") + 1
            if start == -1 or end == 0:
                log.warning("Gemma response contains no JSON block")
                return None
            return json.loads(text[start:end])

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        log.error("Gemma unavailable: %s", exc)
        return None
    except json.JSONDecodeError as exc:
        log.error("Gemma JSON parse error: %s", exc)
        return None


async def send_alpha_command(action: str, value: int | None = None):
    """
    Send a navigation command to Robot Alpha via the Mac broker over Wi-Fi.
    No BLE — the Mac handles the physical write.
    """
    payload: dict = {"action": action}
    if value is not None:
        payload["value"] = value
    await post_to_mac(MAC_ALPHA_CMD_URL, payload)


async def send_beta_override(command: str):
    """Send an override command to Robot Beta via the Mac broker."""
    await post_to_mac(MAC_BETA_CMD_URL, {"action": command})


async def execute_intercept_commands(commands: list):
    """
    Translate Gemma's command list into Wi-Fi POSTs to the Mac broker.
    The Mac broker writes each command to the physical Alpha hub over BLE.
    """
    global intercept_in_progress

    # Report intercept start to cloud dashboard
    await post_to_cloud({
        "device_id":      "robot_alpha",
        "status":         "INITIALIZING",
        "target_command": "STARTUP",
        "timestamp":      time.time(),
    })

    # Halt Beta's search loop first
    await send_beta_override("OVERRIDE_STOP")

    for cmd in commands:
        cmd_type = cmd.get("type", "").upper()
        value    = cmd.get("value")

        if cmd_type == "TURN":
            await send_alpha_command("TURN", int(value))
            await asyncio.sleep(abs(int(value)) / 90.0)

        elif cmd_type == "DRIVE":
            await send_alpha_command("DRIVE", int(value))
            await asyncio.sleep(2)

        elif cmd_type == "ARM_CLOSE":
            await send_alpha_command("ARM_CLOSE")
            await asyncio.sleep(1)

        elif cmd_type == "STOP":
            await send_alpha_command("STOP")

    # Signal Beta to close its arm for cooperative push
    await send_beta_override("OVERRIDE_ARM_CLOSE")

    # Report completion to cloud dashboard
    await post_to_cloud({
        "device_id":      "robot_alpha",
        "status":         "STABLE_HOLD",
        "target_command": "HOLD",
        "timestamp":      time.time(),
    })

    with intercept_lock:
        intercept_in_progress = False


async def intercept_engine_loop():
    """
    Every 7 s, check if Beta reported a blue target.
    If so, query Gemma and dispatch navigation commands to the Mac broker.
    """
    global intercept_in_progress

    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL)

        with intercept_lock:
            if intercept_in_progress:
                continue

        if beta_sensors["color"] != "blue":
            continue

        log.info(
            "BLUE TARGET DETECTED by Beta at (%.1f, %.1f) — querying Gemma",
            beta_coords["x"], beta_coords["y"],
        )

        with intercept_lock:
            intercept_in_progress = True

        prompt = INTERCEPT_PROMPT.format(
            bx=round(beta_coords["x"], 1),
            by=round(beta_coords["y"], 1),
            ax=round(alpha_coords["x"], 1),
            ay=round(alpha_coords["y"], 1),
        )

        result = await query_gemma(prompt)

        if result is None or not isinstance(result.get("commands"), list):
            log.warning("Intercept engine: invalid Gemma response — aborting")
            with intercept_lock:
                intercept_in_progress = False
            continue

        try:
            await asyncio.wait_for(
                execute_intercept_commands(result["commands"]),
                timeout=30,
            )
        except asyncio.TimeoutError:
            log.warning("Intercept sequence timed out after 30 s")
            with intercept_lock:
                intercept_in_progress = False


# ---------------------------------------------------------------------------
# 6. FastAPI — subordinate sync endpoint
# ---------------------------------------------------------------------------
fastapi_app = FastAPI(title="CataSwarm Leader Daemon")


@fastapi_app.post("/subordinate/sync")
async def subordinate_sync(request: Request):
    """Receive telemetry from Phone Beta (or Mac broker forwarding Beta data)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON")

    required = ["robot_id", "distance_mm", "color_string", "position_x", "position_y"]
    missing  = [f for f in required if f not in body]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {missing}")

    try:
        robot_id    = str(body["robot_id"])
        distance_mm = int(body["distance_mm"])
        color       = str(body["color_string"]).lower()
        pos_x       = float(body["position_x"])
        pos_y       = float(body["position_y"])
        image_b64   = body.get("image_base64")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Field type error: {exc}")

    beta_coords["x"]            = pos_x
    beta_coords["y"]            = pos_y
    beta_sensors["distance_mm"] = distance_mm
    beta_sensors["color"]       = color

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO subordinate_telemetry
            (timestamp, robot_id, position_x, position_y, distance_mm, color, image_b64)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, robot_id, pos_x, pos_y, distance_mm, color, image_b64))
    conn.commit()
    conn.close()

    log.info("Subordinate sync: %s at (%.1f, %.1f) color=%s", robot_id, pos_x, pos_y, color)
    return {"status": "ok"}


def run_fastapi():
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8080, log_level="warning")


# ---------------------------------------------------------------------------
# 7. Main entry point
# ---------------------------------------------------------------------------
async def main():
    log.info("=== CataSwarm Leader Daemon starting (Wi-Fi relay mode) ===")
    log.info("Mac broker     : http://%s:%d", MAC_BROKER_IP, MAC_BROKER_PORT)
    log.info("Cloud endpoint : %s", CLOUD_ENDPOINT)
    log.info("Gemma endpoint : %s", GEMMA_URL)
    log.info("NOTE: No Bluetooth on this device — all BLE handled by Mac broker")

    init_database()

    api_thread = threading.Thread(target=run_fastapi, daemon=True, name="FastAPI")
    api_thread.start()
    log.info("FastAPI subordinate-sync server started on port 8080")

    await asyncio.gather(
        telemetry_snap_loop(),
        intercept_engine_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
