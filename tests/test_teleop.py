"""Stacky teleop v1 packet layout: must match the Stack-chan firmware and the JoyC firmware byte for byte."""
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "hub"))
from stacky_hub import teleop as T  # noqa: E402


def test_cmd_vel_matches_golden_vector():
    pkt = T.encode_cmd_vel(7, 150, -500, T.ENABLE | T.ARM_UP, 300, key=0x12345678)
    assert pkt == T.GOLDEN_CMD_VEL
    typ, seq, key, body = T.parse(pkt)
    assert (typ, seq, key) == (T.CMD_VEL, 7, 0x12345678)
    assert body == {"linear_mm_s": 150, "angular_mrad_s": -500, "buttons": T.ENABLE | T.ARM_UP, "lease_ms": 300}


def test_twist_units():
    typ, _, _, body = T.parse(T.twist_to_cmd_vel(1, 0.15, -0.5))
    assert body["linear_mm_s"] == 150 and body["angular_mrad_s"] == -500 and body["buttons"] == T.ENABLE


def test_values_saturate_instead_of_wrapping():
    _, _, _, body = T.parse(T.encode_cmd_vel(1, 99999, -99999))
    assert body["linear_mm_s"] == 32767 and body["angular_mrad_s"] == -32768


def test_rejects_bad_packets():
    assert T.parse(b"XT" + T.GOLDEN_CMD_VEL[2:]) is None
    assert T.parse(T.GOLDEN_CMD_VEL[:15]) is None
    assert T.parse(b"") is None
    assert T.parse(T.encode_hello(3))[0] == T.HELLO


@pytest.mark.parametrize("path", [
    os.path.join(ROOT, "..", "stackchan-mcp-mbot", "firmware", "host_test", "test_mbot_link.cc"),
    os.path.join(ROOT, "remote", "joyc", "test", "golden.txt"),
])
def test_other_implementations_use_the_same_golden_vector(path):
    if not os.path.exists(path):
        pytest.skip("not present: " + path)
    src = open(path, encoding="utf-8").read()
    hexes = "".join(re.findall(r'"([0-9a-f]+)"', src)) if path.endswith(".cc") else src
    assert T.GOLDEN_CMD_VEL.hex() in hexes.replace(" ", "").replace("\n", "")


def test_joyc_encoder_matches_golden(tmp_path):
    import shutil
    import subprocess
    cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        pytest.skip("no C++ compiler")
    exe = tmp_path / "golden"
    subprocess.run([cxx, "-std=c++11", "-o", str(exe), os.path.join(ROOT, "remote", "joyc", "test", "golden_test.cc")],
                   check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
    assert out.stdout.strip() == T.GOLDEN_CMD_VEL.hex()
