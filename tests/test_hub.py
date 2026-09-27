"""Host-side validation + end-to-end: MbotLink -> F3 bytes -> real runtime (fake CyberPiOS) -> back."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from fake_cyberpi import load_runtime  # noqa: E402
from stacky_hub import commands as C  # noqa: E402
from stacky_hub import limits as L  # noqa: E402
from stacky_hub.link import MbotLink  # noqa: E402
from stacky_hub.transports import f3  # noqa: E402


# ---------- host-side validation ----------
def test_move_clamps_and_explains():
    text, note = C.move("forward", 250, 60)
    assert text == "fwd 100 5"
    assert "clamped" in note


def test_move_rejects_unknown_direction():
    with pytest.raises(C.CommandError):
        C.move("up", 40, 1)


@pytest.mark.parametrize("pos,deg", [("up", 120), ("down", 40), ("home", 90), (200, 120), (0, 40)])
def test_arm(pos, deg):
    assert C.arm(pos)[0] == f"arm {deg}"


@pytest.mark.parametrize("pos,deg", [("open", 120), ("close", 45), ("closed", 45), (10, 45), (500, 120)])
def test_gripper(pos, deg):
    assert C.gripper(pos)[0] == f"grip {deg}"


def test_program_normalizes_and_limits():
    text, _ = C.program("forward 200 9; wait 1; arm up; gripper close")
    assert text == "prog fwd 100 5; wait 1; arm 120; grip 45"
    with pytest.raises(C.CommandError):
        C.program("; ".join(["forward 50 5"] * 7))            # 35 s
    with pytest.raises(C.CommandError):
        C.program("; ".join(["led 0 0 0"] * (L.MAX_PROG_STEPS + 1)))
    with pytest.raises(C.CommandError):
        C.program("forward 40 1; rm -rf /")


# ---------- end-to-end over the real framing ----------
class LoopbackTransport:
    """Delivers host frames to the runtime like CyberPiOS would, and the runtime's
    upload_broadcast.set() back to the host as real F3 frames (with the mBot's spaced JSON)."""

    def __init__(self, rt):
        self.rt = rt
        self.connected = False
        self.on_bytes = None
        self.drop_next = 0
        self.dec = f3.Decoder()
        ub = rt._fake.cyberpi.upload_broadcast
        orig = ub.set

        def set_and_emit(topic, value):
            orig(topic, value)
            body = json.dumps({"message": topic, "value": value})  # spaced, like the real mBot
            payload = bytes([f3.TYPE_UPLOAD_MSG]) + body.encode() + b"\x00"
            self.on_bytes(f3._wrap(payload))
        ub.set = set_and_emit

    async def connect(self):
        self.connected = True

    async def write(self, data):
        for fr in self.dec.feed(data):
            if self.drop_next:
                self.drop_next -= 1
                continue
            msg = json.loads(fr.text)
            cp = self.rt._fake.cyberpi
            cp.upload_broadcast.values[msg["message"]] = msg["value"]
            cp._handlers[msg["message"]]()
            self.rt.tick()

    async def close(self):
        self.connected = False


@pytest.fixture
def pair():
    rt = load_runtime()
    return rt, MbotLink(LoopbackTransport(rt))


def test_e2e_move_ack(pair):
    rt, link = pair

    async def go():
        ok, _ = await link.command(C.move("forward", 30, 1)[0])
        await link.close()
        return ok
    assert asyncio.run(go())
    assert rt.motion["kind"] == "fwd"


def test_e2e_locked_is_reported(pair):
    rt, link = pair
    rt.locked = True

    async def go():
        r = await link.command("fwd 30 1")
        await link.close()
        return r
    assert asyncio.run(go()) == (False, "locked")


def test_e2e_retry_after_lost_frame_does_not_double_execute(pair):
    rt, link = pair

    async def go():
        ok1 = await link.command("fwd 30 1")
        # simulate: the ack of the next command is lost -> host resends same id -> "dup"
        orig = link.t.on_bytes
        dropped = {"n": 0}

        def drop_first_ack(data):
            if b"sk_ack" in data and dropped["n"] == 0:
                dropped["n"] = 1
                return
            orig(data)
        link.t.on_bytes = drop_first_ack
        ok2 = await link.command("left 30 1")
        await link.close()
        return ok1, ok2
    import stacky_hub.limits as lim
    lim.ACK_TIMEOUT_S, saved = 0.05, lim.ACK_TIMEOUT_S
    try:
        (ok1, _), (ok2, detail2) = asyncio.run(go())
    finally:
        lim.ACK_TIMEOUT_S = saved
    assert ok1 and ok2 and detail2 == "dup"
    lefts = [e for e in rt._fake.log if e[0] == "drive" and e[1] < 0 and e[2] < 0]
    assert len(lefts) == 1


def test_e2e_read_sensors(pair):
    rt, link = pair

    async def go():
        s = await link.read_sensors()
        await link.close()
        return s
    s = asyncio.run(go())
    assert s["dist"] == "300" and s["arm"] == "120" and s["locked"] == "0"


# ---------- v1.1 end-to-end: explorer -> LinkRobot -> MbotLink -> F3 -> runtime physics ----------
async def _clock_pump(rt, stop):
    """Run the runtime's main loop in (fake) real time, like the CyberPi does."""
    while not stop.is_set():
        await asyncio.sleep(0.01)
        rt._fake.clock.ms += 10
        rt.tick()


def test_e2e_v11_step_turn_sync_through_real_stack(pair):
    rt, link = pair

    async def go():
        stop = asyncio.Event()
        pump = asyncio.create_task(_clock_pump(rt, stop))
        try:
            d1, _ = await link.motion(C.step(12, 60)[0])
            d2, _ = await link.motion(C.turn(-90, 60)[0])
            rtt, off = await link.sync()
        finally:
            stop.set()
            await pump
            await link.close()
        return d1, d2, rtt
    d1, d2, rtt = asyncio.run(go())
    assert d1.reason == "target" and 11.5 <= d1.dist_cm <= 13.0
    assert d2.reason == "target" and abs(d2.yaw_deg + 90) <= 3.5
    assert rtt is not None and rtt < 0.5


def test_e2e_explorer_drives_real_runtime(pair):
    from stacky_hub.adapters import LinkRobot
    from stacky_hub.explore import ExploreConfig, Explorer
    rt, link = pair

    async def go():
        stop = asyncio.Event()
        pump = asyncio.create_task(_clock_pump(rt, stop))
        ex = Explorer(LinkRobot(link), None, ExploreConfig(max_steps=3, step_cm=10, speed_percent=50,
                                                           turn_speed_percent=50, look_every=0))
        try:
            st = await ex.run()
        finally:
            stop.set()
            await pump
            await link.close()
        return ex, st
    ex, st = asyncio.run(go())
    assert st["state"] == "finished" and st["steps"] == 3
    # explorer's pose (from the robot's measured outcomes) matches the simulated physics
    assert abs(ex.pose.x - rt._fake.phys.x) < 1.0
    assert rt.motion is None                       # robot left stopped
