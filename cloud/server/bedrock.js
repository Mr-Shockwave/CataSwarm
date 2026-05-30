/**
 * AWS Bedrock Visual Verification Module
 * Submits base64 images to Claude 3.5 Sonnet for blue target confirmation.
 *
 * Gracefully degrades if AWS SDK is not configured (returns false).
 */

let client = null;
let InvokeModelCommand = null;

try {
  const sdk = require("@aws-sdk/client-bedrock-runtime");
  InvokeModelCommand = sdk.InvokeModelCommand;
  client = new sdk.BedrockRuntimeClient({
    region: process.env.AWS_REGION || "us-east-1",
  });
} catch (err) {
  console.warn("[Bedrock] AWS SDK not available — visual verification disabled");
}

const MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0";
const TIMEOUT_MS = 10000;

/**
 * Verify whether a blue target object is visible in the provided image.
 * @param {string} imageB64 - Base64-encoded image data
 * @returns {Promise<boolean>} - true if blue target confirmed
 */
async function verifyWithBedrock(imageB64) {
  // Graceful degradation if SDK not loaded
  if (!client || !InvokeModelCommand) {
    console.warn("[Bedrock] SDK not configured, skipping verification");
    return false;
  }

  const prompt =
    "Analyze this image from a robot's camera. Is there a distinct blue-colored target object visible in the image? " +
    'Respond with ONLY a JSON object: {"confirmed": true} or {"confirmed": false}. ' +
    "A blue target is a clearly identifiable blue object that stands out from the environment.";

  const requestBody = {
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 100,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: {
              type: "base64",
              media_type: "image/jpeg",
              data: imageB64,
            },
          },
          {
            type: "text",
            text: prompt,
          },
        ],
      },
    ],
  };

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const command = new InvokeModelCommand({
      modelId: MODEL_ID,
      contentType: "application/json",
      accept: "application/json",
      body: JSON.stringify(requestBody),
    });

    const response = await client.send(command, {
      abortSignal: controller.signal,
    });

    const responseBody = JSON.parse(new TextDecoder().decode(response.body));
    const text = responseBody.content?.[0]?.text || "";

    // Parse the JSON response
    const jsonMatch = text.match(/\{[^}]+\}/);
    if (jsonMatch) {
      const result = JSON.parse(jsonMatch[0]);
      return result.confirmed === true;
    }

    return false;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Bedrock verification timed out after 10s");
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = { verifyWithBedrock };
