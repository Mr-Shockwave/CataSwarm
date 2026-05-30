"""
CataSwarm - Robot Beta (Follower Hub)
Rule-based safety MicroPython for LEGO Spike Prime / Pybricks
Requirements: 4-6 from .kiro/specs/cataswarm-system/requirements.md

Behavior priority:
  1. Remote override commands (highest) — immediate execution
  2. Local obstacle avoidance safety routine
  3. Default forward cruise at 50mm/s (lowest)
"""

from pybricks.hubs import PrimeHub
from pybricks.pupdevices import Motor, UltrasonicSensor, ColorSensor
from pybricks.parameters import Port, Direction, Color, Stop
from pybricks.robotics import DriveBase
from pybricks.tools import wait, StopWatch
from usys import stdin
from uselect import poll

# ---------------------------------------------------------------------------
# 1. Hub Hardware Port Mapping
# ---------------------------------------------------------------------------
hub = PrimeHub()

# Drive motors
left_motor = Motor(Port.C, Direction.COUNTERCLOCKWISE)
right_motor = Motor(Port.D)

# DriveBase (standard config matching Beta's physical build)
drive_base = DriveBase(left_motor, right_motor, wheel_diameter=56, axle_track=124)

# Sensors
ultrasonic = UltrasonicSensor(Port.E)
color_sensor = ColorSensor(Port.B)

# Arm/Gripper motor
arm_motor = Motor(Port.F)

# ---------------------------------------------------------------------------
# Color lookup for telemetry
# ---------------------------------------------------------------------------
COLOR_MAP = {
    Color.BLACK: "black",
    Color.BLUE: "blue",
    Color.GREEN: "green",
    Color.RED: "red",
    Color.WHITE: "white",
    Color.YELLOW: "yellow",
    Color.NONE: "none",
}

# ---------------------------------------------------------------------------
# 2. Non-blocking BLE UART polling
# ---------------------------------------------------------------------------
stdin_poll = poll()
stdin_poll.register(stdin)

_rx_buf = ""

# ---------------------------------------------------------------------------
# 3. Override state management
# ---------------------------------------------------------------------------
OVERRIDE_TIMEOUT_MS = 5000
override_active = False
override_timer = StopWatch()


def activate_override():
    """Enter override mode and reset the timeout timer."""
    global override_active
    override_active = True
    override_timer.reset()


def check_override_expiry():
    """Release override mode if timeout has elapsed (Req 6, AC 5-6)."""
    global override_active
    if override_active and override_timer.time() >= OVERRIDE_TIMEOUT_MS:
        override_active = False


# ---------------------------------------------------------------------------
# 4. Command dispatch — Override Engine
# ---------------------------------------------------------------------------
def handle_command(cmd_str):
    """Parse and execute a remote override command."""
    tokens = cmd_str.split(":")

    if len(tokens) == 0:
        return

    verb = tokens[0]

    if verb == "OVERRIDE_MOVE" and len(tokens) == 3:
        try:
            speed = int(tokens[1])
            steering = int(tokens[2])
            if -150 <= speed <= 150 and -90 <= steering <= 90:
                activate_override()
                drive_base.drive(speed, steering)
        except ValueError:
            pass

    elif verb == "OVERRIDE_STOP":
        activate_override()
        drive_base.stop()

    elif verb == "OVERRIDE_ARM_OPEN":
        activate_override()
        arm_motor.run_angle(200, -90, then=Stop.HOLD, wait=False)

    elif verb == "OVERRIDE_ARM_CLOSE":
        activate_override()
        arm_motor.run_angle(200, 90, then=Stop.HOLD, wait=False)

    # Unrecognized commands silently discarded (Req 6, AC 7)


def poll_commands():
    """Non-blocking read of BLE UART stdin; dispatch complete lines."""
    global _rx_buf

    while stdin_poll.poll(0):
        ch = stdin.read(1)
        if ch == "\n":
            line = _rx_buf.strip()
            _rx_buf = ""
            if line:
                handle_command(line)
        else:
            _rx_buf += ch
            # Protocol guard: 64-byte max command length
            if len(_rx_buf) > 64:
                _rx_buf = ""


# ---------------------------------------------------------------------------
# 5. Sensor reading helpers
# ---------------------------------------------------------------------------
def read_distance():
    """Read ultrasonic distance; return -1 on failure."""
    try:
        d = ultrasonic.distance()
        if 0 <= d <= 2000:
            return d
        return -1
    except Exception:
        return -1


def read_color():
    """Read color sensor; return lowercase color string."""
    try:
        c = color_sensor.color()
        return COLOR_MAP.get(c, "none")
    except Exception:
        return "none"


# ---------------------------------------------------------------------------
# 6. Local Safety Routine — obstacle avoidance
# ---------------------------------------------------------------------------
CRUISE_SPEED = 50        # mm/s default forward speed
OBSTACLE_THRESHOLD = 60  # mm — trigger avoidance below this
REVERSE_DISTANCE = 30    # mm to back up
AVOID_TURN_ANGLE = 90    # degrees clockwise


def obstacle_avoidance():
    """
    Execute the reactive safety maneuver:
    Stop -> Reverse 30mm -> Turn 90° clockwise.
    Polls for override commands between each phase so overrides
    can interrupt the maneuver at any point.
    """
    # Stop immediately
    drive_base.stop()
    wait(50)

    # Check if override arrived during stop
    poll_commands()
    if override_active:
        return

    # Reverse 30mm
    drive_base.straight(-REVERSE_DISTANCE)

    # Check again
    poll_commands()
    if override_active:
        return

    # Turn 90° clockwise
    drive_base.turn(AVOID_TURN_ANGLE)


# ---------------------------------------------------------------------------
# 7. Main loop
# ---------------------------------------------------------------------------
telemetry_timer = StopWatch()

# Start cruising
drive_base.drive(CRUISE_SPEED, 0)

while True:
    # --- Priority 1: Poll for remote override commands ---
    poll_commands()

    # --- Check override timeout expiry ---
    check_override_expiry()

    # --- Priority 2 & 3: Local safety routine (only when not overridden) ---
    if not override_active:
        dist = read_distance()

        if dist != -1 and dist < OBSTACLE_THRESHOLD:
            # Obstacle detected — execute avoidance maneuver
            obstacle_avoidance()

            # Resume cruise if override didn't take over
            if not override_active:
                drive_base.drive(CRUISE_SPEED, 0)
        else:
            # No obstacle — ensure we're cruising
            drive_base.drive(CRUISE_SPEED, 0)

    # --- Telemetry broadcast every 200ms ---
    if telemetry_timer.time() >= 200:
        telemetry_timer.reset()

        dist = read_distance()
        col = read_color()

        # Output over BLE UART (stdout)
        print("BETA_SENSORS:" + str(dist) + ":" + col)

    # Yield to avoid busy-spin; keeps loop responsive
    wait(10)
