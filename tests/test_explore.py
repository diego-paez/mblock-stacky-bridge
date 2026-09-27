"""Explorer behaviour in a simulated room: safety, coverage, delay compensation."""
import asyncio
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from sim_world import SimEyes, SimRobot, SimWorld  # noqa: E402
from stacky_hub.explore import ExploreConfig, Explorer, Pose, wrap180  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make(delay=0.0, noise=0.0, **cfg):
    world = SimWorld()
    robot = SimRobot(world, delay_s=delay, yaw_noise=noise)
    eyes = SimEyes(robot)
    ex = Explorer(robot, eyes, ExploreConfig(**cfg))
    return world, robot, eyes, ex


def test_pose_integration_matches_geometry():
    p = Pose()
    p.apply(20, 0)
    p.apply(0, 90)
    p.apply(10, 0)
    assert (round(p.x, 6), round(p.y, 6), round(p.th, 6)) == (20, 10, 90)


def test_explores_without_collision_and_within_limits():
    world, robot, eyes, ex = make(max_steps=80)
    status = run(ex.run())
    assert status["state"] in ("finished", "stuck")
    assert robot.min_clearance > 0.0                       # body never touched a wall or the box
    assert status["cells_visited"] >= 12                   # actually covered the room
    assert all(abs(v) <= 30 for k, v in robot.commands if k == "straight")
    assert all(abs(v) <= 180 for k, v in robot.commands if k == "turn")
    assert robot.stopped >= 1                              # always ends with an explicit stop


def test_dead_reckoning_tracks_true_pose():
    world, robot, eyes, ex = make(max_steps=40)
    start = (robot.x, robot.y, robot.th)
    run(ex.run())
    # explorer pose is relative to the start pose; rotate into world frame
    c, s = math.cos(math.radians(start[2])), math.sin(math.radians(start[2]))
    wx = start[0] + c * ex.pose.x - s * ex.pose.y
    wy = start[1] + s * ex.pose.x + c * ex.pose.y
    assert math.hypot(wx - robot.x, wy - robot.y) < 1.0
    assert abs(wrap180(start[2] + ex.pose.th - robot.th)) < 1.0


def test_photos_are_taken_while_stopped_with_exact_pose():
    world, robot, eyes, ex = make(max_steps=10, look_every=2)
    run(ex.run())
    assert ex.observations, "no observations"
    assert {o.head_yaw for o in ex.observations} == {-50.0, 0.0, 50.0}
    # each observation's pose equals the robot's true pose at photo time (robot started at 40,40,0)
    for o, (x, y, th, hy) in zip(ex.observations, eyes.photos):
        assert math.hypot(40 + o.pose.x - x, 40 + o.pose.y - y) < 1e-6 and o.head_yaw == hy


def test_goal_from_old_photo_is_compensated_for_later_motion():
    """The LLM answers about photo #k only after the robot has moved on.
    The goal must still point where the photo said, in the world frame."""
    world, robot, eyes, ex = make(max_steps=6, look_every=1)
    run(ex.run())                                   # robot wanders, takes photos
    old = ex.observations[1]                        # a photo taken a while ago (head yaw 0)
    # "in that photo, the thing is 30 deg left of centre, 60 cm away"
    ex.set_goal_from_observation(old.id, bearing_in_image_deg=30, distance_cm=60, note="red box")
    expected = old.pose.point_ahead(60, old.head_yaw + 30)
    assert ex.goal.target == pytest.approx(expected)
    # The world point must not depend on where the robot is now:
    ex.pose.apply(15, 70)
    assert ex.goal.target == pytest.approx(expected)


def test_drives_to_goal_point():
    world, robot, eyes, ex = make(max_steps=30, look_every=0)
    ex.set_goal_relative(bearing_deg=60, distance_cm=70, note="there")
    target = ex.goal.target
    run(ex.run())
    assert ex.last_goal_result, ex.log[-5:]
    p = ex.last_goal_result["pose"]
    assert math.hypot(p.x - target[0], p.y - target[1]) <= 15.0


def test_link_delay_does_not_change_outcome():
    """Same world, same decisions, with 50 ms extra latency on every call."""
    _, r_fast, _, ex_fast = make(max_steps=15, look_every=0)
    _, r_slow, _, ex_slow = make(delay=0.05, max_steps=15, look_every=0)
    run(ex_fast.run())
    run(ex_slow.run())
    assert r_fast.commands == r_slow.commands
    assert ex_slow.status()["latency"]["step_s"] > ex_fast.status()["latency"]["step_s"]


def test_gyro_noise_is_absorbed_by_measured_outcomes():
    world, robot, eyes, ex = make(noise=3.0, max_steps=40, look_every=0)
    run(ex.run())
    assert robot.min_clearance > 0.0
    assert abs(wrap180(ex.pose.th - robot.th)) < 1.0      # pose uses what the gyro measured


def test_stop_request_stops_robot():
    world, robot, eyes, ex = make(delay=0.01, max_steps=1000, look_every=0)

    async def go():
        task = asyncio.create_task(ex.run())
        await asyncio.sleep(0.2)
        ex.request_stop("child said stop")
        return await task
    st = run(go())
    assert st["state"] == "stopped" and st["stop_reason"] == "child said stop"
    assert robot.stopped >= 1


def test_button_on_robot_ends_exploration():
    world, robot, eyes, ex = make(max_steps=50, look_every=0)
    robot.inject = "button"
    st = run(ex.run())
    assert st["stop_reason"] == "robot:button"
    assert ex.steps <= 1


def test_stuck_in_a_box_reports_stuck():
    world = SimWorld(w=40, h=40, boxes=())
    robot = SimRobot(world, x=20, y=20)
    ex = Explorer(robot, None, ExploreConfig(max_steps=20))
    st = run(ex.run())
    assert st["state"] == "stuck"
    assert robot.min_clearance >= 0
