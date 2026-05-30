"""
CataSwarm - Robot Alpha (Leader Hub)
Performance-optimized MicroPython for LEGO Spike Prime / Pybricks
Requirements: 1-3 from .kiro/specs/cataswarm-system/requirements.md
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

# DriveBase: wheel_diameter=56mm, axle_track=124mm
drive_base = DriveBase(left_motor, right_motor, wheel_diameter=56, axle_track=124)

# Sensors
ultrasonic = UltrasonicSensor(Port.E)
color_sensor = ColorSensor(Port.B)

# Arm/Gripper motor
arm_motor = Motor(Port.F)

# ---------------------------------------------------------------------------
# Performance: pre-compute color lookup for fast string conversion
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
# 2. Non-blocking stdin polling for BLE UART commands
# ---------------------------------------------------------------------------
stdin_poll = poll()
stdin_poll.register(stdin)

# Line buffer for incoming BLE data
_rx_buf = ""

# ---------------------------------------------------------------------------
# 3. Command dispatch
# ---------------------------------------------------------------------------
def handle_command(cmd_str):
    """Parse and execute a single command string."""
    tokens = cmd_str.split(":")
    verb = tokens[0]

    if verb == "DRIVE" and len(tokens) == 2:
        try:
            speed = int(tokens[1])
            if -500 <= speed <= 500:
                drive_base.drive(speed, 0)
        except ValueError:
            pass

    elif verb == "TURN" and len(tokens) == 2:
        try:
            degrees = int(tokens[1])
            if -360 <= degrees <= 360:
                drive_base.turn(degrees)
        except ValueError:
            pass

    elif verb == "STOP":
        drive_base.stop()

    elif verb == "ARM_OPEN":
        arm_motor.run_angle(200, -90, then=Stop.HOLD, wait=False)

    elif verb == "ARM_CLOSE":
        arm_motor.run_angle(200, 90, then=Stop.HOLD, wait=False)

    elif verb == "PUSH":
        arm_motor.run_angle(400, 60, then=Stop.HOLD, wait=True)
        arm_motor.run_angle(400, -60, then=Stop.HOLD, wait=True)

    # Unrecognized commands are silently discarded (Req 2, AC 7)


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
            # Guard against buffer overflow (64-byte max per protocol)
            if len(_rx_buf) > 64:
                _rx_buf = ""


# ---------------------------------------------------------------------------
# 4. Telemetry transmission helpers
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
# 5. Main loop — 200ms telemetry cycle with interleaved command polling
# ---------------------------------------------------------------------------
telemetry_timer = StopWatch()

while True:
    # Poll for inbound commands (non-blocking)
    poll_commands()

    # Transmit telemetry every 200ms
    if telemetry_timer.time() >= 200:
        telemetry_timer.reset()

        dist = read_distance()
        col = read_color()

        # Output over BLE UART (stdout)
        print("ALPHA_SENSORS:" + str(dist) + ":" + col)

    # Yield briefly to avoid busy-spin; keeps loop responsive
    wait(10)
