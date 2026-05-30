---
name: CataSwarm Dashboard Verification
url: http://localhost:3000/dashboard
timeout: 60
---

## Verify dashboard panels load

Assert that the element with id "leader-status-panel" is visible on the page.
Assert that the element with id "follower-status-panel" is visible on the page.
Assert that the element with id "telemetry-canvas" is visible on the page.
Assert that the element with id "global-swarm-state" is visible and contains text "EXPLORING".
Assert that the element with id "simulation-harness" is visible on the page.

## Click Simulate 30s Exploration Logs

Click the button with text "Simulate 30s Exploration Logs".
Wait 5 seconds.
Verify that the canvas element with id "telemetry-canvas" has rendered content (is not blank).

## Click Simulate Follower Beta Network Disconnect

Click the button with text "Simulate Follower Beta Network Disconnect".
Wait 2 seconds.
Assert that the element with id "follower-badge" contains the text "OFFLINE".

## Click Simulate Beta Target Found and verify state transition

Click the button with text "Simulate Beta Color Sensor Blue Objective Found".
Wait 3 seconds.
Assert that the element with id "global-swarm-state" contains the exact text "COOPERATIVE_ENGAGED: TARGET_CONFIRMED".
