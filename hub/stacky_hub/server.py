"""stacky-hub: MCP server that lets an LLM drive the mBot2 safely (Mac → BLE/USB → mBot).

Same tool names as the stackchan-mcp gateway's mbot_* tools (Stack-chan firmware path),
so prompts work with either. Run:  python -m stacky_hub [--usb | --ble-address XX]
Requires stacky_runtime.py running on the mBot (upload it once from mBlock).
"""
import argparse
import asyncio
import json
import logging
import os
import time

from mcp.server.fastmcp import FastMCP, Image

from . import commands as C
from . import limits as L
from .link import MbotLink

LIMITS = (f"Safety limits (enforced on host AND robot): speed 0-100% of {L.MAX_RPM} rpm, "
          f"max {L.MAX_MOVE_S:g}s per motion, arm {L.ARM_MIN}-{L.ARM_MAX}° (down..up, home {L.ARM_HOME}), "
          f"gripper {L.GRIP_MIN}-{L.GRIP_MAX}° (closed..open, home {L.GRIP_HOME}), "
          f"programs max {L.MAX_PROG_STEPS} steps / {L.MAX_PROG_S:g}s. Forward motion auto-stops "
          f"at obstacles < {L.OBSTACLE_CM} cm. A child may press button B on the robot to lock it; "
          "then motion is refused until they press A.")

mcp = FastMCP("stacky-hub", instructions=(
    "Controls a Makeblock mBot2 robot (wheels, 5 LEDs, arm and gripper) that plays with a child "
    "and the Stack-chan robot. Prefer slow speeds (30-50%) and short motions. Call mbot_stop "
    "immediately if anyone says stop / 止まって / とまれ. " + LIMITS))

link: MbotLink = None  # set in main()
ARGS = None            # parsed command line (robot backend, gateway URL, head calibration)
EXPLORE = {"explorer": None, "task": None, "gw": None}


async def _run(text, note):
    try:
        ok, detail = await link.command(text)
    except Exception as e:
        return f"mBot not reachable: {e}"
    if ok:
        return f"OK: {note}" + (f" ({detail})" if detail else "")
    hint = {"locked": "The child locked the robot with button B; ask them to press A to unlock.",
            "no_ack": "The robot did not answer; is stacky_runtime running on it?"}.get(detail, "")
    return f"REFUSED by robot: {detail}. {hint}".strip()


def _err(e):
    return f"Invalid request: {e}. {LIMITS}"


@mcp.tool()
async def mbot_status() -> str:
    """Connection state of the mBot, the last sensor reading and the most recent robot events."""
    try:
        await link.ensure_connected()
    except Exception as e:
        return f"disconnected: {e}"
    recent = [f"{time.strftime('%H:%M:%S', time.localtime(t))} {e}" for t, e in list(link.events)[-8:]]
    return f"connected. last sensors: {link.last_sensors}. recent events: {recent or 'none'}"


@mcp.tool()
async def mbot_move(direction: str, speed_percent: float = 40, seconds: float = 1.0) -> str:
    """Drive the mBot. direction: forward | backward | left | right (left/right turn in place).
    speed_percent 0-100 (100% = 60 rpm, about 20 cm/s; prefer 30-50), seconds 0.1-5.
    Returns when the robot accepted the motion; it stops by itself after `seconds`."""
    try:
        text, note = C.move(direction, speed_percent, seconds)
    except C.CommandError as e:
        return _err(e)
    return await _run(text, note)


@mcp.tool()
async def mbot_stop() -> str:
    """Stop the mBot immediately (wheels and any running program). Always allowed."""
    return await _run("stop", "stopped")


@mcp.tool()
async def mbot_set_led(r: int, g: int, b: int, index: str = "all") -> str:
    """Set the CyberPi LEDs. r, g, b 0-255. index: 'all' or 1-5."""
    try:
        text, note = C.led(r, g, b, index)
    except C.CommandError as e:
        return _err(e)
    return await _run(text, note)


@mcp.tool()
async def mbot_arm(position: str) -> str:
    """Move the arm (servo S4): 'up' (120°), 'down' (40°), 'home' (90°) or an angle 40-120. Moves smoothly."""
    try:
        text, note = C.arm(position)
    except C.CommandError as e:
        return _err(e)
    return await _run(text, note)


@mcp.tool()
async def mbot_gripper(position: str) -> str:
    """Open/close the gripper (servo S3): 'open' (120°), 'close' (45°), 'home' (90°) or an angle 45-120."""
    try:
        text, note = C.gripper(position)
    except C.CommandError as e:
        return _err(e)
    return await _run(text, note)


@mcp.tool()
async def mbot_home() -> str:
    """Put arm and gripper in their neutral position (90°)."""
    return await _run("home", "arm and gripper -> 90°")


@mcp.tool()
async def mbot_read_sensors() -> str:
    """Read distance to the nearest obstacle (cm), battery %, arm/gripper angles and lock state."""
    try:
        s = await link.read_sensors()
    except Exception as e:
        return f"mBot not reachable: {e}"
    return f"sensors: {s}" if s else "no sensor reply"


@mcp.tool()
async def mbot_run_program(steps: str) -> str:
    """Run a small safe program on the mBot. Steps separated by ';'. Allowed steps:
    'forward|backward|left|right <speed%> <seconds>', 'wait <seconds>', 'led <r> <g> <b> [all|1-5]',
    'arm up|down|home|<deg>', 'grip open|close|home|<deg>', 'home'.
    Example: 'led 0 255 0; forward 40 1; arm down; grip close; arm up; backward 40 1'.
    Max 20 steps and 30 s total; the whole program is rejected if a step is invalid. mbot_stop aborts it."""
    try:
        text, note = C.program(steps)
    except C.CommandError as e:
        return _err(e)
    return await _run(text, note)


@mcp.tool()
async def mbot_step(distance_cm: float, speed_percent: float = 30) -> str:
    """Drive straight BY DISTANCE, closed-loop on the wheel encoders (protocol v1.1).
    distance_cm -30..30 (negative = backwards), speed_percent 5-100 (default 30, slow).
    Waits until the robot reports the outcome: target / obstacle / timeout / stopped, with the real distance."""
    try:
        text, note = C.step(distance_cm, speed_percent)
        done, detail = await link.motion(text)
    except C.CommandError as e:
        return _err(e)
    except Exception as e:
        return f"mBot not reachable: {e}"
    if done is None:
        return f"REFUSED by robot: {detail}"
    return f"{note}: {done.reason}, drove {done.dist_cm} cm, heading change {done.yaw_deg}°"


@mcp.tool()
async def mbot_turn(degrees: float, speed_percent: float = 30) -> str:
    """Turn in place BY ANGLE, closed-loop on the gyro (protocol v1.1). degrees -180..180,
    + = LEFT (counter-clockwise), - = right. Waits for the outcome with the real angle turned."""
    try:
        text, note = C.turn(degrees, speed_percent)
        done, detail = await link.motion(text)
    except C.CommandError as e:
        return _err(e)
    except Exception as e:
        return f"mBot not reachable: {e}"
    if done is None:
        return f"REFUSED by robot: {detail}"
    return f"{note}: {done.reason}, turned {done.yaw_deg}°"


@mcp.tool()
async def mbot_sync() -> str:
    """Measure link round-trip time and the robot's clock offset."""
    try:
        rtt, off = await link.sync()
    except Exception as e:
        return f"mBot not reachable: {e}"
    return json.dumps({"rtt_ms": None if rtt is None else round(rtt * 1000), "clock_offset_ms": off})


# ------------------------------------------------------------------ exploration
async def _make_backend():
    from .adapters import GatewayClient, GatewayEyes, GatewayRobot, LinkRobot
    gw = None
    if ARGS.gateway_url and (ARGS.robot == "gateway" or not ARGS.no_camera):
        gw = GatewayClient(ARGS.gateway_url, ARGS.gateway_token or os.environ.get("STACKCHAN_TOKEN"))
        await gw.__aenter__()
    robot = GatewayRobot(gw) if ARGS.robot == "gateway" else LinkRobot(link)
    eyes = None if (gw is None or ARGS.no_camera) else GatewayEyes(gw, pitch=ARGS.head_pitch, yaw_sign=ARGS.head_yaw_sign)
    return robot, eyes, gw


@mcp.tool()
async def explore_start(max_steps: int = 40, max_minutes: float = 10, step_cm: float = 20,
                        speed_percent: float = 30, look_every: int = 3) -> str:
    """Start slow autonomous exploration with Stack-chan riding on the mBot.
    Loop: STOP -> LOOK (head pans left/centre/right, photos with exact pose) -> DECIDE (local rules:
    go forward into new open space, scan when blocked) -> ONE bounded step (<= 30 cm or one turn) -> repeat.
    Runs in the background; poll explore_status to see photos and progress, steer with explore_set_goal,
    end with explore_stop. step_cm 5-30, speed_percent 10-50, look_every = photos every N steps."""
    from .explore import ExploreConfig, Explorer
    if EXPLORE["task"] and not EXPLORE["task"].done():
        return "already exploring; call explore_stop first"
    try:
        robot, eyes, gw = await _make_backend()
    except Exception as e:
        return f"cannot start: {e}"
    cfg = ExploreConfig(max_steps=max(1, min(int(max_steps), 500)), max_seconds=max(10, min(max_minutes, 60)) * 60,
                        step_cm=max(5, min(step_cm, L.MAX_STEP_CM)), speed_percent=max(10, min(speed_percent, 50)),
                        turn_speed_percent=max(10, min(speed_percent, 50)), look_every=max(0, int(look_every)))
    ex = Explorer(robot, eyes, cfg)

    async def runner():
        try:
            return await ex.run()
        finally:
            if gw:
                await gw.__aexit__(None, None, None)

    EXPLORE.update(explorer=ex, task=asyncio.create_task(runner()), gw=gw)
    return (f"exploring: up to {cfg.max_steps} steps / {cfg.max_seconds / 60:g} min, steps of {cfg.step_cm:g} cm "
            f"at {cfg.speed_percent:g}%, camera {'on' if eyes else 'off'}. Poll explore_status.")


@mcp.tool()
async def explore_status(photos: int = 3):
    """Exploration progress: state, pose (x,y cm; th deg, relative to the start), goal, visited cells,
    per-step latency, recent log, and the most recent photos (with observation ids and the pose each was
    taken from). Use an observation id with explore_set_goal to steer toward something you see."""
    ex = EXPLORE["explorer"]
    if ex is None:
        return "not exploring (call explore_start)"
    st = ex.status(last_obs=max(0, min(photos, 6)))
    out = [json.dumps(st, indent=1)]
    for o in st["observations"]:
        path = o.get("image")
        if path and os.path.isfile(path) and os.path.getsize(path) < 200_000:
            out.append(Image(path=path))
    return out


@mcp.tool()
async def explore_set_goal(observation_id: int, bearing_deg: float = 0, distance_cm: float = 0, note: str = "") -> str:
    """Steer exploration toward something seen in a photo. observation_id from explore_status;
    bearing_deg = where the thing is relative to the photo's centre (+ = left, the camera sees about
    +-30 deg); distance_cm = rough distance (0 = just face that direction). The goal is anchored to where
    the robot was when it took that photo, so it stays correct even though the robot kept moving."""
    ex = EXPLORE["explorer"]
    if ex is None:
        return "not exploring"
    try:
        g = ex.set_goal_from_observation(observation_id, bearing_deg, distance_cm or None, note)
    except ValueError as e:
        return str(e)
    return f"goal set: heading {g.heading:.0f}°" + (f", target ({g.target[0]:.0f}, {g.target[1]:.0f}) cm" if g.target else "")


@mcp.tool()
async def explore_clear_goal() -> str:
    """Drop the current goal and go back to free exploration."""
    ex = EXPLORE["explorer"]
    if ex:
        ex.clear_goal()
    return "goal cleared"


@mcp.tool()
async def explore_stop(reason: str = "requested") -> str:
    """Stop exploring now (the robot stops after, at most, the current bounded step; mbot_stop is instant)."""
    ex, task = EXPLORE["explorer"], EXPLORE["task"]
    if ex is None or task is None:
        return "not exploring"
    ex.request_stop(reason)
    try:
        st = await asyncio.wait_for(asyncio.shield(task), 8)
    except asyncio.TimeoutError:
        await link.stop()
        return "stop requested; robot told to stop"
    return f"stopped after {st['steps']} steps, {st['cells_visited']} cells visited, pose {st['pose']}"


def main():
    global link
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--usb", action="store_true", help="use USB instead of BLE (quit mBlock first)")
    ap.add_argument("--port", help="USB serial port")
    ap.add_argument("--ble-address", help="BLE address/UUID of a specific mBot")
    ap.add_argument("--robot", choices=["link", "gateway"], default="link",
                    help="exploration drives the mBot via this server's own link, or via Stack-chan (gateway)")
    ap.add_argument("--gateway-url", default="http://127.0.0.1:8767/mcp",
                    help="stackchan-mcp daemon (serve --transport streamable-http) for camera/head")
    ap.add_argument("--gateway-token", default=None)
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--head-pitch", type=int, default=45, help="camera tilt while exploring (5..85)")
    ap.add_argument("--head-yaw-sign", type=int, choices=[-1, 1], default=1,
                    help="-1 if move_head(yaw>0) turns the head to the RIGHT")
    args = ap.parse_args()
    global ARGS
    ARGS = args
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.usb:
        from .transports.usb import UsbTransport
        transport = UsbTransport(args.port)
    else:
        from .transports.ble import BleTransport
        transport = BleTransport(address=args.ble_address)
    link = MbotLink(transport)
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
