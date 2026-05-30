# CataSwarm — Asymmetric Swarm Visual Verification Spec
# Kane CLI End-to-End Browser Automation
# Requirements: 19-20 from .kiro/specs/cataswarm-system/requirements.md

## Test: Dashboard Initial Load

Navigate to `http://localhost:3000/dashboard`
Wait for page to load within 10 seconds

Assert that element `#leader-status-panel` is visible
Assert that element `#follower-status-panel` is visible
Assert that element `#telemetry-canvas` is visible
Assert that element `#playback-slider` is visible
Assert that element `#global-swarm-state` is visible
Assert that element `#simulation-harness` is visible
Assert that element `#btn-mock-explore-stream` is visible
Assert that element `#btn-mock-follower-disconnect` is visible
Assert that element `#btn-mock-target-found` is visible

## Test: Simulate 30s Exploration Logs

Click the button with text "Simulate 30s Exploration Logs"

Wait 5 seconds for canvas rendering to populate

Verify that the canvas `#telemetry-canvas` contains rendered path elements
Verify that a green colored path (Alpha LEADER trajectory) is drawn on the canvas
Verify that a red colored path (Beta FOLLOWER trajectory) is drawn on the canvas

## Test: Simulate Follower Beta Network Disconnect

Click the button with text "Simulate Follower Beta Network Disconnect"

Wait 2 seconds for state propagation

Assert that element `#follower-badge` contains text "OFFLINE (RELAY MODE)"
Assert that element `#follower-badge` has class "offline"

## Test: Simulate Beta Color Sensor Blue Objective Found

Click the button with text "Simulate Beta Color Sensor Blue Objective Found"

Pause for 2000ms to allow cloud model validation routines to finalize processing

Assert that element `#global-swarm-state` contains exact text "COOPERATIVE_ENGAGED: TARGET_CONFIRMED"
Assert that element `#global-swarm-state` has class "confirmed"

## Test: Dashboard Responsive Layout

Resize viewport to 900px width
Assert that element `#leader-status-panel` is visible
Assert that element `#follower-status-panel` is visible
Verify that panels stack vertically in single-column layout

Resize viewport to 1200px width
Verify that panels display in two-column grid layout
