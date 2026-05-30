# Pybricks — Hub Firmware

MicroPython scripts targeting LEGO Spike Prime Hubs via the Pybricks runtime.

## Files

- `alpha_hub.py` — Robot Alpha (Leader): telemetry stream + command executor
- `beta_hub.py` — Robot Beta (Follower): local safety loop + remote override engine

## Deployment

1. Open [Pybricks Code](https://code.pybricks.com/)
2. Connect to the Spike Prime Hub via USB or BLE
3. Upload the corresponding `.py` file
4. Run directly on-hub

## Hardware Requirements

Both hubs share identical port assignments:
- Port B: Color Sensor
- Port C: Left Motor
- Port D: Right Motor
- Port E: Ultrasonic Distance Sensor
- Port F: Arm/Gripper Motor

DriveBase config: wheel_diameter=56mm, axle_track=124mm
