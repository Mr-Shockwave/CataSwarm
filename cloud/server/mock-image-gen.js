/**
 * CataSwarm — Procedural Mock Image Generator
 * Generates tiny BMP images in pure JS (no native deps).
 * Represents robot POV: floor gradient, wall at distance, color bar at bottom.
 *
 * Output: base64-encoded BMP string ready for the Street View panel.
 */

const IMG_WIDTH = 160;
const IMG_HEIGHT = 120;

// BMP color constants
const COLOR_PALETTE = {
  none: [80, 80, 80],
  black: [20, 20, 20],
  blue: [30, 60, 220],
  green: [30, 180, 60],
  red: [200, 40, 40],
  white: [240, 240, 240],
  yellow: [220, 200, 30],
};

/**
 * Generate a procedural robot-view image.
 * @param {number} distanceMm - Ultrasonic distance (0-2000mm)
 * @param {string} color - Detected color string
 * @param {string} robotId - "robot_alpha" or "robot_beta"
 * @returns {string} Base64-encoded BMP image
 */
function generateMockImage(distanceMm, color, robotId) {
  const pixels = new Uint8Array(IMG_WIDTH * IMG_HEIGHT * 3);

  // Normalize distance to wall position (0=far, 1=close)
  const wallProximity = Math.max(0, Math.min(1, 1 - distanceMm / 500));
  const wallY = Math.floor(IMG_HEIGHT * 0.2 + wallProximity * IMG_HEIGHT * 0.5);

  // Robot tint
  const isAlpha = robotId === "robot_alpha";
  const tintR = isAlpha ? 0 : 20;
  const tintG = isAlpha ? 20 : 0;
  const tintB = 0;

  for (let y = 0; y < IMG_HEIGHT; y++) {
    for (let x = 0; x < IMG_WIDTH; x++) {
      const idx = (y * IMG_WIDTH + x) * 3;
      let r, g, b;

      if (y < 10) {
        // Top bar: sky/ceiling (dark gray)
        r = 30 + tintR;
        g = 30 + tintG;
        b = 40;
      } else if (y < wallY) {
        // Wall area (gets brighter as wall is closer)
        const shade = 40 + Math.floor(wallProximity * 120);
        r = shade + tintR;
        g = shade + tintG;
        b = shade - 10;
        // Add some texture noise
        if ((x + y) % 8 === 0) {
          r = Math.min(255, r + 15);
          g = Math.min(255, g + 15);
          b = Math.min(255, b + 15);
        }
      } else if (y >= IMG_HEIGHT - 15) {
        // Bottom bar: color sensor reading
        const col = COLOR_PALETTE[color] || COLOR_PALETTE.none;
        r = col[0];
        g = col[1];
        b = col[2];
      } else {
        // Floor gradient (darker further away)
        const floorProgress = (y - wallY) / (IMG_HEIGHT - 15 - wallY);
        const floorShade = 50 + Math.floor(floorProgress * 80);
        r = floorShade + tintR;
        g = floorShade + 10 + tintG;
        b = floorShade - 5;
        // Floor line markers
        if (y % 20 < 2) {
          r = Math.min(255, r + 40);
          g = Math.min(255, g + 40);
          b = Math.min(255, b + 40);
        }
      }

      pixels[idx] = Math.min(255, Math.max(0, b));     // BMP is BGR
      pixels[idx + 1] = Math.min(255, Math.max(0, g));
      pixels[idx + 2] = Math.min(255, Math.max(0, r));
    }
  }

  return encodeBMP(pixels, IMG_WIDTH, IMG_HEIGHT);
}

/**
 * Encode raw BGR pixel data as a base64 BMP.
 */
function encodeBMP(pixels, width, height) {
  const rowSize = Math.ceil((width * 3) / 4) * 4; // Rows padded to 4-byte boundary
  const pixelDataSize = rowSize * height;
  const fileSize = 54 + pixelDataSize;

  const buffer = Buffer.alloc(fileSize);

  // BMP Header (14 bytes)
  buffer.write("BM", 0);                          // Signature
  buffer.writeUInt32LE(fileSize, 2);               // File size
  buffer.writeUInt32LE(0, 6);                      // Reserved
  buffer.writeUInt32LE(54, 10);                    // Pixel data offset

  // DIB Header (40 bytes)
  buffer.writeUInt32LE(40, 14);                    // Header size
  buffer.writeInt32LE(width, 18);                  // Width
  buffer.writeInt32LE(-height, 22);                // Height (negative = top-down)
  buffer.writeUInt16LE(1, 26);                     // Color planes
  buffer.writeUInt16LE(24, 28);                    // Bits per pixel
  buffer.writeUInt32LE(0, 30);                     // Compression (none)
  buffer.writeUInt32LE(pixelDataSize, 34);         // Image size
  buffer.writeInt32LE(2835, 38);                   // H resolution (72 DPI)
  buffer.writeInt32LE(2835, 42);                   // V resolution
  buffer.writeUInt32LE(0, 46);                     // Colors in palette
  buffer.writeUInt32LE(0, 50);                     // Important colors

  // Pixel data (top-down, BGR, padded rows)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const srcIdx = (y * width + x) * 3;
      const dstIdx = 54 + y * rowSize + x * 3;
      buffer[dstIdx] = pixels[srcIdx];         // B
      buffer[dstIdx + 1] = pixels[srcIdx + 1]; // G
      buffer[dstIdx + 2] = pixels[srcIdx + 2]; // R
    }
    // Padding bytes are already 0 from Buffer.alloc
  }

  return buffer.toString("base64");
}

module.exports = { generateMockImage };
