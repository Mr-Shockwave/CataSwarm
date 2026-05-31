import asyncio
import base64
import time
import requests
from fastapi import FastAPI, BackgroundTasks
import uvicorn
from bleak import BleakClient, BleakScanner

# CONFIGURATION INTERFACES
KIRO_SERVER_URL = "http://localhost:3000/api/telemetry"
LEADER_PHONE_URL = "http://192.168.1.10:8080/subordinate/sync" # Replace with Alpha's real IP if needed
UART_TX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

app = FastAPI()
ble_client = None

# 1. READ MARS SURFACE IMAGERY FOR TRANSMISSION
def get_mock_image_b64():
    try:
        with open("beta_mars_surface.jpg", "rb") as img_file:
            return base64.b64encode(img_file.read()).decode('utf-8')
    except FileNotFoundError:
        # Emergency low-overhead fallback base64 string if file isn't found
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

# 2. INBOUND MISSION CONTROL OVERRIDE LISTENER
@app.post("/command-override")
async def command_override(payload: dict, background_tasks: BackgroundTasks):
    instruction = payload.get("command", "OVERRIDE_STOP")
    print(f"[MISSION CONTROL COMMAND] Routing Directive: {instruction}")
    
    if ble_client and ble_client.is_connected:
        await ble_client.write_gatt_char(UART_TX_CHAR_UUID, f"{instruction}\n".encode())
    return {"status": "command_forwarded"}

# 3. 7-SECOND TELEMETRY BROADCAST TICK
async def telemetry_loop():
    x, y = 0.0, 0.0
    while True:
        await asyncio.sleep(7)
        img_b64 = get_mock_image_b64()
        
        payload = {
            "robot_id": "robot_beta",
            "timestamp": time.time(),
            "coords": {"x": x, "y": y},
            "sensor_data": {"distance_mm": 120, "color": "none"},
            "image_b64": img_b64,
            "status": "EXPLORING"
        }
        
        try:
            requests.post(KIRO_SERVER_URL, json=payload, timeout=2)
            requests.post(LEADER_PHONE_URL, json=payload, timeout=2)
            print("[SYNC] Telemetry successfully broadcasted from Base Station Broker.")
        except Exception as e:
            print(f"[NET ERROR] Telemetry buffering: {e}")
        
        x += 5.0 # Progress along tracking timeline

# 4. SECURE COREBLUETOOTH HANDSHAKE AND ENGINE STARTUP
async def main():
    global ble_client
    print("[INIT] Scanning the Martian atmosphere for Pybricks Hub names...")
    
    try:
        # Scans area for any device broadcasting a 'Pybricks' tag to bypass macOS UUID rotation
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: d.name and "Pybricks" in d.name,
            timeout=10.0
        )
        
        if not device:
            print("[X ERROR] No Pybricks Hub detected in range. Is the robot turned on?")
            print("[WARN] Falling back to pure software simulation mode...")
        else:
            print(f"[FOUND] Target locked onto: {device.name} [{device.address}]")
            print("[INIT] Handshaking over macOS CoreBluetooth...")
            ble_client = BleakClient(device)
            await ble_client.connect()
            print("[SUCCESS] Mac natively uplinked to Robot Beta chassis!")
            
    except Exception as e:
        print(f"[WARN] Bluetooth subsystem error, running in simulation: {e}")

    # Launch local server gateway and background logging operations concurrently
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    
    await asyncio.gather(
        server.serve(),
        telemetry_loop()
    )

# 5. ABSOLUTE ENTRY POINT RUNTIME ENFORCEMENT
if __name__ == "__main__":
    asyncio.run(main())
