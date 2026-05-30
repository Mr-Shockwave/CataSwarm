# Cloud — Central Orchestration Platform

Full-stack web application for swarm telemetry ingestion, visualization, and AI verification.

## Components

### Server (Backend)
- `POST /api/telemetry` — Ingests telemetry from both phones
- Bedrock integration — Claude 3.5 Sonnet visual verification when Beta finds blue target
- SQLite/PostgreSQL storage sorted by timestamp

### Dashboard (Frontend)
- Telemetry Canvas Grid — Green (Alpha/Leader) and Red (Beta/Follower) trajectory paths
- Cinematic Street View — Base64 image decode + timeline scrubber
- Simulation Harness — Dev-only testing buttons

## Simulation Harness Buttons (dev mode only)

| Button ID                      | Action                                          |
|--------------------------------|-------------------------------------------------|
| btn-mock-explore-stream        | Generate 30s of mock trajectory data            |
| btn-mock-follower-disconnect   | Simulate Beta network drop (relay mode)         |
| btn-mock-target-found          | Simulate blue target discovery + confirmation   |

## Setup

```bash
cd cloud
npm install
npm run dev
```
