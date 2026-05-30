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
// ---------------------------------------------------------------------------
app.post("/api/telemetry", async (req, res) => {
  if (!db) return res.status(503).json({ error: "Database not ready" });

  const { robot_id, timestamp, coords, sensor_data, image_b64 } = req.body;

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
      console.log("[MockGen] Simulated TARGET_CONFIRMED at (%.1f, %.1f)", x, y);
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
