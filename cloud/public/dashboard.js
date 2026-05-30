/**
 * CataSwarm Dashboard — Client-side JavaScript
 * Handles WebSocket telemetry, canvas rendering, image slideshow, and simulation harness.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const MAX_PATH_POINTS = 500;

const state = {
  alphaPaths: [],   // [{x, y, timestamp}]
  betaPaths: [],    // [{x, y, timestamp}]
  images: [],       // [{robot_id, timestamp, image_b64}]
  sliderAtEnd: true,
  betaOnline: true,
  lastAlphaTs: 0,
  lastBetaTs: 0,
  simulationTimers: {},
};

// ---------------------------------------------------------------------------
// DOM References
// ---------------------------------------------------------------------------
const canvas = document.getElementById("telemetry-canvas");
const ctx = canvas.getContext("2d");
const globalStateEl = document.getElementById("global-swarm-state");
const imageDisplay = document.getElementById("image-display");
const imageSourceLabel = document.getElementById("image-source-label");
const imageTimestampLabel = document.getElementById("image-timestamp-label");
const playbackSlider = document.getElementById("playback-slider");
const followerBadge = document.getElementById("follower-badge");
const simulationHarness = document.getElementById("simulation-harness");

// ---------------------------------------------------------------------------
// WebSocket Connection
// ---------------------------------------------------------------------------
let ws;

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === "telemetry") {
      handleTelemetry(msg.data);
    } else if (msg.type === "state") {
      updateGlobalState(msg.state);
    }
  };

  ws.onclose = () => {
    setTimeout(connectWebSocket, 2000);
  };
}

connectWebSocket();

// ---------------------------------------------------------------------------
// Telemetry Handler
// ---------------------------------------------------------------------------
function handleTelemetry(data) {
  const { robot_id, coords, sensor_data, has_image, timestamp } = data;
  const point = { x: coords.x, y: coords.y, timestamp };

  if (robot_id === "robot_alpha") {
    state.alphaPaths.push(point);
    if (state.alphaPaths.length > MAX_PATH_POINTS) state.alphaPaths.shift();
    state.lastAlphaTs = Date.now();
  } else if (robot_id === "robot_beta") {
    state.betaPaths.push(point);
    if (state.betaPaths.length > MAX_PATH_POINTS) state.betaPaths.shift();
    state.lastBetaTs = Date.now();
    updateBetaStatus(true);
  }

  renderCanvas();
}

// ---------------------------------------------------------------------------
// Canvas Rendering
// ---------------------------------------------------------------------------
function renderCanvas() {
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Draw grid
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 0.5;
  for (let x = 0; x < w; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let y = 0; y < h; y += 40) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // Determine if streams are stale (>5s)
  const now = Date.now();
  const alphaStale = now - state.lastAlphaTs > 5000;
  const betaStale = now - state.lastBetaTs > 5000;

  // Draw Alpha path (green)
  if (state.alphaPaths.length > 1) {
    drawPath(state.alphaPaths, alphaStale ? "#21262d" : "#3fb950", w, h);
  }

  // Draw Beta path (red)
  if (state.betaPaths.length > 1) {
    drawPath(state.betaPaths, betaStale ? "#21262d" : "#f85149", w, h);
  }
}

function drawPath(points, color, canvasW, canvasH) {
  if (points.length < 2) return;

  // Auto-scale: find bounds
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const allPoints = [...state.alphaPaths, ...state.betaPaths];
  for (const p of allPoints) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }

  const rangeX = Math.max(maxX - minX, 100);
  const rangeY = Math.max(maxY - minY, 100);
  const margin = 30;
  const scaleX = (canvasW - margin * 2) / rangeX;
  const scaleY = (canvasH - margin * 2) / rangeY;

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;

  for (let i = 0; i < points.length; i++) {
    const px = margin + (points[i].x - minX) * scaleX;
    const py = canvasH - margin - (points[i].y - minY) * scaleY;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.stroke();

  // Draw current position dot
  const last = points[points.length - 1];
  const lx = margin + (last.x - minX) * scaleX;
  const ly = canvasH - margin - (last.y - minY) * scaleY;
  ctx.beginPath();
  ctx.arc(lx, ly, 5, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

// ---------------------------------------------------------------------------
// Image Slideshow
// ---------------------------------------------------------------------------
function addImage(robot_id, timestamp, image_b64) {
  state.images.push({ robot_id, timestamp, image_b64 });
  playbackSlider.max = state.images.length - 1;

  if (state.sliderAtEnd) {
    playbackSlider.value = playbackSlider.max;
    displayImage(state.images.length - 1);
  }
}

function displayImage(index) {
  if (index < 0 || index >= state.images.length) return;

  const img = state.images[index];
  imageDisplay.innerHTML = `<img src="data:image/jpeg;base64,${img.image_b64}" alt="Robot view" />`;
  imageSourceLabel.textContent = img.robot_id === "robot_alpha" ? "LEADER (Alpha)" : "FOLLOWER (Beta)";
  imageTimestampLabel.textContent = img.timestamp;
}

playbackSlider.addEventListener("input", () => {
  const idx = parseInt(playbackSlider.value);
  state.sliderAtEnd = idx >= state.images.length - 1;
  displayImage(idx);
});

// ---------------------------------------------------------------------------
// State Updates
// ---------------------------------------------------------------------------
function updateGlobalState(newState) {
  globalStateEl.textContent = newState;
  if (newState.includes("TARGET_CONFIRMED")) {
    globalStateEl.classList.add("confirmed");
  } else {
    globalStateEl.classList.remove("confirmed");
  }
}

function updateBetaStatus(online) {
  state.betaOnline = online;
  if (online) {
    followerBadge.textContent = "ONLINE";
    followerBadge.className = "badge online";
  } else {
    followerBadge.textContent = "OFFLINE (RELAY MODE)";
    followerBadge.className = "badge offline";
  }
}

// ---------------------------------------------------------------------------
// Simulation Harness (development only)
// ---------------------------------------------------------------------------

function initSimulationHarness() {
  // Show harness only in development
  fetch("/api/state")
    .then((r) => r.json())
    .then(() => {
      // If we can reach the server, check if harness should show
      // Server sets NODE_ENV; we detect dev by checking if harness element exists
      // and the server responds (dev mode always shows it)
      simulationHarness.style.display = "block";
    })
    .catch(() => {});
}

// Always show in dev (server-side gating via template would be ideal,
// but for static files we show it and let the server control behavior)
if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
  simulationHarness.style.display = "block";
}

// --- Simulate 30s Exploration Logs ---
document.getElementById("btn-mock-explore-stream").addEventListener("click", () => {
  // Cancel existing simulation
  if (state.simulationTimers.explore) {
    clearInterval(state.simulationTimers.explore);
  }

  let elapsed = 0;
  const interval = 200; // ms
  const duration = 30000; // 30s
  let alphaAngle = 0;
  let betaAngle = Math.PI / 2;

  state.simulationTimers.explore = setInterval(() => {
    elapsed += interval;
    if (elapsed > duration) {
      clearInterval(state.simulationTimers.explore);
      state.simulationTimers.explore = null;
      return;
    }

    // Generate spiral-like paths
    alphaAngle += 0.1;
    betaAngle += 0.08;
    const alphaR = 50 + elapsed / 500;
    const betaR = 30 + elapsed / 600;

    const alphaPayload = {
      robot_id: "robot_alpha",
      timestamp: new Date().toISOString(),
      coords: {
        x: Math.cos(alphaAngle) * alphaR,
        y: Math.sin(alphaAngle) * alphaR,
      },
      sensor_data: { distance_mm: 200 + Math.random() * 100, color: "none" },
    };

    const betaPayload = {
      robot_id: "robot_beta",
      timestamp: new Date().toISOString(),
      coords: {
        x: Math.cos(betaAngle) * betaR + 100,
        y: Math.sin(betaAngle) * betaR + 50,
      },
      sensor_data: { distance_mm: 150 + Math.random() * 80, color: "none" },
    };

    fetch("/api/telemetry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(alphaPayload),
    });

    fetch("/api/telemetry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(betaPayload),
    });
  }, interval);
});

// --- Simulate Follower Beta Network Disconnect ---
document.getElementById("btn-mock-follower-disconnect").addEventListener("click", () => {
  updateBetaStatus(false);
  // Stop sending Beta telemetry (simulate disconnect)
  if (state.simulationTimers.explore) {
    // If exploration is running, we just mark Beta as offline
    // The canvas will show stale after 5s
  }
  state.lastBetaTs = 0; // Force stale immediately
  renderCanvas();
});

// --- Simulate Beta Color Sensor Blue Objective Found ---
document.getElementById("btn-mock-target-found").addEventListener("click", () => {
  // Cancel existing
  if (state.simulationTimers.targetFound) {
    clearTimeout(state.simulationTimers.targetFound);
  }

  // Post a blue detection telemetry event
  const payload = {
    robot_id: "robot_beta",
    timestamp: new Date().toISOString(),
    coords: {
      x: state.betaPaths.length > 0 ? state.betaPaths[state.betaPaths.length - 1].x : 80,
      y: state.betaPaths.length > 0 ? state.betaPaths[state.betaPaths.length - 1].y : 60,
    },
    sensor_data: { distance_mm: 45, color: "blue" },
    // In real scenario, image_b64 would trigger Bedrock verification
    // For simulation, we directly update state
  };

  fetch("/api/telemetry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  // Simulate Bedrock confirmation after 2s delay
  state.simulationTimers.targetFound = setTimeout(() => {
    updateGlobalState("COOPERATIVE_ENGAGED: TARGET_CONFIRMED");
  }, 2000);
});

// ---------------------------------------------------------------------------
// Periodic stale check
// ---------------------------------------------------------------------------
setInterval(() => {
  const now = Date.now();
  if (state.betaOnline && now - state.lastBetaTs > 5000 && state.lastBetaTs > 0) {
    updateBetaStatus(false);
  }
  renderCanvas();
}, 2000);

// ---------------------------------------------------------------------------
// Load historical data on page load
// ---------------------------------------------------------------------------
async function loadHistory() {
  try {
    const resp = await fetch("/api/telemetry/history?limit=500");
    const rows = await resp.json();

    for (const row of rows) {
      const point = { x: row.position_x, y: row.position_y, timestamp: row.timestamp };
      if (row.robot_id === "robot_alpha") {
        state.alphaPaths.push(point);
      } else {
        state.betaPaths.push(point);
      }
    }

    // Trim to max
    while (state.alphaPaths.length > MAX_PATH_POINTS) state.alphaPaths.shift();
    while (state.betaPaths.length > MAX_PATH_POINTS) state.betaPaths.shift();

    renderCanvas();

    // Load images
    const imgResp = await fetch("/api/images?limit=100");
    const images = await imgResp.json();
    for (const img of images) {
      state.images.push({
        robot_id: img.robot_id,
        timestamp: img.timestamp,
        image_b64: img.image_b64,
      });
    }
    if (state.images.length > 0) {
      playbackSlider.max = state.images.length - 1;
      playbackSlider.value = playbackSlider.max;
      displayImage(state.images.length - 1);
    }
  } catch (err) {
    console.warn("Failed to load history:", err);
  }
}

loadHistory();
