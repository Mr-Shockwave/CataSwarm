# CataSwarm

Asymmetric Leader-Follower Robot Swarm Coordination System.

## Architecture Overview

CataSwarm is a multi-layered robotics coordination platform that orchestrates two LEGO Spike Prime robots through edge computing nodes (Android phones) and a central cloud dashboard.

```
┌─────────────────────────────────────────────────────────────┐
│                    AWS Cloud Platform                         │
│   POST /api/telemetry  │  Dashboard  │  Bedrock Verification │
└────────────────┬────────────────────────────┬────────────────┘
                 │ HTTP                        │ HTTP
     ┌───────────┴───────────┐    ┌───────────┴───────────┐
     │   Phone Alpha (Edge)  │    │   Phone Beta (Relay)   │
     │   FastAPI + Gemma LLM │    │   Lightweight Broker   │
     │   Port 8080           │    │   Port 8080            │
     └───────────┬───────────┘    └───────────┬───────────┘
                 │ BLE                         │ BLE
     ┌───────────┴───────────┐    ┌───────────┴───────────┐
     │   Robot Alpha (Leader) │    │   Robot Beta (Follower)│
     │   Spike Prime Hub     │    │   Spike Prime Hub      │
     │   Ports: C,D,E,B,F   │    │   Ports: C,D,E,B,F    │
     └───────────────────────┘    └───────────────────────┘
```

## Project Structure

```
CataSwarm/
├── pybricks/                 # MicroPython scripts for LEGO Spike Prime Hubs
│   ├── alpha_hub.py          # Robot Alpha - Leader hub firmware
│   └── beta_hub.py           # Robot Beta - Follower hub firmware
├── termux/                   # Android Termux Python edge handlers
│   ├── phone_alpha/          # Edge Brain - runs local LLM
│   │   ├── daemon.py         # Async coordination daemon
│   │   └── requirements.txt
│   └── phone_beta/           # Drone Broker - lightweight relay
│       ├── broker.py         # Single-threaded passthrough
│       └── requirements.txt
├── cloud/                    # AWS central orchestration web platform
│   ├── server/               # Backend API + Bedrock integration
│   ├── dashboard/            # Frontend visualization
│   └── package.json
├── verification/             # Kane CLI end-to-end test specs
│   └── asymmetric_swarm_verification.md
├── docs/                     # Additional documentation
│   └── plan.md               # Full implementation plan reference
└── README.md
```

## Layer Breakdown

### 1. Pybricks (MicroPython) — Hub Firmware
- **Alpha**: Telemetry broadcast + command execution (drive, turn, arm, push)
- **Beta**: Local obstacle avoidance + remote override acceptance

### 2. Termux (Python) — Edge Computing
- **Phone Alpha**: BLE bridge, camera capture, SQLite buffer, FastAPI server, Gemma LLM decision engine
- **Phone Beta**: BLE bridge, camera capture, dual-destination telemetry forwarding, override listener

### 3. Cloud (Web Platform) — Central Orchestration
- REST ingestion API with Bedrock visual verification
- Real-time swarm canvas with trajectory visualization
- Cinematic image slideshow with timeline scrubber
- Development simulation harness

### 4. Kane CLI — Visual Verification
- Plain-English browser automation specs
- Assertion-driven UI validation
- NDJSON error stream for agent auto-repair

## Hardware Port Mapping (Both Hubs)

| Port | Device                  |
|------|-------------------------|
| B    | Color Sensor            |
| C    | Left Drive Motor        |
| D    | Right Drive Motor       |
| E    | Ultrasonic Distance     |
| F    | Arm/Gripper Motor       |

## Communication Protocol

- **Hub ↔ Phone**: BLE UART (Pybricks protocol)
- **Phone ↔ Phone**: HTTP REST (local network)
- **Phone ↔ Cloud**: HTTP POST (internet)
- **Telemetry format**: `<ROBOT>_SENSORS:<distance_mm>:<color_string>\n`

## Quick Start

Each layer is independently deployable. See individual README files in each subdirectory for setup instructions.
