# Termux — Edge Computing Layer

Python scripts running on Android phones inside Termux, bridging BLE hubs to the cloud.

## Phone Alpha (Edge Brain)

High-spec device running:
- BLE connection to Robot Alpha hub
- SQLite telemetry buffer
- FastAPI server on port 8080 (receives Beta sync data)
- 7-second camera capture loop
- Local Gemma LLM via llama.cpp (port 11434) for intercept decisions

## Phone Beta (Drone Broker)

Low-spec device running:
- BLE connection to Robot Beta hub
- 7-second camera capture + dual-destination forwarding
- HTTP override listener on port 8080
- In-memory queue for network resilience

## Setup

```bash
pkg install python
pip install -r phone_alpha/requirements.txt
# or
pip install -r phone_beta/requirements.txt
```

## Network Topology

- Phone Alpha listens on `:8080` for subordinate sync
- Phone Beta listens on `:8080` for command overrides
- Both POST telemetry to cloud at `<kiro_server_ip>/api/telemetry`
