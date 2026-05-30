/**
 * CataSwarm — Auto-Fallback Mock Telemetry Generator
 *
 * When no real telemetry arrives for 10+ seconds, this module
 * automatically generates realistic exploration data so the
 * dashboard always has something to display.
 *
 * Simulates:
 * - Alpha (Leader): spiral exploration pattern, occasional obstacles
 * - Beta (Follower): offset wandering pattern, discovers "blue" target at ~45s
 * - Realistic sensor noise and color detections
 * - Procedural camera images every ~7 seconds
 */

const { generateMockImage } = require("./mock-image-gen");

const FALLBACK_TIMEOUT_MS = 10000; // Start mock after 10s of silence
const TICK_INTERVAL_MS = 200;      // Match real telemetry rate (200ms)
const BLUE_DETECTION_TICK = 225;   // ~45 seconds in, Beta finds blue target
const IMAGE_INTERVAL_TICKS = 35;   // Generate image every ~7 seconds (35 * 200ms)

// Possible color readings (weighted toward "none" for realism)
const COLORS = ["none", "none", "none", "none", "none", "black", "white", "red", "green", "yellow", "blue"];

class MockTelemetryGenerator {
  constructor({ onTelemetry, onBlueDetection }) {
    this.onTelemetry = onTelemetry;
    this.onBlueDetection = onBlueDetection;
    this.timer = null;
    this.fallbackTimer = null;
    this.tick = 0;
    this.active = false;
    this.lastRealTelemetryAt = Date.now();

    // Alpha state
    this.alphaX = 0;
    this.alphaY = 0;
    this.alphaHeading = 0; // radians

    // Beta state
    this.betaX = 100;
    this.betaY = 50;
    this.betaHeading = Math.PI / 4;

    this.blueDetected = false;
  }

  /**
   * Call this whenever real telemetry arrives from a phone/robot.
   * Resets the fallback timer.
   */
  notifyRealTelemetry() {
    this.lastRealTelemetryAt = Date.now();
    if (this.active) {
      this.stop();
      console.log("[MockGen] Real telemetry detected — stopping mock generator");
    }
  }

  /**
   * Start monitoring. If no real data arrives within timeout, begin generating.
   */
  startMonitoring() {
    this.fallbackTimer = setInterval(() => {
      const elapsed = Date.now() - this.lastRealTelemetryAt;
      if (elapsed >= FALLBACK_TIMEOUT_MS && !this.active) {
        console.log("[MockGen] No real telemetry for 10s — activating fallback mock data");
        this.start();
      }
    }, 2000);
  }

  start() {
    if (this.active) return;
    this.active = true;
    this.tick = 0;
    this.blueDetected = false;

    // Reset positions for fresh demo
    this.alphaX = 0;
    this.alphaY = 0;
    this.alphaHeading = 0;
    this.betaX = 100;
    this.betaY = 50;
    this.betaHeading = Math.PI / 4;

    this.timer = setInterval(() => this._generateTick(), TICK_INTERVAL_MS);
  }

  stop() {
    this.active = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  destroy() {
    this.stop();
    if (this.fallbackTimer) {
      clearInterval(this.fallbackTimer);
      this.fallbackTimer = null;
    }
  }

  _generateTick() {
    this.tick++;

    // --- Alpha movement: gentle spiral exploration ---
    this.alphaHeading += 0.03 + Math.random() * 0.02;
    const alphaSpeed = 8 + Math.random() * 4; // ~8-12mm per tick
    this.alphaX += Math.cos(this.alphaHeading) * alphaSpeed;
    this.alphaY += Math.sin(this.alphaHeading) * alphaSpeed;

    const alphaDistance = 200 + Math.floor(Math.random() * 300);
    const alphaColor = this._randomColor(false);

    const alphaPayload = {
      robot_id: "robot_alpha",
      timestamp: new Date().toISOString(),
      coords: { x: this.alphaX, y: this.alphaY },
      sensor_data: { distance_mm: alphaDistance, color: alphaColor },
    };

    // Attach image every ~7 seconds
    if (this.tick % IMAGE_INTERVAL_TICKS === 0) {
      alphaPayload.image_b64 = generateMockImage(alphaDistance, alphaColor, "robot_alpha");
    }

    this.onTelemetry(alphaPayload);

    // --- Beta movement: offset wandering pattern ---
    this.betaHeading += 0.02 + Math.random() * 0.03;
    // Occasional obstacle avoidance (simulate turn)
    if (this.tick % 50 === 0) {
      this.betaHeading += Math.PI / 2;
    }
    const betaSpeed = 5 + Math.random() * 3; // Slower (50mm/s cruise)
    this.betaX += Math.cos(this.betaHeading) * betaSpeed;
    this.betaY += Math.sin(this.betaHeading) * betaSpeed;

    const betaDistance = this.tick % 50 === 49 ? 40 : 150 + Math.floor(Math.random() * 200);
    const isBlueTick = this.tick === BLUE_DETECTION_TICK;
    const betaColor = isBlueTick ? "blue" : this._randomColor(false);

    const betaPayload = {
      robot_id: "robot_beta",
      timestamp: new Date().toISOString(),
      coords: { x: this.betaX, y: this.betaY },
      sensor_data: { distance_mm: betaDistance, color: betaColor },
    };

    // Attach image every ~7 seconds (offset from Alpha by half)
    if ((this.tick + 17) % IMAGE_INTERVAL_TICKS === 0 || isBlueTick) {
      betaPayload.image_b64 = generateMockImage(betaDistance, betaColor, "robot_beta");
    }

    this.onTelemetry(betaPayload);

    // Blue target detection event
    if (isBlueTick && !this.blueDetected) {
      this.blueDetected = true;
      console.log("[MockGen] Beta detected BLUE target at (%.1f, %.1f)", this.betaX, this.betaY);
      if (this.onBlueDetection) {
        this.onBlueDetection(this.betaX, this.betaY);
      }
    }

    // Loop after ~60 seconds (300 ticks at 200ms)
    if (this.tick >= 300) {
      console.log("[MockGen] Demo cycle complete, restarting...");
      this.tick = 0;
      this.blueDetected = false;
    }
  }

  _randomColor(allowBlue) {
    const pool = allowBlue ? COLORS : COLORS.filter((c) => c !== "blue");
    return pool[Math.floor(Math.random() * pool.length)];
  }
}

module.exports = { MockTelemetryGenerator };
