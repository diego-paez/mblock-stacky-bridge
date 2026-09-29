"""The safety limits must be identical everywhere: protocol doc, hub, mBot runtime, Stack-chan firmware."""
import glob
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "hub"))
from stacky_hub import limits as L  # noqa: E402

NAMES = ["MAX_RPM", "MAX_MOVE_S", "MAX_PROG_S", "MAX_PROG_STEPS", "ARM_MIN", "ARM_MAX", "ARM_HOME",
         "GRIP_MIN", "GRIP_MAX", "GRIP_HOME", "SERVO_DEG_PER_S", "OBSTACLE_CM", "WATCHDOG_S", "HB_PERIOD_S",
         "MAX_STEP_CM", "MAX_TURN_DEG", "MAX_DRIVE_LEASE_MS", "MAX_DRIVE_DEG_S"]
EXPECTED = {n: float(getattr(L, n)) for n in NAMES}

# The user's hardware range (2026-09-26): arm 40 (lowest) - 120 (highest), gripper 45 (closed) - 120 (open),
# home 90. On 2026-09-28 the user asked for a 5 deg margin inside that range. Change only on purpose.
HARDWARE = {"ARM_MIN": 40, "ARM_MAX": 120, "GRIP_MIN": 45, "GRIP_MAX": 120}
MARGIN_DEG = 5
USER_SPEC = {"ARM_MIN": 45, "ARM_MAX": 115, "ARM_HOME": 90, "GRIP_MIN": 50, "GRIP_MAX": 115, "GRIP_HOME": 90}


def test_user_servo_mapping():
    for k, v in USER_SPEC.items():
        assert getattr(L, k) == v, k
    for k, hw in HARDWARE.items():   # always at least MARGIN_DEG inside the hardware range
        assert (getattr(L, k) - hw) * (1 if k.endswith("MIN") else -1) >= MARGIN_DEG, k


def test_protocol_doc_matches():
    doc = open(os.path.join(ROOT, "docs", "protocol.md"), encoding="utf-8").read()
    for n in ["MAX_RPM", "MAX_MOVE_S", "MAX_PROG_S", "MAX_PROG_STEPS", "SERVO_DEG_PER_S", "OBSTACLE_CM",
              "WATCHDOG_S", "HB_PERIOD_S", "MAX_STEP_CM", "MAX_TURN_DEG", "MAX_DRIVE_LEASE_MS", "MAX_DRIVE_DEG_S"]:
        m = re.search(r"`%s` \| ([\d.]+)" % n, doc)
        assert m and float(m.group(1)) == EXPECTED[n], n
    for axis in ("ARM", "GRIP"):
        m = re.search(r"`%s_MIN/MAX/HOME`[^|]*\| (\d+) / (\d+) / (\d+)" % axis, doc)
        assert m, axis
        assert [float(x) for x in m.groups()] == [EXPECTED[axis + s] for s in ("_MIN", "_MAX", "_HOME")]


def test_runtime_matches():
    src = open(os.path.join(ROOT, "mbot", "micropython", "stacky_runtime.py"), encoding="utf-8").read()
    for n in NAMES:
        m = re.search(r"^%s = ([\d.]+)" % n, src, re.M)
        assert m and float(m.group(1)) == EXPECTED[n], n


FIRMWARE_HEADERS = glob.glob(os.path.join(ROOT, "..", "stackchan-mcp-mbot", "firmware", "components",
                                          "mbot_link", "**", "mbot_limits.h"), recursive=True)


@pytest.mark.skipif(not FIRMWARE_HEADERS, reason="firmware worktree not present")
def test_firmware_header_matches():
    src = open(FIRMWARE_HEADERS[0], encoding="utf-8").read()
    for n in NAMES:
        m = re.search(r"MBOT_%s\b\s*=?\s*\(?\s*([\d.]+)" % n, src)
        assert m and float(m.group(1)) == EXPECTED[n], n
