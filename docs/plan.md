# CataSwarm Implementation Plan

Reference document for the full build sequence. Each section maps to a specific layer and tool.

## Build Order

1. **Pybricks** — Hub firmware (MicroPython, standalone)
2. **Termux** — Edge handlers (Python, depends on hub BLE protocol)
3. **Cloud** — Web platform (Node/Python, depends on telemetry format)
4. **Kane** — Verification (depends on cloud dashboard being live)

## Section 1: Pybricks IDE

### Robot Alpha (Leader Hub)
- Port mapping: C(left), D(right), E(ultrasonic), B(color), F(arm)
- DriveBase: 56mm wheels, 124mm axle track
- Telemetry: "ALPHA_SENSORS:<dist>:<color>\n" every 200ms
- Commands: DRIVE, TURN, STOP, ARM_OPEN, ARM_CLOSE, PUSH

### Robot Beta (Follower Hub)
- Same port mapping as Alpha
- Default: drive forward 50mm/s with obstacle avoidance (<60mm → stop, reverse 30mm, turn 90°)
- Telemetry: "BETA_SENSORS:<dist>:<color>\n" every 200ms
- Overrides: OVERRIDE_MOVE, OVERRIDE_STOP, OVERRIDE_ARM_OPEN, OVERRIDE_ARM_CLOSE

## Section 2: Termux Edge Handlers

### Phone Alpha (Edge Brain)
- SQLite: leader_buffer.db (my_telemetry, subordinate_telemetry)
- BLE: bleak → Alpha hub, parse ALPHA_SENSORS packets
- Camera: termux-camera-photo every 7s → base64 → POST to cloud
- FastAPI: port 8080, POST /subordinate/sync
- LLM: Gemma via llama.cpp on port 11434, intercept logic on blue detection

### Phone Beta (Drone Broker)
- In-memory queue for resilience
- BLE: bleak → Beta hub, parse BETA_SENSORS packets
- Camera: termux-camera-photo every 7s → base64
- Dual POST: cloud + Phone Alpha:8080/subordinate/sync
- FastAPI: port 8080, POST /command-override → BLE forward

## Section 3: AWS Cloud Platform

- POST /api/telemetry (validates robot_alpha or robot_beta)
- Bedrock verification on blue color detection
- Canvas: green=Alpha, red=Beta trajectory lines
- Image slideshow with timeline slider
- Dev simulation harness (3 buttons)

## Section 4: Kane CLI Verification

- Browser target: localhost:3000/dashboard
- Assert panels, canvas paths, disconnect badge, target confirmation
- NDJSON output for agent auto-repair loop
