"""Stacky teleop v1 packets (UDP, joystick <-> Stack-chan). Spec: docs/teleop.md.

The same layout as firmware/components/mbot_link/teleop_packet.h in stackchan-mcp and
remote/joyc/src/teleop_packet.h; all three are checked against GOLDEN_CMD_VEL.
CMD_VEL carries a ROS 2 geometry_msgs/Twist (linear.x in mm/s, angular.z in mrad/s),
so a ROS 2 bridge only has to scale units.
"""
import struct
from dataclasses import dataclass

PORT = 8790
DEFAULT_KEY = 0x53544B31
VERSION = 1
CMD_VEL, STATUS, HELLO, HELLO_ACK = 1, 2, 3, 4

ENABLE, ESTOP, ARM_UP, ARM_DOWN, GRIP_OPEN, GRIP_CLOSE, HOME = (1 << i for i in range(7))
MBOT_CONNECTED, MBOT_LOCKED, TELEOP_ACTIVE, GATEWAY_CONNECTED = (1 << i for i in range(4))

_HDR = struct.Struct("<2sBBII")      # magic, version, type, seq, key (12 bytes)
_CMD = struct.Struct("<hhHH")        # linear_x_mm_s, angular_z_mrad_s, buttons, lease_ms
_STATUS = struct.Struct("<BBHBBH")   # flags, battery, dist_cm, arm, grip, rtt_ms

# seq 7, key 0x12345678, 150 mm/s, -500 mrad/s, ENABLE|ARM_UP, lease 300 ms
GOLDEN_CMD_VEL = bytes.fromhex("5354010107000000785634129600" "0cfe05002c01")


@dataclass
class Status:
    flags: int
    battery_pct: int | None
    dist_cm: int | None
    arm_deg: int
    grip_deg: int
    rtt_ms: int | None

    @property
    def mbot_connected(self):
        return bool(self.flags & MBOT_CONNECTED)


def _clamp16(v):
    return max(-32768, min(32767, int(round(v))))


def encode_hello(seq, key=DEFAULT_KEY):
    return _HDR.pack(b"ST", VERSION, HELLO, seq & 0xFFFFFFFF, key)


def encode_cmd_vel(seq, linear_mm_s, angular_mrad_s, buttons=0, lease_ms=300, key=DEFAULT_KEY):
    return (_HDR.pack(b"ST", VERSION, CMD_VEL, seq & 0xFFFFFFFF, key)
            + _CMD.pack(_clamp16(linear_mm_s), _clamp16(angular_mrad_s), buttons & 0xFFFF, lease_ms & 0xFFFF))


def twist_to_cmd_vel(seq, linear_x_m_s, angular_z_rad_s, buttons=ENABLE, lease_ms=300, key=DEFAULT_KEY):
    """ROS 2 geometry_msgs/Twist units -> packet."""
    return encode_cmd_vel(seq, linear_x_m_s * 1000, angular_z_rad_s * 1000, buttons, lease_ms, key)


def parse(data):
    """Return (type, seq, key, body) or None. body is a dict for CMD_VEL, a Status for STATUS/HELLO_ACK."""
    if len(data) < _HDR.size:
        return None
    magic, ver, typ, seq, key = _HDR.unpack_from(data)
    if magic != b"ST" or ver != VERSION:
        return None
    if typ == CMD_VEL and len(data) >= _HDR.size + _CMD.size:
        lin, ang, btn, lease = _CMD.unpack_from(data, _HDR.size)
        return typ, seq, key, {"linear_mm_s": lin, "angular_mrad_s": ang, "buttons": btn, "lease_ms": lease}
    if typ in (STATUS, HELLO_ACK) and len(data) >= _HDR.size + _STATUS.size:
        f, b, d, a, g, r = _STATUS.unpack_from(data, _HDR.size)
        return typ, seq, key, Status(f, None if b == 0xFF else b, None if d == 0xFFFF else d, a, g,
                                     None if r == 0xFFFF else r)
    if typ == HELLO:
        return typ, seq, key, None
    return None
