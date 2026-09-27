"""Safety tests for the mBot-side runtime, run on the PC against fake CyberPiOS modules."""
import pytest

from fake_cyberpi import last, load_runtime, send


@pytest.fixture
def rt():
    return load_runtime()


def drives(rt):
    return [e for e in rt._fake.log if e[0] == "drive"]


def test_forward_is_clamped_to_max_rpm_and_mirrored(rt):
    send(rt, "1 fwd 500 1")
    assert last(rt, "sk_ack") == "1 ok "
    l, r = drives(rt)[-1][1:]
    assert l == rt.MAX_RPM and r == -rt.MAX_RPM


@pytest.mark.parametrize("cmd,sign", [("fwd", (1, -1)), ("back", (-1, 1)), ("right", (1, 1)), ("left", (-1, -1))])
def test_direction_signs(rt, cmd, sign):
    send(rt, "1 %s 50 1" % cmd)
    l, r = drives(rt)[-1][1:]
    assert (l > 0) - (l < 0) == sign[0] and (r > 0) - (r < 0) == sign[1]


def test_motion_duration_is_capped(rt):
    send(rt, "1 fwd 40 999")
    end_s = rt.motion["end"] / 1000.0
    assert end_s == rt.MAX_MOVE_S


def test_negative_speed_cannot_reverse_direction(rt):
    send(rt, "1 fwd -100 1")
    l, r = drives(rt)[-1][1:]
    assert l == 0 and r == 0


@pytest.mark.parametrize("arg,expected", [("up", 120), ("down", 40), ("home", 90), ("10", 40), ("179", 120), ("75", 75)])
def test_arm_limits(rt, arg, expected):
    send(rt, "1 arm " + arg)
    assert rt.servo[rt.ARM_PORT][1] == expected


@pytest.mark.parametrize("arg,expected", [("open", 120), ("close", 45), ("home", 90), ("0", 45), ("180", 120)])
def test_gripper_limits(rt, arg, expected):
    send(rt, "1 grip " + arg)
    assert rt.servo[rt.GRIP_PORT][1] == expected


def run(rt, seconds, hb=True):
    """Advance the fake clock in 10 ms ticks; optionally keep the host heartbeat alive."""
    for i in range(int(seconds * 100)):
        rt._fake.clock.ms += 10
        if hb and i % 100 == 0:
            send(rt, str(i), topic="sk_hb")
        rt.tick()


def test_servo_glides_never_jumps(rt):
    send(rt, "1 arm down")          # 120 -> 40
    run(rt, 2)
    sets = [e[2] for e in rt._fake.log if e[0] == "servo" and e[1] == rt.ARM_PORT]
    assert sets[-1] == 40
    assert all(abs(a - b) <= rt.SERVO_STEP_DEG for a, b in zip([120] + sets, sets))
    assert all(rt.ARM_MIN <= a <= rt.ARM_MAX for a in sets)


def test_motion_ends_on_time_and_reports_done(rt):
    send(rt, "1 fwd 40 1")
    run(rt, 1.2)
    assert rt.motion is None
    assert drives(rt)[-1] == ("drive", 0, 0)
    assert any(e.startswith("done 1 time ") for e in rt._fake.sent["sk_evt"])


def test_watchdog_stops_when_host_goes_silent(rt):
    send(rt, "1 prog fwd 40 5; fwd 40 5; fwd 40 5")
    run(rt, rt.WATCHDOG_S + 0.5, hb=False)
    assert rt.program is None and rt.motion is None
    assert "stopped watchdog" in rt._fake.sent["sk_evt"]


def test_heartbeat_keeps_program_running(rt):
    send(rt, "1 prog fwd 40 5; fwd 40 5")
    run(rt, rt.WATCHDOG_S + 2)
    assert rt.program is not None


def test_obstacle_stops_forward(rt):
    send(rt, "1 fwd 40 5")
    run(rt, 0.3)
    rt._fake.mbuild.distance = 6.0
    run(rt, 0.3)
    assert rt.motion is None
    assert "obstacle 6" in rt._fake.sent["sk_evt"]


def test_obstacle_does_not_block_backing_away(rt):
    rt._fake.mbuild.distance = 6.0
    send(rt, "1 back 40 1")
    run(rt, 0.5)
    assert rt.motion is not None


def test_program_runs_all_steps_in_order(rt):
    send(rt, "1 prog led 0 255 0; fwd 40 0.5; wait 0.5; arm up; led 0 0 0")
    run(rt, 4)
    assert rt.program is None
    assert any(e.startswith("done 1 prog ") for e in rt._fake.sent["sk_evt"])
    leds = [e for e in rt._fake.log if e[0] == "led"]
    assert leds[0][1:4] == (0, 255, 0) and leds[-1][1:4] == (0, 0, 0)
    assert rt.servo[rt.ARM_PORT][0] == 120


def test_button_b_locks_and_stops(rt):
    send(rt, "1 fwd 40 5")
    rt._fake.cyberpi.controller.is_press = lambda k: k == "b"
    rt.tick()
    assert rt.locked and rt.motion is None
    rt._fake.cyberpi.controller.is_press = lambda k: False
    rt.tick()
    send(rt, "2 fwd 40 1")
    assert last(rt, "sk_ack") == "2 err locked"
    rt._fake.cyberpi.controller.is_press = lambda k: k == "a"
    rt.tick()
    assert not rt.locked


def test_led_values_clamped(rt):
    send(rt, "1 led 999 -5 128 9")
    assert rt._fake.log[-1] == ("led", 255, 0, 128, 5)


def test_unknown_command_rejected(rt):
    send(rt, "1 selfdestruct")
    assert last(rt, "sk_ack") == "1 err unknown_cmd"
    assert not drives(rt)


def test_locked_blocks_motion_but_not_stop(rt):
    rt.locked = True
    send(rt, "1 fwd 40 1")
    assert last(rt, "sk_ack") == "1 err locked"
    assert not drives(rt)
    send(rt, "2 stop")
    assert last(rt, "sk_ack") == "2 ok "


def test_duplicate_id_is_not_executed_twice(rt):
    send(rt, "7 fwd 40 1")
    n = len(drives(rt))
    send(rt, "7 fwd 40 1")
    assert last(rt, "sk_ack") == "7 ok dup"
    assert len(drives(rt)) == n


def test_program_step_limit(rt):
    send(rt, "1 prog " + "; ".join(["led 0 0 0"] * (rt.MAX_PROG_STEPS + 1)))
    assert last(rt, "sk_ack") == "1 err too_many_steps"
    assert rt.program is None


def test_program_time_limit_uses_clamped_durations(rt):
    # 7 moves x 5 s (each clamped) = 35 s > 30 s
    send(rt, "1 prog " + "; ".join(["fwd 50 99"] * 7))
    assert last(rt, "sk_ack") == "1 err too_long"


def test_program_with_bad_step_rejected_whole(rt):
    send(rt, "1 prog fwd 40 1; explode; led 0 255 0")
    assert last(rt, "sk_ack") == "1 err step_unknown_cmd"
    assert not drives(rt)


def test_program_accepted(rt):
    send(rt, "1 prog fwd 40 1; wait 0.5; arm up; grip close; led 0 255 0")
    assert last(rt, "sk_ack").startswith("1 ok 5_steps")
    assert rt.program is not None


def test_wait_only_inside_programs(rt):
    send(rt, "1 wait 3")
    assert last(rt, "sk_ack") == "1 err wait_only_in_prog"


def test_stop_cancels_program_and_motion(rt):
    send(rt, "1 prog fwd 40 2; back 40 2")
    rt.start_step(*rt.program["steps"][0][:2], cid="1")
    send(rt, "2 stop")
    assert rt.motion is None and rt.program is None
    assert drives(rt)[-1] == ("drive", 0, 0)
    assert "stopped cmd" in rt._fake.sent["sk_evt"]


def test_new_motion_replaces_current(rt):
    send(rt, "1 fwd 40 5")
    send(rt, "2 left 40 1")
    assert rt.motion["kind"] == "left" and rt.motion["id"] == "2"


def test_read_reports_state(rt):
    send(rt, "1 read")
    assert last(rt, "sk_evt") == "sensors dist=300 batt=90 arm=120 grip=120 locked=0 l=0.0 r=0.0 yaw=0.0 t=0"


def test_heartbeat_topic_updates_last_rx(rt):
    rt._fake.clock.ms = 5000
    send(rt, "1", topic="sk_hb")
    assert rt.last_rx == 5000


def test_stop_freezes_gliding_servo(rt):
    send(rt, "1 grip close")        # 120 -> 45, takes ~0.8 s
    run(rt, 0.3)
    mid = rt.servo[rt.GRIP_PORT][0]
    assert rt.GRIP_MIN < mid < rt.GRIP_MAX
    send(rt, "2 stop")
    run(rt, 1)
    assert rt.servo[rt.GRIP_PORT] == [mid, mid]
    assert "stopped cmd" in rt._fake.sent["sk_evt"]


# ---------------- v1.1: closed-loop exploration primitives ----------------
def done_evt(rt, cid):
    evts = [e for e in rt._fake.sent.get("sk_evt", []) if e.startswith("done %s " % cid)]
    assert evts, rt._fake.sent.get("sk_evt")
    parts = evts[-1].split()
    kv = dict(p.split("=", 1) for p in parts[3:])
    return parts[2], kv


def test_straight_stops_at_distance_by_encoders(rt):
    send(rt, "1 straight 20 30")
    run(rt, 5)
    reason, kv = done_evt(rt, "1")
    assert reason == "target"
    assert 19.5 <= float(kv["dist"]) <= 21.0          # closed loop, small overshoot only
    assert abs(rt._fake.phys.x - float(kv["dist"])) < 0.6


def test_straight_backwards(rt):
    send(rt, "1 straight -10 30")
    run(rt, 4)
    reason, kv = done_evt(rt, "1")
    assert reason == "target" and -10.8 <= float(kv["dist"]) <= -9.5


@pytest.mark.parametrize("deg", [45, -90, 170])
def test_turn_uses_gyro_and_handles_wrap(rt, deg):
    rt._fake.phys.yaw = 150.0          # start near the +-180 wrap of the IMU
    send(rt, "1 turn %d 40" % deg)
    run(rt, 5)
    reason, kv = done_evt(rt, "1")
    assert reason == "target"
    assert abs(float(kv["yaw"]) - deg) <= 3.5


def test_step_and_turn_are_clamped(rt):
    send(rt, "1 straight 500 100")
    assert rt.motion["target"] == rt.MAX_STEP_CM
    send(rt, "2 turn -999 100")
    assert rt.motion["target"] == rt.MAX_TURN_DEG and rt.motion["dir"] == "right"


def test_straight_obstacle_reports_actual_distance(rt):
    send(rt, "1 straight 30 30")
    run(rt, 1.0)
    rt._fake.mbuild.distance = 7.0
    run(rt, 0.3)
    reason, kv = done_evt(rt, "1")
    assert reason == "obstacle" and 3 < float(kv["dist"]) < 30
    assert rt.motion is None


def test_stop_reports_partial_motion(rt):
    send(rt, "1 turn 90 30")
    run(rt, 0.5)
    send(rt, "2 stop")
    reason, kv = done_evt(rt, "1")
    assert reason == "cmd" and 0 < float(kv["yaw"]) < 90


def test_watchdog_reports_partial_motion(rt):
    send(rt, "1 straight 30 20")
    run(rt, rt.WATCHDOG_S + 0.5, hb=False)
    reason, _ = done_evt(rt, "1")
    assert reason == "watchdog"


def test_timeout_without_encoders_is_timed_fallback(rt):
    rt._fake.phys.em_ports = ("X", "Y")   # encoders unreadable
    rt.EM_PORTS = None
    send(rt, "1 straight 10 30")
    run(rt, 5)
    reason, kv = done_evt(rt, "1")
    assert reason == "timeout" and kv["dist"] == "none"


def test_sync_returns_robot_clock(rt):
    rt._fake.clock.ms = 12345
    send(rt, "7 sync 999")
    assert last(rt, "sk_ack") == "7 ok 999 12345"


def test_odom_stream(rt):
    send(rt, "1 odom 200")
    send(rt, "2 straight 10 30")
    run(rt, 2)
    od = [e for e in rt._fake.sent["sk_evt"] if e.startswith("odom ")]
    assert len(od) >= 8
    assert od[-1].startswith("odom t=") and " l=" in od[-1] and " yaw=" in od[-1]
    send(rt, "3 odom 0")
    n = len([e for e in rt._fake.sent["sk_evt"] if e.startswith("odom ")])
    run(rt, 1)
    assert len([e for e in rt._fake.sent["sk_evt"] if e.startswith("odom ")]) == n


def test_sync_and_read_work_while_locked(rt):
    rt.locked = True
    send(rt, "1 sync 5")
    assert last(rt, "sk_ack").startswith("1 ok")
    send(rt, "2 straight 10 30")
    assert last(rt, "sk_ack") == "2 err locked"


def test_program_with_closed_loop_steps(rt):
    send(rt, "1 prog straight 10 30; turn 90 40; straight 5 30")
    run(rt, 12)
    assert rt.program is None
    assert rt._fake.phys.yaw == pytest.approx(90, abs=4)
