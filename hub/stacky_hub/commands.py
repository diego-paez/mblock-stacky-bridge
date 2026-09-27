"""Build and validate Stacky v1 command strings on the host (clamped to limits.py).

Every builder returns (command_text, human_note). The note tells the caller (and the
LLM) what will actually happen after clamping, e.g. "speed 150% -> 100%".
"""
from . import limits as L

DIRECTIONS = {
    "forward": "fwd", "fwd": "fwd", "ahead": "fwd",
    "backward": "back", "back": "back", "backwards": "back", "reverse": "back",
    "left": "left", "right": "right",
}
ARM_PRESETS = {"up": L.ARM_MAX, "down": L.ARM_MIN, "home": L.ARM_HOME}
GRIP_PRESETS = {"open": L.GRIP_MAX, "close": L.GRIP_MIN, "closed": L.GRIP_MIN, "home": L.GRIP_HOME}


class CommandError(ValueError):
    pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _num(x, name):
    try:
        return float(x)
    except (TypeError, ValueError):
        raise CommandError(f"{name} must be a number, got {x!r}")


def move(direction, speed_percent=40, seconds=1.0):
    d = DIRECTIONS.get(str(direction).strip().lower())
    if d is None:
        raise CommandError(f"direction must be forward/backward/left/right, got {direction!r}")
    sp = _num(speed_percent, "speed_percent")
    sec = _num(seconds, "seconds")
    csp, csec = _clamp(sp, 0, 100), _clamp(sec, 0.1, L.MAX_MOVE_S)
    notes = []
    if csp != sp:
        notes.append(f"speed {sp:g}% -> {csp:g}%")
    if csec != sec:
        notes.append(f"time {sec:g}s -> {csec:g}s (max {L.MAX_MOVE_S:g}s)")
    rpm = L.MAX_RPM * csp / 100
    note = f"{d} at {csp:g}% ({rpm:.0f} rpm) for {csec:g}s" + (f" [clamped: {', '.join(notes)}]" if notes else "")
    return f"{d} {csp:g} {csec:g}", note


def led(r, g, b, index="all"):
    rgb = [int(_clamp(_num(v, "color"), 0, 255)) for v in (r, g, b)]
    idx = str(index).strip().lower()
    if idx != "all":
        i = int(_clamp(_num(idx, "index"), 1, 5))
        idx = str(i)
    return f"led {rgb[0]} {rgb[1]} {rgb[2]} {idx}", f"LEDs {idx} -> rgb{tuple(rgb)}"


def _servo(name, position, presets, lo, hi, what):
    key = str(position).strip().lower()
    if key in presets:
        deg = presets[key]
    else:
        raw = _num(position, what)
        deg = int(_clamp(raw, lo, hi))
        if deg != raw:
            return f"{name} {deg}", f"{what} {raw:g}° -> {deg}° (allowed {lo}..{hi})"
    return f"{name} {deg}", f"{what} -> {deg}°"


def arm(position):
    return _servo("arm", position, ARM_PRESETS, L.ARM_MIN, L.ARM_MAX, "arm")


def gripper(position):
    return _servo("grip", position, GRIP_PRESETS, L.GRIP_MIN, L.GRIP_MAX, "gripper")


def step(distance_cm, speed_percent=30):
    """v1.1 closed-loop straight move (encoders). + forward, - backward."""
    cm = _num(distance_cm, "distance_cm")
    sp = _num(speed_percent, "speed_percent")
    ccm, csp = _clamp(cm, -L.MAX_STEP_CM, L.MAX_STEP_CM), _clamp(sp, 5, 100)
    note = f"straight {ccm:g} cm at {csp:g}%" + (" [clamped]" if (ccm, csp) != (cm, sp) else "")
    return f"straight {ccm:g} {csp:g}", note


def turn(degrees, speed_percent=30):
    """v1.1 closed-loop turn in place (gyro). + left (CCW), - right."""
    deg = _num(degrees, "degrees")
    sp = _num(speed_percent, "speed_percent")
    cdeg, csp = _clamp(deg, -L.MAX_TURN_DEG, L.MAX_TURN_DEG), _clamp(sp, 5, 100)
    note = f"turn {cdeg:g}° ({'left' if cdeg >= 0 else 'right'}) at {csp:g}%" + (
        " [clamped]" if (cdeg, csp) != (deg, sp) else "")
    return f"turn {cdeg:g} {csp:g}", note


def odom(period_ms):
    ms = _num(period_ms, "period_ms")
    ms = 0 if ms <= 0 else int(_clamp(ms, 100, 5000))
    return f"odom {ms}", f"odometry every {ms} ms" if ms else "odometry off"


def _step(tokens):
    """One program step -> (normalized text, seconds). Same grammar as the mBot runtime."""
    if not tokens:
        raise CommandError("empty step")
    c, a = tokens[0].lower(), tokens[1:]
    if c in DIRECTIONS:
        text, _ = move(c, a[0] if a else 40, a[1] if len(a) > 1 else 1.0)
        return text, float(text.split()[2])
    if c == "wait":
        sec = _clamp(_num(a[0] if a else 0.5, "wait"), 0, L.MAX_MOVE_S)
        return f"wait {sec:g}", sec
    if c in ("straight", "turn"):
        if not a:
            raise CommandError(f"{c} needs an amount")
        text, _ = (step if c == "straight" else turn)(a[0], a[1] if len(a) > 1 else 30)
        amount, pct = (float(x) for x in text.split()[1:3])
        v_cm_s = L.MAX_RPM * pct / 100 * 6 * (6.5 * 3.14159 / 360)
        est = abs(amount) / v_cm_s if c == "straight" else abs(amount) / (2 * v_cm_s / 11.5 * 57.2958)
        return text, min(est * 1.5 + 1.0, L.MAX_MOVE_S)
    if c == "led":
        if len(a) < 3:
            raise CommandError("led needs r g b")
        return led(*a[:4])[0], 0.0
    if c == "arm":
        return arm(a[0] if a else "home")[0], 0.0
    if c in ("grip", "gripper"):
        return gripper(a[0] if a else "home")[0], 0.0
    if c == "home":
        return "home", 0.0
    raise CommandError(f"unknown step {c!r} (allowed: forward/backward/left/right, straight, turn, wait, led, "
                       "arm, grip, home)")


def program(steps):
    """steps: 'fwd 40 1; led 0 255 0; wait 0.5; arm up' or a list of step strings."""
    raw = steps if isinstance(steps, list) else str(steps).split(";")
    raw = [s.strip() for s in raw if s and s.strip()]
    if not raw:
        raise CommandError("program is empty")
    if len(raw) > L.MAX_PROG_STEPS:
        raise CommandError(f"program has {len(raw)} steps, max is {L.MAX_PROG_STEPS}")
    out, total = [], 0.0
    for s in raw:
        text, sec = _step(s.split())
        out.append(text)
        total += sec
    if total > L.MAX_PROG_S:
        raise CommandError(f"program lasts {total:g}s, max is {L.MAX_PROG_S:g}s")
    return "prog " + "; ".join(out), f"{len(out)} steps, about {total:g}s of motion/wait"
