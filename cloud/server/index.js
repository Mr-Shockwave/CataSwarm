/**
 * CataSwarm Cloud Platform — Central Orchestration Server
 * Requirements: 14-18 from .kiro/specs/cataswarm-system/requirements.md
 *
 * Stack: Express + sql.js (no native build) + WebSocket + AWS Bedrock
 */

const express = require("express");
const cors = require("cors");
const path = require("path");
const fs = require("fs");
const { WebSocketServer } = require("ws");
const http = require("http");
const initSqlJs = require("sql.js");
const { verifyWithBedrock } = require("./bedrock");
const { MockTelemetryGenerator } = require("./mock-telemetry");

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const PORT = process.env.PORT || 3000;
const NODE_ENV = process.env.NODE_ENV || "production";
const dataDir = path.join(__dirname, "..", "data");
const DB_FILE = path.join(dataDir, "telemetry.db");

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------
let globalSwarmState = "EXPLORING";
let db = null;
let mockGen = null;

// ---------------------------------------------------------------------------
// Express app
// ---------------------------------------------------------------------------
const app = express();
app.use(cors());
app.use(express.json({ limit: "10mb" }));
app.use(express.static(path.join(__dirname, "..", "public")));

// ---------------------------------------------------------------------------
// Database initialization (async — sql.js)
// ---------------------------------------------------------------------------
async function initDatabase() {
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

  const SQL = await initSqlJs();

  // Load existing DB file if present, otherwise create fresh
  if (fs.existsSync(DB_FILE)) {
    const fileBuffer = fs.readFileSync(DB_FILE);
    db = new SQL.Database(fileBuffer);
  } else {
    db = new SQL.Database();
  }

  db.run(`
    CREATE TABLE IF NOT EXISTS telemetry (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      robot_id TEXT NOT NULL,
      timestamp TEXT NOT NULL,
      position_x REAL,
      position_y REAL,
      distance_mm INTEGER,
      color_string TEXT,
      image_b64 TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);
  db.run(`CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(timestamp)`);
  db.run(`CREATE INDEX IF NOT EXISTS idx_telemetry_robot ON telemetry(robot_id)`);

  // Device state table — one row per device, upserted on each broker ping
  db.run(`
    CREATE TABLE IF NOT EXISTS device_state (
      device_id TEXT PRIMARY KEY,
      status TEXT NOT NULL DEFAULT 'UNKNOWN',
      target_command TEXT NOT NULL DEFAULT 'HOLD',
      timestamp REAL NOT NULL,
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  console.log("✅ Database initialized (sql.js)");
}

// Persist DB to file periodically
function saveDatabase() {
  if (!db) return;
  try {
    const data = db.export();
    const buffer = Buffer.from(data);
    fs.writeFileSync(DB_FILE, buffer);
  } catch (err) {
    console.error("DB save error:", err.message);
  }
}

// Auto-save every 30 seconds
setInterval(saveDatabase, 30000);


// ---------------------------------------------------------------------------
// REST API: POST /api/telemetry
// Handles two payload shapes:
//   A) Broker/edge-AI shape: { device_id, status, target_command, timestamp }
//   B) Robot sensor shape:   { robot_id, coords, sensor_data, image_b64, timestamp }
// ---------------------------------------------------------------------------
app.post("/api/telemetry", async (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const body = req.body;

  // --- Shape A: broker device-state ping ---
  // Identified by presence of `device_id` + `status` + `target_command`
  if (body.device_id !== undefined && body.status !== undefined && body.target_command !== undefined) {
    const { device_id, status, target_command, timestamp } = body;

    if (!device_id || typeof device_id !== "string") {
      return res.status(400).json({ error: "Invalid or missing device_id" });
    }
    if (!status || !target_command) {
      return res.status(400).json({ error: "Missing required fields: status, target_command" });
    }

    const ts = (typeof timestamp === "number") ? timestamp : Date.now() / 1000;

    try {
      db.run(
        `INSERT INTO device_state (device_id, status, target_command, timestamp, updated_at)
         VALUES (?, ?, ?, ?, datetime('now'))
         ON CONFLICT(device_id) DO UPDATE SET
           status = excluded.status,
           target_command = excluded.target_command,
           timestamp = excluded.timestamp,
           updated_at = excluded.updated_at`,
        [device_id, status, target_command, ts]
      );
    } catch (err) {
      console.error("DB upsert error (device_state):", err.message);
      return res.status(500).json({ error: "Database write failed" });
    }

    // Broadcast device state update over WebSocket
    const stateMsg = JSON.stringify({
      type: "device_state",
      data: { device_id, status, target_command, timestamp: ts },
    });
    for (const ws of wsClients) {
      if (ws.readyState === 1) ws.send(stateMsg);
    }

    console.log(`[DeviceState] ${device_id} → status=${status}, cmd=${target_command}`);
    return res.status(200).json({ status: "ok" });
  }

  // --- Shape B: robot sensor telemetry ---
  const { robot_id, timestamp, coords, sensor_data, image_b64 } = body;

  // Validate robot_id
  if (!robot_id || !["robot_alpha", "robot_beta"].includes(robot_id)) {
    return res.status(400).json({ error: "Invalid or missing robot_id" });
  }

  // Extract fields with fallbacks
  const posX = coords?.x ?? 0;
  const posY = coords?.y ?? 0;
  const distanceMm = sensor_data?.distance_mm;
  const colorString = sensor_data?.color;

  if (distanceMm === undefined || colorString === undefined) {
    return res.status(400).json({
      error: "Missing required sensor_data fields (distance_mm, color)",
    });
  }

  const ts = timestamp || new Date().toISOString();

  // Store in database
  try {
    db.run(
      `INSERT INTO telemetry (robot_id, timestamp, position_x, position_y, distance_mm, color_string, image_b64)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [robot_id, ts, posX, posY, distanceMm, colorString, image_b64 || null]
    );
  } catch (err) {
    console.error("DB insert error:", err.message);
    return res.status(500).json({ error: "Database write failed" });
  }

  // Broadcast to WebSocket clients
  broadcastTelemetry({
    robot_id,
    timestamp: ts,
    coords: { x: posX, y: posY },
    sensor_data: { distance_mm: distanceMm, color: colorString },
    has_image: !!image_b64,
  });

  // Notify mock generator that real data arrived
  if (mockGen) mockGen.notifyRealTelemetry();

  // Broadcast image to Street View panel if present
  if (image_b64) {
    const imgMsg = JSON.stringify({
      type: "image",
      data: { robot_id, timestamp: ts, image_b64 },
    });
    for (const ws of wsClients) {
      if (ws.readyState === 1) ws.send(imgMsg);
    }
  }

  // Bedrock verification: Beta + blue + image
  if (robot_id === "robot_beta" && colorString === "blue" && image_b64) {
    handleBlueDetection(image_b64, ts, posX, posY);
  }

  return res.status(201).json({ status: "stored" });
});

// ---------------------------------------------------------------------------
// REST API: GET /api/state
// ---------------------------------------------------------------------------
app.get("/api/state", (_req, res) => {
  res.json({ state: globalSwarmState });
});

// ---------------------------------------------------------------------------
// REST API: GET /api/health
// ---------------------------------------------------------------------------
app.get("/api/health", (_req, res) => {
  res.json({ status: "ok", db: db ? "connected" : "initializing" });
});

// ---------------------------------------------------------------------------
// REST API: GET /api/command/:device_id
// Lightweight polling endpoint for Pybricks hardware to fetch its assignment.
// Returns { command: "HOLD" } as fail-safe when no record exists.
// ---------------------------------------------------------------------------
app.get("/api/command/:device_id", (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const { device_id } = req.params;
  if (!device_id) return res.status(400).json({ error: "Missing device_id" });

  try {
    const stmt = db.prepare(
      "SELECT target_command FROM device_state WHERE device_id = ?"
    );
    stmt.bind([device_id]);
    const row = stmt.step() ? stmt.getAsObject() : null;
    stmt.free();

    const command = row?.target_command || "HOLD";
    return res.json({ command });
  } catch (err) {
    console.error("DB query error (device_state):", err.message);
    return res.status(500).json({ error: "Database query failed" });
  }
});

// ---------------------------------------------------------------------------
// REST API: GET /api/device-state/:device_id
// Returns the full latest state record for a device (used by dashboard on load).
// ---------------------------------------------------------------------------
app.get("/api/device-state/:device_id", (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const { device_id } = req.params;

  try {
    const stmt = db.prepare(
      "SELECT device_id, status, target_command, timestamp FROM device_state WHERE device_id = ?"
    );
    stmt.bind([device_id]);
    const row = stmt.step() ? stmt.getAsObject() : null;
    stmt.free();

    if (!row) return res.json(null);
    return res.json(row);
  } catch (err) {
    console.error("DB query error (device_state):", err.message);
    return res.status(500).json({ error: "Database query failed" });
  }
});

// ---------------------------------------------------------------------------
// REST API: POST /api/alpha/command  (Mac-in-the-Middle relay)
// Proxies a motor-action command from the dashboard or phone to the Mac broker.
// MAC_BROKER_URL env var must point to the Mac broker (default: http://localhost:8888)
// ---------------------------------------------------------------------------
const MAC_BROKER_URL = process.env.MAC_BROKER_URL || "http://localhost:8888";

app.post("/api/alpha/command", async (req, res) => {
  const { action, value, command } = req.body || {};

  // Accept both { action, value } and legacy { command } shapes
  if (!action && !command) {
    return res.status(400).json({ error: "Missing 'action' or 'command' field" });
  }

  try {
    const http = require("http");
    const body = JSON.stringify(req.body);
    const url  = new URL("/api/alpha/command", MAC_BROKER_URL);

    const proxyReq = http.request(
      { hostname: url.hostname, port: url.port || 8888, path: url.pathname, method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } },
      (proxyRes) => {
        let data = "";
        proxyRes.on("data", (chunk) => { data += chunk; });
        proxyRes.on("end", () => {
          try { res.status(proxyRes.statusCode).json(JSON.parse(data)); }
          catch { res.status(proxyRes.statusCode).send(data); }
        });
      }
    );
    proxyReq.on("error", (err) => {
      console.warn("[MacBroker] Proxy error:", err.message);
      res.status(503).json({ error: "Mac broker unreachable", detail: err.message });
    });
    proxyReq.write(body);
    proxyReq.end();
  } catch (err) {
    console.error("[MacBroker] Unexpected proxy error:", err.message);
    res.status(500).json({ error: "Internal proxy error" });
  }
});

// ---------------------------------------------------------------------------
// REST API: GET /api/telemetry/history
// ---------------------------------------------------------------------------
app.get("/api/telemetry/history", (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const limit = Math.min(parseInt(req.query.limit) || 500, 1000);
  const robotId = req.query.robot_id;

  let rows;
  if (robotId) {
    const stmt = db.prepare(
      "SELECT * FROM telemetry WHERE robot_id = ? ORDER BY timestamp DESC LIMIT ?"
    );
    stmt.bind([robotId, limit]);
    rows = [];
    while (stmt.step()) rows.push(stmt.getAsObject());
    stmt.free();
  } else {
    const stmt = db.prepare(
      "SELECT * FROM telemetry ORDER BY timestamp DESC LIMIT ?"
    );
    stmt.bind([limit]);
    rows = [];
    while (stmt.step()) rows.push(stmt.getAsObject());
    stmt.free();
  }

  res.json(rows.reverse());
});

// ---------------------------------------------------------------------------
// REST API: GET /api/images
// ---------------------------------------------------------------------------
app.get("/api/images", (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const limit = Math.min(parseInt(req.query.limit) || 100, 500);
  const stmt = db.prepare(
    "SELECT id, robot_id, timestamp, image_b64 FROM telemetry WHERE image_b64 IS NOT NULL ORDER BY timestamp DESC LIMIT ?"
  );
  stmt.bind([limit]);
  const rows = [];
  while (stmt.step()) rows.push(stmt.getAsObject());
  stmt.free();

  res.json(rows.reverse());
});


// ---------------------------------------------------------------------------
// Bedrock Visual Verification
// ---------------------------------------------------------------------------
async function handleBlueDetection(imageB64, _timestamp, x, y) {
  console.log(`[Bedrock] Blue detection from Beta at (${x}, ${y}), verifying...`);

  try {
    const confirmed = await verifyWithBedrock(imageB64);

    if (confirmed) {
      globalSwarmState = "COOPERATIVE_ENGAGED: TARGET_CONFIRMED";
      console.log("[Bedrock] TARGET CONFIRMED — transitioning state");
      broadcastStateChange(globalSwarmState);
    } else {
      console.log("[Bedrock] Target NOT confirmed, maintaining exploration state");
    }
  } catch (err) {
    console.error("[Bedrock] Verification failed:", err.message);
  }
}

// ---------------------------------------------------------------------------
// WebSocket server
// ---------------------------------------------------------------------------
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: "/ws" });

const wsClients = new Set();

wss.on("connection", (ws) => {
  wsClients.add(ws);
  // Send current state on connect
  ws.send(JSON.stringify({ type: "state", state: globalSwarmState }));
  ws.on("close", () => wsClients.delete(ws));
});

function broadcastTelemetry(data) {
  const msg = JSON.stringify({ type: "telemetry", data });
  for (const ws of wsClients) {
    if (ws.readyState === 1) ws.send(msg);
  }
}

function broadcastStateChange(state) {
  const msg = JSON.stringify({ type: "state", state });
  for (const ws of wsClients) {
    if (ws.readyState === 1) ws.send(msg);
  }
}

// ---------------------------------------------------------------------------
// Dashboard route (SPA fallback)
// ---------------------------------------------------------------------------
app.get("/dashboard", (_req, res) => {
  res.sendFile(path.join(__dirname, "..", "public", "index.html"));
});

// API: pause/resume mock generator
app.post("/api/mock/pause", (_req, res) => {
  if (mockGen) {
    mockGen.stop();
    // Also stop the monitoring so it doesn't auto-restart
    if (mockGen.fallbackTimer) {
      clearInterval(mockGen.fallbackTimer);
      mockGen.fallbackTimer = null;
    }
    globalSwarmState = "EXPLORING";
    broadcastStateChange(globalSwarmState);
    res.json({ status: "paused" });
  } else {
    res.json({ status: "no_mock" });
  }
});

app.post("/api/mock/resume", (_req, res) => {
  if (mockGen) {
    if (!mockGen.active) {
      mockGen.startMonitoring();
      mockGen.start();
    }
    res.json({ status: "resumed" });
  } else {
    res.json({ status: "no_mock" });
  }
});

// Serve local images folder
app.use("/images", express.static(path.join(__dirname, "..", "images")));

// API: list available local images
app.get("/api/local-images/:robot", (req, res) => {
  const robot = req.params.robot; // "alpha" or "beta"
  if (!["alpha", "beta"].includes(robot)) {
    return res.status(400).json({ error: "Invalid robot. Use 'alpha' or 'beta'" });
  }
  const imgDir = path.join(__dirname, "..", "images", robot);
  if (!fs.existsSync(imgDir)) {
    return res.json([]);
  }
  const files = fs.readdirSync(imgDir)
    .filter(f => /\.(jpg|jpeg|png|bmp|gif|webp)$/i.test(f))
    .sort();
  const urls = files.map(f => `/images/${robot}/${f}`);
  res.json(urls);
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------
async function start() {
  await initDatabase();

  // Initialize mock telemetry fallback
  mockGen = new MockTelemetryGenerator({
    onTelemetry: (payload) => {
      // Store in DB
      if (db) {
        const { robot_id, timestamp, coords, sensor_data, image_b64 } = payload;
        db.run(
          `INSERT INTO telemetry (robot_id, timestamp, position_x, position_y, distance_mm, color_string, image_b64)
           VALUES (?, ?, ?, ?, ?, ?, ?)`,
          [robot_id, timestamp, coords.x, coords.y, sensor_data.distance_mm, sensor_data.color, image_b64 || null]
        );
      }
      // Broadcast via WebSocket
      broadcastTelemetry({
        robot_id: payload.robot_id,
        timestamp: payload.timestamp,
        coords: payload.coords,
        sensor_data: payload.sensor_data,
        has_image: !!payload.image_b64,
      });
      // If image present, broadcast it for the Street View panel
      if (payload.image_b64) {
        const imgMsg = JSON.stringify({
          type: "image",
          data: {
            robot_id: payload.robot_id,
            timestamp: payload.timestamp,
            image_b64: payload.image_b64,
          },
        });
        for (const ws of wsClients) {
          if (ws.readyState === 1) ws.send(imgMsg);
        }
      }
    },
    onBlueDetection: (x, y) => {
      // Simulate target confirmation (no Bedrock in mock mode)
      globalSwarmState = "COOPERATIVE_ENGAGED: TARGET_CONFIRMED";
      console.log("[MockGen] Simulated TARGET_CONFIRMED at (" + x.toFixed(1) + ", " + y.toFixed(1) + ")");
      broadcastStateChange(globalSwarmState);
    },
  });
  mockGen.startMonitoring();

  server.listen(PORT, () => {
    console.log(`CataSwarm Cloud Platform running on http://localhost:${PORT}`);
    console.log(`Dashboard: http://localhost:${PORT}/dashboard`);
    console.log(`Environment: ${NODE_ENV}`);
    console.log(`Mock fallback: active (triggers after 10s of no real telemetry)`);
  });
}

// Graceful shutdown — save DB on exit
process.on("SIGINT", () => {
  console.log("\nShutting down, saving database...");
  saveDatabase();
  process.exit(0);
});

process.on("SIGTERM", () => {
  saveDatabase();
  process.exit(0);
});

start().catch((err) => {
  console.error("Failed to start server:", err);
  process.exit(1);
});
