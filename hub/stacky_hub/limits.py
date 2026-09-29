"""Safety limits, host side. Mirror of docs/protocol.md (checked by tests/test_limits.py).

The mBot runtime enforces the same limits independently; these exist so the host
never even asks for something unsafe, and so the LLM is told the real ranges.
"""

MAX_RPM = 60
MAX_MOVE_S = 5.0
MAX_PROG_S = 30.0
MAX_PROG_STEPS = 20
ARM_MIN = 45      # S4, lowest
ARM_MAX = 115     # S4, highest
ARM_HOME = 90
GRIP_MIN = 50     # S3, closed
GRIP_MAX = 115    # S3, open
GRIP_HOME = 90
SERVO_DEG_PER_S = 40
OBSTACLE_CM = 10
WATCHDOG_S = 3.0
HB_PERIOD_S = 1.0
MAX_STEP_CM = 30   # v1.1 closed-loop straight
MAX_TURN_DEG = 180  # v1.1 closed-loop turn
MAX_DRIVE_LEASE_MS = 500  # v1.2 teleop: drive stops unless renewed within this
MAX_DRIVE_DEG_S = 90      # v1.2 teleop: fastest turn rate

ACK_TIMEOUT_S = 1.5
