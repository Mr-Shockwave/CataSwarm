# Requirements Document

## Introduction

CataSwarm is an asymmetric Leader-Follower robot swarm coordination system built across four layers: Pybricks hub firmware (MicroPython on LEGO Spike Prime), Android Termux edge handlers (Python), an AWS cloud orchestration web platform, and a Kane CLI visual verification suite. The system coordinates two robots (Alpha/Leader and Beta/Follower) through edge computing nodes (Android phones) with local LLM decision-making and central cloud visualization.

## Glossary

- **Alpha_Hub**: The LEGO Spike Prime hub running MicroPython firmware for Robot Alpha (Leader role), connected via BLE to Phone Alpha
- **Beta_Hub**: The LEGO Spike Prime hub running MicroPython firmware for Robot Beta (Follower role), connected via BLE to Phone Beta
- **Phone_Alpha**: The high-spec Android device running Termux with FastAPI server, SQLite database, BLE bridge, and Gemma LLM engine
- **Phone_Beta**: The low-spec Android device running Termux with lightweight broker, BLE bridge, and dual-destination telemetry forwarding
- **Cloud_Platform**: The AWS-hosted web application providing REST API, Bedrock verification, dashboard visualization, and simulation harness
- **Kane_CLI**: The browser automation verification tool that validates dashboard DOM elements, canvas rendering, and state transitions
- **DriveBase**: The Pybricks motor pair abstraction configured with wheel diameter and axle track for coordinated movement
- **BLE_UART**: Bluetooth Low Energy serial communication channel between a Spike Prime hub and its paired Android phone
- **Telemetry_Packet**: A colon-delimited string in the format "<ROBOT>_SENSORS:<distance_mm>:<color_string>\n" transmitted over BLE UART
- **Dead_Reckoning**: Position estimation by integrating motor encoder data to maintain (x, y) coordinates without external positioning
- **Intercept_Engine**: The Gemma LLM-based decision module on Phone Alpha that generates navigation commands when Beta detects a blue target
- **TARGET_CONFIRMED**: The system state entered when AWS Bedrock visual verification confirms a blue target detection from Beta's camera image
- **Simulation_Harness**: The cloud dashboard development tool providing mock event buttons for testing without physical hardware

## Requirements

### Requirement 1: Alpha Hub Telemetry Broadcast

**User Story:** As a system operator, I want Robot Alpha to continuously broadcast sensor readings over BLE, so that Phone Alpha can maintain situational awareness of the leader robot's environment.

#### Acceptance Criteria

1. WHILE Alpha_Hub is powered on and BLE-connected, THE Alpha_Hub SHALL transmit a Telemetry_Packet in the format "ALPHA_SENSORS:<distance_mm>:<color_string>\n" every 200ms (±10ms) over BLE_UART
2. WHEN the Ultrasonic Sensor on Port E returns a distance reading within the range 0 to 2000mm, THE Alpha_Hub SHALL include the distance value as an integer in millimeters as the first colon-delimited field of the Telemetry_Packet
3. WHEN the Color Sensor on Port B detects a color, THE Alpha_Hub SHALL include the color name as a lowercase string from the set {black, blue, green, red, white, yellow, none} as the second colon-delimited field of the Telemetry_Packet
4. IF the Ultrasonic Sensor on Port E returns no echo or an out-of-range reading, THEN THE Alpha_Hub SHALL include the value -1 as the distance field in the Telemetry_Packet
5. IF the Color Sensor on Port B cannot identify a color, THEN THE Alpha_Hub SHALL include the string "none" as the color field in the Telemetry_Packet
6. IF the BLE_UART connection is lost, THEN THE Alpha_Hub SHALL continue collecting sensor data locally and discard readings older than 5 seconds, and upon reconnection SHALL resume transmission starting with the most recent buffered reading

### Requirement 2: Alpha Hub Command Execution

**User Story:** As a system operator, I want Robot Alpha to execute movement and arm commands received over BLE, so that Phone Alpha can remotely control the leader robot.

#### Acceptance Criteria

1. WHEN a "DRIVE:<speed>" command is received over BLE_UART, THE Alpha_Hub SHALL drive the DriveBase forward at the specified speed in mm/s, where speed is an integer in the range -500 to 500 (negative values indicate reverse)
2. WHEN a "TURN:<degrees>" command is received over BLE_UART, THE Alpha_Hub SHALL rotate the DriveBase by the specified number of degrees, where degrees is an integer in the range -360 to 360 (positive values rotate clockwise, negative values rotate counter-clockwise)
3. WHEN a "STOP" command is received over BLE_UART, THE Alpha_Hub SHALL halt all DriveBase motor activity within 100ms of command receipt
4. WHEN an "ARM_OPEN" command is received over BLE_UART, THE Alpha_Hub SHALL rotate the arm motor on Port F to the open position at 90 degrees from the closed reference
5. WHEN an "ARM_CLOSE" command is received over BLE_UART, THE Alpha_Hub SHALL rotate the arm motor on Port F to the closed/grip position at 0 degrees (closed reference)
6. WHEN a "PUSH" command is received over BLE_UART, THE Alpha_Hub SHALL execute a forward push motion by rotating the arm motor on Port F 60 degrees forward and then returning it to its prior position
7. IF an unrecognized command string is received, THEN THE Alpha_Hub SHALL discard the command without affecting current motor state
8. WHEN a new movement command (DRIVE, TURN, or STOP) is received while a previous movement command is in progress, THE Alpha_Hub SHALL cancel the in-progress movement and execute the new command immediately
9. IF a DRIVE or TURN command is received with a parameter value outside the valid range, THEN THE Alpha_Hub SHALL discard the command without affecting current motor state

### Requirement 3: Alpha Hub DriveBase Configuration

**User Story:** As a developer, I want the Alpha hub DriveBase to be configured with precise physical measurements, so that movement commands produce accurate real-world distances and rotations.

#### Acceptance Criteria

1. THE Alpha_Hub SHALL configure the DriveBase with a wheel_diameter of 56mm and an axle_track of 124mm during hub initialization, before the BLE_UART command listener begins accepting commands
2. THE Alpha_Hub SHALL assign the left drive motor to Port C and the right drive motor to Port D as the DriveBase motor pair
3. WHEN a "DRIVE:100\n" command is executed after DriveBase configuration, THE Alpha_Hub SHALL produce forward travel within ±5% of 100mm as measured by motor encoder feedback
4. WHEN a "TURN:90\n" command is executed after DriveBase configuration, THE Alpha_Hub SHALL produce rotation within ±5% of 90 degrees as measured by motor encoder feedback

### Requirement 4: Beta Hub Autonomous Navigation

**User Story:** As a system operator, I want Robot Beta to autonomously explore its environment with obstacle avoidance, so that it can discover targets without constant remote control.

#### Acceptance Criteria

1. WHILE no override command is active, THE Beta_Hub SHALL drive forward at a speed of 50mm/s
2. WHEN the Ultrasonic Sensor on Port E detects an obstacle at less than 60mm distance, THE Beta_Hub SHALL stop forward motion within 100ms of the sensor reading
3. WHEN the Beta_Hub stops due to obstacle detection, THE Beta_Hub SHALL reverse 30mm and then rotate 90 degrees clockwise before resuming forward motion
4. WHILE an override command is active, THE Beta_Hub SHALL suspend the autonomous navigation routine until the override is released per the timeout defined in Requirement 6
5. IF an obstacle is detected during the reverse or rotation maneuver, THEN THE Beta_Hub SHALL halt the current maneuver, reverse an additional 30mm, and restart the 90-degree clockwise rotation

### Requirement 5: Beta Hub Telemetry Broadcast

**User Story:** As a system operator, I want Robot Beta to continuously broadcast sensor readings over BLE, so that Phone Beta can track the follower robot's environment.

#### Acceptance Criteria

1. WHILE Beta_Hub is powered on and BLE-connected, THE Beta_Hub SHALL transmit a Telemetry_Packet in the format "BETA_SENSORS:<distance_mm>:<color_string>\n" every 200ms (±10ms tolerance) over BLE_UART
2. WHEN the Ultrasonic Sensor on Port E returns a distance reading, THE Beta_Hub SHALL include the distance value as an integer in millimeters (range 0 to 2000) as the first colon-delimited field of the Telemetry_Packet
3. WHEN the Color Sensor on Port B detects a color, THE Beta_Hub SHALL include the detected color as a lowercase color name string (e.g., "black", "blue", "green", "red", "white", "yellow", "none") as the second colon-delimited field of the Telemetry_Packet
4. IF the Ultrasonic Sensor on Port E returns no valid reading, THEN THE Beta_Hub SHALL include the value "-1" as the distance field in the Telemetry_Packet
5. IF the Color Sensor on Port B detects no identifiable color, THEN THE Beta_Hub SHALL include the value "none" as the color field in the Telemetry_Packet
6. IF the BLE_UART connection is lost, THEN THE Beta_Hub SHALL continue collecting sensor data locally and resume transmission when the connection is re-established

### Requirement 6: Beta Hub Remote Override

**User Story:** As a system operator, I want to remotely override Beta's autonomous behavior, so that the swarm can coordinate cooperative maneuvers when a target is found.

#### Acceptance Criteria

1. WHEN an "OVERRIDE_MOVE:<speed>:<steering>" command is received over BLE_UART, THE Beta_Hub SHALL drive the DriveBase at the specified speed in mm/s (range: -150 to 150) with the specified steering value in degrees/s (range: -90 to 90), suspending autonomous navigation
2. WHEN an "OVERRIDE_STOP" command is received over BLE_UART, THE Beta_Hub SHALL halt all motor activity and remain stationary until a subsequent override command or the 5-second override timeout elapses
3. WHEN an "OVERRIDE_ARM_OPEN" command is received over BLE_UART, THE Beta_Hub SHALL rotate the arm motor on Port F to the open position
4. WHEN an "OVERRIDE_ARM_CLOSE" command is received over BLE_UART, THE Beta_Hub SHALL rotate the arm motor on Port F to the closed/grip position
5. WHEN any override command is received, THE Beta_Hub SHALL reset the override timeout timer to 5 seconds, measured from the completion of that command's motor action
6. WHEN the override timeout timer expires without a new override command being received, THE Beta_Hub SHALL resume autonomous navigation as defined in Requirement 4
7. IF an "OVERRIDE_MOVE" command is received with speed or steering values outside the valid range or in a non-integer format, THEN THE Beta_Hub SHALL discard the command without affecting current motor state

### Requirement 7: Phone Alpha BLE Bridge and Telemetry Parsing

**User Story:** As a system operator, I want Phone Alpha to maintain a BLE connection to the Alpha hub and parse incoming telemetry, so that sensor data is available for decision-making and cloud forwarding.

#### Acceptance Criteria

1. THE Phone_Alpha SHALL establish a BLE connection to Alpha_Hub using the bleak library and maintain it by subscribing to the BLE_UART notification characteristic
2. WHEN a Telemetry_Packet matching "ALPHA_SENSORS:<distance_mm>:<color_string>\n" is received, THE Phone_Alpha SHALL parse the distance and color fields and store them in the 'my_telemetry' table of the SQLite database 'leader_buffer.db' within 100ms of receipt
3. WHEN a parsed telemetry packet contains a distance_mm value that is a non-negative integer in the range 0 to 2000 and a color_string that is a non-empty lowercase alphabetic string, THE Phone_Alpha SHALL update the Dead_Reckoning (x, y) coordinate estimate for Alpha_Hub
4. IF the BLE connection to Alpha_Hub is lost, THEN THE Phone_Alpha SHALL attempt reconnection every 2 seconds indefinitely until the connection is re-established, logging each failed attempt
5. IF a received BLE_UART message does not match the expected "ALPHA_SENSORS:<distance_mm>:<color_string>\n" format, THEN THE Phone_Alpha SHALL discard the message and log a parse error without interrupting the BLE notification subscription

### Requirement 8: Phone Alpha Camera Capture and Cloud Forwarding

**User Story:** As a system operator, I want Phone Alpha to periodically capture camera images and forward them to the cloud, so that the central dashboard has visual context of the leader robot's environment.

#### Acceptance Criteria

1. WHILE Phone_Alpha is running, THE Phone_Alpha SHALL execute a camera capture via termux-camera-photo every 7 seconds
2. WHEN a camera image is captured, THE Phone_Alpha SHALL encode the image as base64, include the robot identifier "robot_alpha" in the payload, and transmit it via HTTP POST to the Cloud_Platform telemetry endpoint within a timeout of 10 seconds
3. IF the HTTP POST to Cloud_Platform fails due to network error, timeout, or non-2xx response, THEN THE Phone_Alpha SHALL store the base64-encoded image in the SQLite database 'leader_buffer.db' for later retry, retaining a maximum of 50 buffered images and discarding the oldest image when the limit is exceeded
4. WHILE one or more locally stored images are pending in the SQLite database, THE Phone_Alpha SHALL attempt to forward the oldest stored image to the Cloud_Platform every 15 seconds in FIFO order, removing the image from local storage upon successful delivery
5. IF the termux-camera-photo command fails or returns a non-zero exit code, THEN THE Phone_Alpha SHALL skip that capture cycle and retry at the next 7-second interval without interrupting the periodic capture schedule

### Requirement 9: Phone Alpha FastAPI Server

**User Story:** As a system operator, I want Phone Alpha to expose an HTTP endpoint for receiving Beta's telemetry, so that the leader's edge brain has full swarm awareness.

#### Acceptance Criteria

1. THE Phone_Alpha SHALL run a FastAPI HTTP server on port 8080
2. WHEN a POST request is received at /subordinate/sync with a JSON payload containing the required fields (robot_id, distance_mm as integer, color_string as lowercase string, position_x as float, position_y as float, and optionally image_base64 as string), THE Phone_Alpha SHALL parse the payload, store it in the 'subordinate_telemetry' table of 'leader_buffer.db', and respond with HTTP 200
3. WHEN subordinate telemetry is stored, THE Phone_Alpha SHALL update the Dead_Reckoning (x, y) coordinate estimate for Beta_Hub using the position_x and position_y values from the payload
4. IF the POST /subordinate/sync request is missing any required field (robot_id, distance_mm, color_string, position_x, position_y) or contains fields of incorrect type, THEN THE Phone_Alpha SHALL respond with HTTP 400 and an error message indicating which fields are missing or invalid
5. IF the POST /subordinate/sync request body is not valid JSON, THEN THE Phone_Alpha SHALL respond with HTTP 400 and an error message indicating the payload is not parseable

### Requirement 10: Phone Alpha Intercept Engine (Gemma LLM)

**User Story:** As a system operator, I want Phone Alpha to use a local LLM to generate intercept navigation commands when Beta detects a blue target, so that Alpha can autonomously navigate toward the target location.

#### Acceptance Criteria

1. WHEN the subordinate telemetry from Beta_Hub reports a 'blue' color detection and no intercept sequence is currently in progress, THE Phone_Alpha SHALL invoke the Intercept_Engine by sending the current Dead_Reckoning coordinates of both Alpha_Hub and Beta_Hub to the Gemma LLM running on llama.cpp at port 11434, with a response timeout of 10 seconds
2. WHEN the Intercept_Engine returns a response, THE Phone_Alpha SHALL parse the response into a sequence of BLE macro commands (DRIVE, TURN, STOP) and transmit them to Alpha_Hub over BLE_UART
3. WHEN the Intercept_Engine returns a response, THE Phone_Alpha SHALL also parse override commands (OVERRIDE_MOVE, OVERRIDE_STOP) for Beta_Hub and transmit them to Phone_Beta via HTTP POST to /command-override
4. IF the Gemma LLM service on port 11434 does not respond within 10 seconds or the connection is refused, THEN THE Phone_Alpha SHALL log the failure to the application log and continue operating without intercept capability until the next blue detection event
5. IF the Intercept_Engine returns a response that cannot be parsed into valid BLE macro commands, THEN THE Phone_Alpha SHALL discard the response, log a parse error, and not transmit any commands to Alpha_Hub or Beta_Hub
6. WHILE an intercept command sequence is being transmitted to Alpha_Hub, THE Phone_Alpha SHALL suppress additional Intercept_Engine invocations from new blue detection events until the current sequence completes or a maximum duration of 30 seconds elapses

### Requirement 11: Phone Beta BLE Bridge and Telemetry Parsing

**User Story:** As a system operator, I want Phone Beta to maintain a BLE connection to the Beta hub and parse incoming telemetry, so that follower sensor data is available for forwarding.

#### Acceptance Criteria

1. THE Phone_Beta SHALL establish and maintain a BLE connection to Beta_Hub using the bleak library
2. WHEN a Telemetry_Packet matching "BETA_SENSORS:<distance_mm>:<color_string>\n" is received, THE Phone_Beta SHALL parse the distance and color fields and store them in an in-memory queue with a maximum capacity of 100 entries, evicting the oldest entry when the queue is full
3. WHEN a parsed telemetry packet contains a distance_mm value that is a non-negative integer between 0 and 10000 and a color_string that is a non-empty lowercase alphabetic string, THE Phone_Beta SHALL update the Dead_Reckoning (x, y) coordinate estimate for Beta_Hub
4. IF the BLE connection to Beta_Hub is lost, THEN THE Phone_Beta SHALL attempt reconnection every 2 seconds until the connection is re-established
5. IF a received BLE message does not match the expected "BETA_SENSORS:<distance_mm>:<color_string>\n" format, THEN THE Phone_Beta SHALL discard the message and log a parse error

### Requirement 12: Phone Beta Dual-Stream Forwarding

**User Story:** As a system operator, I want Phone Beta to forward telemetry and camera images to both the cloud and Phone Alpha, so that both the central dashboard and the edge brain have full follower awareness.

#### Acceptance Criteria

1. WHILE Phone_Beta is running, THE Phone_Beta SHALL execute a camera capture via termux-camera-photo every 7 seconds, measured from the completion of the previous capture
2. WHEN a camera image is captured, THE Phone_Beta SHALL encode the image as base64 and transmit it via HTTP POST to both the Cloud_Platform telemetry endpoint and Phone_Alpha's /subordinate/sync endpoint, tracking delivery status to each destination independently
3. WHEN telemetry data is available in the in-memory queue, THE Phone_Beta SHALL forward the telemetry to both the Cloud_Platform and Phone_Alpha's /subordinate/sync endpoint within 1 second of the data becoming available
4. IF the HTTP POST to Cloud_Platform fails (network timeout after 5 seconds or non-2xx response), THEN THE Phone_Beta SHALL retain the data in the in-memory queue for retry at 5-second intervals, independently of the Phone_Alpha delivery status
5. IF the HTTP POST to Phone_Alpha fails (network timeout after 5 seconds or non-2xx response), THEN THE Phone_Beta SHALL retain the data in the in-memory queue for retry at 5-second intervals, independently of the Cloud_Platform delivery status
6. IF the in-memory queue exceeds 50 entries, THEN THE Phone_Beta SHALL discard the oldest entries to maintain the queue at 50 entries maximum

### Requirement 13: Phone Beta Command Override Listener

**User Story:** As a system operator, I want Phone Beta to accept override commands from Phone Alpha and route them to the Beta hub, so that the leader's edge brain can coordinate the follower robot.

#### Acceptance Criteria

1. THE Phone_Beta SHALL run a FastAPI HTTP server on port 8080 with a POST /command-override endpoint that accepts a JSON body containing a "command" string field
2. WHEN a POST request is received at /command-override with a recognized override command, THE Phone_Beta SHALL forward the command to Beta_Hub over BLE_UART and respond with HTTP 200 within 500ms
3. WHEN the override command is "OVERRIDE_MOVE:<speed>:<steering>" where speed is an integer between -300 and 300 mm/s and steering is an integer between -100 and 100, THE Phone_Beta SHALL transmit the command verbatim to Beta_Hub
4. WHEN the override command is "OVERRIDE_STOP", THE Phone_Beta SHALL transmit the command verbatim to Beta_Hub
5. WHEN the override command is "OVERRIDE_ARM_OPEN" or "OVERRIDE_ARM_CLOSE", THE Phone_Beta SHALL transmit the command verbatim to Beta_Hub
6. IF the BLE connection to Beta_Hub is unavailable when an override command arrives, THEN THE Phone_Beta SHALL respond with HTTP 503 and queue the command in the in-memory queue up to a maximum of 20 commands for delivery when the connection is restored
7. IF a POST request to /command-override contains an unrecognized command string or missing "command" field, THEN THE Phone_Beta SHALL respond with HTTP 400 and an error message indicating the invalid command format

### Requirement 14: Cloud Platform Telemetry Ingestion

**User Story:** As a system operator, I want the cloud platform to ingest telemetry from both robots, so that all swarm data is centrally stored and available for visualization.

#### Acceptance Criteria

1. THE Cloud_Platform SHALL expose a REST endpoint at POST /api/telemetry that accepts a JSON payload containing the fields: robot_id (string), distance_mm (integer), color_string (string), timestamp (ISO 8601 string), and an optional image field (base64-encoded string)
2. WHEN a telemetry POST is received with a valid robot identifier (robot_alpha or robot_beta) and valid telemetry fields (distance_mm as non-negative integer, color_string as non-empty lowercase string), THE Cloud_Platform SHALL store the telemetry record in the database indexed by timestamp and respond with HTTP 201
3. WHEN a telemetry POST includes a base64-encoded camera image in the image field (maximum 5MB decoded size), THE Cloud_Platform SHALL store the image data alongside the telemetry record
4. IF a telemetry POST is received with an invalid or missing robot identifier, THEN THE Cloud_Platform SHALL respond with HTTP 400 and an error message indicating the robot_id is invalid or missing
5. IF a telemetry POST is received with a valid robot identifier but missing or malformed required fields (distance_mm or color_string), THEN THE Cloud_Platform SHALL respond with HTTP 400 and an error message indicating which fields failed validation

### Requirement 15: Cloud Platform Bedrock Visual Verification

**User Story:** As a system operator, I want the cloud platform to use AWS Bedrock to visually verify target detections, so that false positives from color sensors are filtered before triggering cooperative engagement.

#### Acceptance Criteria

1. WHEN a telemetry POST from Beta_Hub includes a 'blue' color detection and a base64-encoded camera image in the same request payload, THE Cloud_Platform SHALL submit the image to AWS Bedrock (Claude 3.5 Sonnet) with a prompt requesting confirmation of whether a blue-colored target object is visible in the image
2. WHEN AWS Bedrock returns an affirmative response indicating a blue target is present in the image, THE Cloud_Platform SHALL transition the system state to TARGET_CONFIRMED
3. WHEN the system enters TARGET_CONFIRMED state, THE Cloud_Platform SHALL emit a "COOPERATIVE_ENGAGED: TARGET_CONFIRMED" event to connected dashboard clients via the real-time connection within 2 seconds of the state transition
4. IF AWS Bedrock returns a negative or inconclusive response indicating no blue target is confirmed in the image, THEN THE Cloud_Platform SHALL discard the detection record, log the negative verification result, and maintain the current exploration state without state transition
5. IF the AWS Bedrock API call does not return a response within 10 seconds or returns an error, THEN THE Cloud_Platform SHALL treat the verification as inconclusive, discard the detection, maintain the current exploration state, and log the timeout or error condition

### Requirement 16: Cloud Dashboard Telemetry Canvas

**User Story:** As a system operator, I want to see real-time robot trajectories on a canvas grid, so that I can monitor swarm movement patterns visually.

#### Acceptance Criteria

1. THE Cloud_Platform dashboard SHALL render a Telemetry Canvas Grid that maps Dead_Reckoning (x, y) coordinates to pixel positions, displaying robot trajectory paths as connected line segments
2. WHILE telemetry data has been received from Alpha_Hub within the last 5 seconds, THE Cloud_Platform dashboard SHALL render the Alpha trajectory path in green color on the canvas
3. WHILE telemetry data has been received from Beta_Hub within the last 5 seconds, THE Cloud_Platform dashboard SHALL render the Beta trajectory path in red color on the canvas
4. WHEN new telemetry coordinates arrive, THE Cloud_Platform dashboard SHALL append the new position to the corresponding robot's path and render the updated path within 1 second of receipt
5. IF no telemetry data has been received from a robot for more than 5 seconds, THEN THE Cloud_Platform dashboard SHALL visually indicate that the robot's telemetry stream is stale by ceasing path color rendering for that robot
6. THE Cloud_Platform dashboard SHALL retain and display the most recent 500 coordinate points per robot trajectory path, discarding the oldest points when the limit is exceeded

### Requirement 17: Cloud Dashboard Cinematic Street View

**User Story:** As a system operator, I want to view captured camera images in a chronological slideshow with playback controls, so that I can review the robots' visual perspective over time.

#### Acceptance Criteria

1. THE Cloud_Platform dashboard SHALL display a Cinematic Street View Panel showing base64-decoded camera images with a visible label indicating the source robot identifier (robot_alpha or robot_beta) and the capture timestamp for the currently displayed image
2. THE Cloud_Platform dashboard SHALL provide a chronological playback slider allowing navigation through captured images by timestamp, where each discrete slider position corresponds to one captured image ordered by arrival time
3. WHEN the playback slider position changes, THE Cloud_Platform dashboard SHALL display the camera image at the selected slider position within 500ms
4. WHEN new camera images arrive from either robot while the slider is positioned at the most recent image, THE Cloud_Platform dashboard SHALL append them to the chronological image sequence and automatically advance the slider to display the newest image
5. WHEN new camera images arrive from either robot while the slider is not positioned at the most recent image, THE Cloud_Platform dashboard SHALL append them to the chronological image sequence without changing the current slider position
6. IF no camera images have been received yet, THEN THE Cloud_Platform dashboard SHALL display an empty-state placeholder within the Cinematic Street View Panel indicating that no images are available

### Requirement 18: Cloud Dashboard Simulation Harness

**User Story:** As a developer, I want simulation buttons on the dashboard that generate mock events, so that I can test the system end-to-end without physical hardware.

#### Acceptance Criteria

1. THE Cloud_Platform dashboard SHALL display a simulation harness panel containing exactly three control buttons labeled "btn-mock-explore-stream", "btn-mock-follower-disconnect", and "btn-mock-target-found"
2. WHEN the "btn-mock-explore-stream" button is activated, THE Cloud_Platform SHALL generate simulated Telemetry_Packets for both Alpha and Beta robots at a rate of one packet every 200ms for a duration of 30 seconds, using the standard telemetry format and rendering the resulting coordinates on the Telemetry Canvas Grid
3. WHEN the "btn-mock-follower-disconnect" button is activated, THE Cloud_Platform SHALL simulate a Beta network disconnect event and display a visible disconnect indicator element on the dashboard associated with the Beta robot status
4. WHEN the "btn-mock-target-found" button is activated, THE Cloud_Platform SHALL simulate a target discovery event, transition the system state to TARGET_CONFIRMED, and display "COOPERATIVE_ENGAGED: TARGET_CONFIRMED" on the dashboard
5. IF a simulation button is activated while a previously triggered simulation of the same type is still in progress, THEN THE Cloud_Platform SHALL cancel the in-progress simulation and restart it from the beginning

### Requirement 19: Kane CLI Dashboard Verification

**User Story:** As a developer, I want automated browser tests that verify the dashboard renders correctly and responds to state changes, so that UI regressions are caught before deployment.

#### Acceptance Criteria

1. THE Kane_CLI SHALL navigate the browser to localhost:3000/dashboard and wait a maximum of 10 seconds for the page to reach a loaded state before executing any verification assertions
2. THE Kane_CLI SHALL assert the presence of required DOM elements within 5 seconds of page load, including the Telemetry Canvas Grid, Cinematic Street View Panel, and the three simulation harness buttons (btn-mock-explore-stream, btn-mock-follower-disconnect, btn-mock-target-found)
3. WHEN the simulation harness generates mock telemetry via btn-mock-explore-stream, THE Kane_CLI SHALL verify within 5 seconds that the canvas contains at least one rendered path element for the Alpha trajectory (green) and at least one rendered path element for the Beta trajectory (red)
4. WHEN the mock follower disconnect is triggered via btn-mock-follower-disconnect, THE Kane_CLI SHALL verify within 3 seconds that a visible element containing the text "OFFLINE" is displayed on the dashboard
5. WHEN the mock target found is triggered via btn-mock-target-found, THE Kane_CLI SHALL verify within 3 seconds that a visible element containing the text "COOPERATIVE_ENGAGED: TARGET_CONFIRMED" is displayed on the dashboard
6. IF the dashboard at localhost:3000/dashboard fails to load within 10 seconds, THEN THE Kane_CLI SHALL abort the verification run and report a connection failure in the output

### Requirement 20: Kane CLI Workspace Integration

**User Story:** As a developer, I want Kane verification to run automatically on file save, so that I get immediate feedback on dashboard changes during development.

#### Acceptance Criteria

1. THE Kane_CLI SHALL integrate with the Kiro workspace via save hooks that trigger a verification run when a file matching the dashboard source glob pattern is saved
2. WHEN a Kane verification run completes with failures, THE Kane_CLI SHALL output one NDJSON line per failed assertion containing the assertion identifier, the expected value, the actual value, and a human-readable description of the failure
3. WHEN a Kane verification run completes successfully, THE Kane_CLI SHALL output a single NDJSON line indicating success and the total number of assertions that passed
4. IF a file save event occurs while a Kane verification run is already in progress, THEN THE Kane_CLI SHALL queue the new run and execute it after the current run completes
5. IF a Kane verification run does not complete within 30 seconds, THEN THE Kane_CLI SHALL terminate the run and output an NDJSON error line indicating a timeout failure

### Requirement 21: Telemetry Packet Format Parsing

**User Story:** As a developer, I want a well-defined telemetry packet format with reliable parsing, so that all layers of the system can correctly interpret sensor data.

#### Acceptance Criteria

1. THE Alpha_Hub SHALL format telemetry packets as "ALPHA_SENSORS:<distance_mm>:<color_string>\n" where distance_mm is an integer in the range 0 to 2000 and color_string is one of the lowercase values: "black", "blue", "green", "red", "white", "yellow", "none"
2. THE Beta_Hub SHALL format telemetry packets as "BETA_SENSORS:<distance_mm>:<color_string>\n" where distance_mm is an integer in the range 0 to 2000 and color_string is one of the lowercase values: "black", "blue", "green", "red", "white", "yellow", "none"
3. WHEN Phone_Alpha or Phone_Beta receives a telemetry packet, THE receiving phone SHALL parse the packet by splitting on colon delimiters, verifying exactly 3 fields are present, verifying the first field matches the expected prefix, verifying the second field is an integer within 0 to 2000, and verifying the third field is a recognized color value
4. IF a received packet does not match the expected format, THEN THE receiving phone SHALL discard the packet without updating telemetry state and log a parse error containing the raw packet bytes and the reason for rejection
5. THE System SHALL ensure that for all valid Telemetry_Packets, parsing the packet into its component fields and reconstructing the packet string from those fields produces an identical byte sequence (round-trip property)
6. IF the Ultrasonic Sensor returns no detection reading, THEN THE transmitting hub SHALL encode the distance_mm field as 2000 in the Telemetry_Packet

### Requirement 22: BLE Command Protocol Format

**User Story:** As a developer, I want a well-defined command protocol format, so that commands are reliably transmitted and parsed between phones and hubs.

#### Acceptance Criteria

1. THE Phone_Alpha SHALL format commands as colon-delimited ASCII strings terminated by a newline character (e.g., "DRIVE:100\n", "TURN:90\n", "STOP\n") with a maximum total length of 64 bytes including the newline terminator
2. THE Phone_Beta SHALL format override commands as colon-delimited ASCII strings terminated by a newline character (e.g., "OVERRIDE_MOVE:100:50\n", "OVERRIDE_STOP\n") with a maximum total length of 64 bytes including the newline terminator
3. WHEN Alpha_Hub receives a command string, THE Alpha_Hub SHALL parse the command by splitting on colon delimiters, validating that the field count matches the expected count for the command type, and dispatching based on the first field
4. WHEN Beta_Hub receives a command string, THE Beta_Hub SHALL parse the command by splitting on colon delimiters, validating that the field count matches the expected count for the command type, and dispatching based on the first field
5. IF a received command does not match any known command pattern or contains an incorrect number of fields, THEN THE receiving hub SHALL discard the command without side effects
6. WHILE receiving bytes over BLE_UART, THE receiving hub SHALL buffer incoming bytes and treat the newline character as the command boundary, discarding any buffered content that exceeds 64 bytes without a newline terminator
7. THE Phone_Alpha and Phone_Beta SHALL encode all command strings as 7-bit ASCII, and parsing then reconstructing any valid command string SHALL produce an identical byte sequence (round-trip property)
