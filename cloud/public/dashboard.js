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
  targetFound: null, // {x, y} when blue target is detected
  // Edge node state
  edgeNode: {
    device_id: null,
    status: null,
    target_command: null,
    timestamp: null, // epoch float (seconds)
  },
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
    } else if (msg.type === "image") {
      addImage(msg.data.robot_id, msg.data.timestamp, msg.data.image_b64);
    } else if (msg.type === "device_state") {
      updateEdgeNode(msg.data);
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
let ignoreWebSocketTelemetry = false; // Flag to block mock data during manual simulations

function handleTelemetry(data) {
  // When running manual simulations, ignore server-pushed telemetry
  if (ignoreWebSocketTelemetry) return;

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

  // Draw blue target marker if found
  if (state.targetFound) {
    drawTargetMarker(state.targetFound, w, h);
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

function drawTargetMarker(target, canvasW, canvasH) {
  // Calculate position using same scaling as paths
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

  const tx = margin + (target.x - minX) * scaleX;
  const ty = canvasH - margin - (target.y - minY) * scaleY;

  // Pulsing blue circle
  ctx.beginPath();
  ctx.arc(tx, ty, 12, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(30, 100, 255, 0.3)";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(tx, ty, 8, 0, Math.PI * 2);
  ctx.fillStyle = "#1e64ff";
  ctx.fill();

  // Label
  ctx.fillStyle = "#58a6ff";
  ctx.font = "bold 11px sans-serif";
  ctx.fillText("TARGET", tx + 14, ty + 4);
}

// ---------------------------------------------------------------------------
// Image Slideshow — Local folder-based
// ---------------------------------------------------------------------------
const localImages = { alpha: [], beta: [] };
let currentRobot = "alpha";
let currentImageIndex = 0;
let autoPlayTimer = null;

async function loadLocalImages() {
  try {
    const [alphaResp, betaResp] = await Promise.all([
      fetch("/api/local-images/alpha"),
      fetch("/api/local-images/beta"),
    ]);
    localImages.alpha = await alphaResp.json();
    localImages.beta = await betaResp.json();

    const total = localImages[currentRobot].length;
    if (total > 0) {
      playbackSlider.max = total - 1;
      playbackSlider.value = 0;
      currentImageIndex = 0;
      displayLocalImage();
      startAutoPlay();
    } else {
      imageDisplay.innerHTML = '<p class="placeholder">No images yet — drop files into cloud/images/alpha/ or cloud/images/beta/</p>';
    }
    updateImageCounter();
  } catch (err) {
    console.warn("Failed to load local images:", err);
  }
}

function displayLocalImage() {
  const images = localImages[currentRobot];
  if (images.length === 0) return;

  const url = images[currentImageIndex];
  imageDisplay.innerHTML = `<img src="${url}" alt="Robot view" />`;
  imageSourceLabel.textContent = currentRobot === "alpha" ? "LEADER (Alpha)" : "FOLLOWER (Beta)";
  imageTimestampLabel.textContent = `Frame ${currentImageIndex + 1}`;
  updateImageCounter();
}

function updateImageCounter() {
  const counter = document.getElementById("image-counter");
  const total = localImages[currentRobot].length;
  if (counter) counter.textContent = `${currentImageIndex + 1} / ${total}`;
}

function startAutoPlay() {
  stopAutoPlay();
  autoPlayTimer = setInterval(() => {
    const images = localImages[currentRobot];
    if (images.length === 0) return;
    currentImageIndex = (currentImageIndex + 1) % images.length;
    playbackSlider.value = currentImageIndex;
    displayLocalImage();
  }, 7000); // Cycle every 7 seconds (matches telemetry snap interval)
}

function stopAutoPlay() {
  if (autoPlayTimer) {
    clearInterval(autoPlayTimer);
    autoPlayTimer = null;
  }
}

playbackSlider.addEventListener("input", () => {
  const idx = parseInt(playbackSlider.value);
  currentImageIndex = idx;
  displayLocalImage();
  stopAutoPlay(); // Stop auto-advance when user manually scrubs
});

// Toggle between Alpha and Beta views
document.getElementById("btn-toggle-robot").addEventListener("click", () => {
  currentRobot = currentRobot === "alpha" ? "beta" : "alpha";
  const images = localImages[currentRobot];
  currentImageIndex = 0;
  playbackSlider.max = Math.max(0, images.length - 1);
  playbackSlider.value = 0;
  if (images.length > 0) {
    displayLocalImage();
    startAutoPlay();
  } else {
    imageDisplay.innerHTML = `<p class="placeholder">No images for ${currentRobot} — add to cloud/images/${currentRobot}/</p>`;
    updateImageCounter();
  }
});

// Also handle WebSocket images (from real phones or mock generator) as fallback
function addImage(robot_id, timestamp, image_b64) {
  // If we have local images, prefer those. Otherwise show streamed images.
  if (localImages.alpha.length === 0 && localImages.beta.length === 0) {
    let mimeType = "image/bmp";
    if (image_b64.startsWith("/9j/")) mimeType = "image/jpeg";
    else if (image_b64.startsWith("iVBOR")) mimeType = "image/png";
    imageDisplay.innerHTML = `<img src="data:${mimeType};base64,${image_b64}" alt="Robot view" />`;
    imageSourceLabel.textContent = robot_id === "robot_alpha" ? "LEADER (Alpha)" : "FOLLOWER (Beta)";
    imageTimestampLabel.textContent = new Date(timestamp).toLocaleTimeString();
  }
}

loadLocalImages();

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

// Always show in dev — server tells us via /api/state that it's running
// (In production, you'd gate this server-side or remove the harness entirely)
fetch("/api/state").then(() => {
  simulationHarness.style.display = "block";
}).catch(() => {});

// --- Helper: pause mock generator before running manual simulations ---
async function pauseMock() {
  ignoreWebSocketTelemetry = true; // Block incoming WebSocket telemetry
  try {
    await fetch("/api/mock/pause", { method: "POST" });
  } catch (e) { /* ignore */ }
}

// --- Simulate 30s Exploration Logs ---
document.getElementById("btn-mock-explore-stream").addEventListener("click", async () => {
  // Pause background mock and reset state
  await pauseMock();
  if (state.simulationTimers.explore) clearInterval(state.simulationTimers.explore);

  // Clear canvas paths for a fresh start
  state.alphaPaths = [];
  state.betaPaths = [];
  state.targetFound = null;
  updateGlobalState("EXPLORING");
  updateBetaStatus(true);
  renderCanvas();

  let elapsed = 0;
  const interval = 200;
  const duration = 30000;
  let alphaAngle = 0;
  let betaAngle = Math.PI / 2;

  state.simulationTimers.explore = setInterval(() => {
    elapsed += interval;
    if (elapsed > duration) {
      clearInterval(state.simulationTimers.explore);
      state.simulationTimers.explore = null;
      return;
    }

    // Alpha: expanding spiral from center
    alphaAngle += 0.1;
    const alphaR = 20 + elapsed / 400;
    const ax = Math.cos(alphaAngle) * alphaR;
    const ay = Math.sin(alphaAngle) * alphaR;

    // Beta: offset wandering pattern
    betaAngle += 0.08;
    const betaR = 15 + elapsed / 500;
    const bx = Math.cos(betaAngle) * betaR + 150;
    const by = Math.sin(betaAngle) * betaR + 80;

    // Update local state directly for responsiveness
    state.alphaPaths.push({ x: ax, y: ay, timestamp: new Date().toISOString() });
    state.betaPaths.push({ x: bx, y: by, timestamp: new Date().toISOString() });
    if (state.alphaPaths.length > MAX_PATH_POINTS) state.alphaPaths.shift();
    if (state.betaPaths.length > MAX_PATH_POINTS) state.betaPaths.shift();
    state.lastAlphaTs = Date.now();
    state.lastBetaTs = Date.now();

    renderCanvas();
  }, interval);
});

// --- Simulate Follower Beta Network Disconnect ---
document.getElementById("btn-mock-follower-disconnect").addEventListener("click", async () => {
  await pauseMock();

  // Stop the exploration timer from adding more Beta points
  if (state.simulationTimers.explore) {
    clearInterval(state.simulationTimers.explore);
    state.simulationTimers.explore = null;
  }

  // Show Beta going offline — path stays visible but grayed out
  updateBetaStatus(false);
  state.lastBetaTs = 0; // Makes Beta path render in stale gray color

  // Keep Alpha's timestamp fresh so its path stays green
  state.lastAlphaTs = Date.now();

  renderCanvas();
});

// --- Simulate Beta Color Sensor Blue Objective Found ---
document.getElementById("btn-mock-target-found").addEventListener("click", async () => {
  await pauseMock();
  if (state.simulationTimers.targetFound) clearTimeout(state.simulationTimers.targetFound);

  // Mark Beta's last known position as the target location
  const targetX = state.betaPaths.length > 0 ? state.betaPaths[state.betaPaths.length - 1].x : 150;
  const targetY = state.betaPaths.length > 0 ? state.betaPaths[state.betaPaths.length - 1].y : 80;

  // Keep both paths fresh so they stay colored
  state.lastAlphaTs = Date.now();
  state.lastBetaTs = Date.now();
  updateBetaStatus(true);

  // Store the target for canvas rendering
  state.targetFound = { x: targetX, y: targetY };
  renderCanvas();

  // After 2s, confirm target and show state change
  state.simulationTimers.targetFound = setTimeout(() => {
    updateGlobalState("COOPERATIVE_ENGAGED: TARGET_CONFIRMED");
    renderCanvas();
  }, 2000);
});

// ---------------------------------------------------------------------------
// Periodic stale check
// ---------------------------------------------------------------------------
setInterval(() => {
  // Don't run stale checks during manual simulations
  if (ignoreWebSocketTelemetry) return;

  const now = Date.now();
  if (state.betaOnline && now - state.lastBetaTs > 5000 && state.lastBetaTs > 0) {
    updateBetaStatus(false);
  }
  renderCanvas();
}, 2000);

// ---------------------------------------------------------------------------
// Edge Node Card — Robot Beta
// ---------------------------------------------------------------------------
const edgeDeviceIdEl = document.getElementById("edge-device-id");
const edgeStatusBadgeEl = document.getElementById("edge-status-badge");
const edgeCommandEl = document.getElementById("edge-command");
const edgeLastSyncEl = document.getElementById("edge-last-sync");

function updateEdgeNode(data) {
  const { device_id, status, target_command, timestamp } = data;
  state.edgeNode = { device_id, status, target_command, timestamp };

  edgeDeviceIdEl.textContent = device_id || "—";
  edgeCommandEl.textContent = target_command || "—";

  // Badge colour logic
  const isActive =
    status === "INITIALIZING" || target_command === "STARTUP";
  const isHold =
    status === "STABLE_HOLD" || target_command === "HOLD";

  edgeStatusBadgeEl.textContent = status || "—";
  edgeStatusBadgeEl.className = "badge edge-badge " + (
    isActive ? "edge-badge-active" :
    isHold   ? "edge-badge-hold"   :
               "edge-badge-idle"
  );

  renderEdgeSyncAge();
}

function renderEdgeSyncAge() {
  if (!state.edgeNode.timestamp) {
    edgeLastSyncEl.textContent = "No data yet";
    return;
  }
  const nowSec = Date.now() / 1000;
  const ageSec = Math.max(0, Math.round(nowSec - state.edgeNode.timestamp));
  edgeLastSyncEl.textContent = ageSec < 60
    ? `${ageSec}s ago`
    : `${Math.floor(ageSec / 60)}m ${ageSec % 60}s ago`;
}

// Tick the "seconds ago" label every second
setInterval(renderEdgeSyncAge, 1000);

// On page load, fetch the latest device state for robot_beta
async function loadEdgeNodeState() {
  try {
    const resp = await fetch("/api/device-state/robot_beta");
    if (!resp.ok) return;
    const row = await resp.json();
    if (row) updateEdgeNode(row);
  } catch (err) {
    console.warn("Failed to load edge node state:", err);
  }
}

loadEdgeNodeState();

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
  } catch (err) {
    console.warn("Failed to load history:", err);
  }
}

loadHistory();
