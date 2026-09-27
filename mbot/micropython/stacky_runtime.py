# Stacky runtime v1.1: runs ON the mBot2 CyberPi (mBlock 5 -> Python -> Upload mode).
# Receives Stacky protocol v1 commands from Stack-chan (or the Mac hub) over BLE/USB
# and executes them SAFELY. Spec: docs/protocol.md
#
# Safety, enforced here regardless of what the sender asks:
#   - speeds clamped to MAX_RPM, every motion ends after <= MAX_MOVE_S
#   - arm S4 in [40,120], gripper S3 in [45,120], servos glide at SERVO_DEG_PER_S
#   - forward motion stops below OBSTACLE_CM
#   - watchdog: moving/programs stop if the host goes silent for WATCHDOG_S
#   - button B = emergency stop + lock, button A = unlock
#   - v1.1 exploration primitives (straight <cm>, turn <deg>) are closed-loop HERE,
#     on encoders and gyro, so link/LLM delays cannot change how far the robot goes
import cyberpi
import mbot2
import mbuild
import time

VERSION = "1.1"

# ---- Safety limits (mirror of docs/protocol.md; checked by tests/test_limits.py) ----
MAX_RPM = 60
MAX_MOVE_S = 5.0
MAX_PROG_S = 30.0
MAX_PROG_STEPS = 20
ARM_MIN = 40
ARM_MAX = 120
ARM_HOME = 90
GRIP_MIN = 45
GRIP_MAX = 120
GRIP_HOME = 90
SERVO_DEG_PER_S = 90
OBSTACLE_CM = 10
WATCHDOG_S = 3.0
HB_PERIOD_S = 1.0
MAX_STEP_CM = 30
MAX_TURN_DEG = 180
ODOM_MIN_MS = 100

# ---- Calibration (verify with scripts/calibrate_mbot.py, then edit here) ----
WHEEL_CM_PER_DEG = 6.5 * 3.14159 / 360   # 6.5 cm wheels
TRACK_CM = 11.5                          # distance between the wheels
ENC_LEFT_SIGN = 1     # EM1 angle grows when the left wheel rolls forward
ENC_RIGHT_SIGN = -1   # EM2 is mounted mirrored
YAW_SIGN = 1          # +1 if cyberpi.get_yaw() grows when the robot turns left (CCW)
MOVE_TOL_CM = 0.5
TURN_TOL_DEG = 2.0
SLOW_CM = 3.0         # slow down for the last few cm / degrees to limit overshoot
SLOW_DEG = 10.0

ARM_PORT = "s4"
GRIP_PORT = "s3"
ARM_PRESETS = {"up": ARM_MAX, "down": ARM_MIN, "home": ARM_HOME}
GRIP_PRESETS = {"open": GRIP_MAX, "close": GRIP_MIN, "home": GRIP_HOME}
MOVES = ("fwd", "back", "left", "right")
CLOSED = ("straight", "turn")
SERVO_STEP_MS = 33
SERVO_STEP_DEG = max(1, int(SERVO_DEG_PER_S * SERVO_STEP_MS / 1000))


def now():
    return time.ticks_ms()


def since(t):
    return time.ticks_diff(time.ticks_ms(), t) / 1000.0


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def evt(text):
    cyberpi.upload_broadcast.set("sk_evt", text)


def ack(cid, ok, detail=""):
    cyberpi.upload_broadcast.set("sk_ack", "%s %s %s" % (cid, "ok" if ok else "err", detail))


# ---- State (shared between the message handler thread and the main loop) ----
inbox = []          # (id, tokens) waiting for the main loop
last_rx = now()     # last message of any kind from the host
last_id = None      # dedupe host retries
locked = False
motion = None       # dict(kind, end, id, w0, y0, ...) while wheels turn
odom_ms = 0         # odometry streaming period (0 = off)
program = None      # dict(steps, idx, id, wait_until, started)
servo = {}          # port -> [current, target]


def servo_init(port, home, lo, hi):
    cur = home
    try:
        g = float(mbot2.servo_get(port))
        if 0 <= g <= 180:
            cur = int(g)
    except Exception:
        pass
    servo[port] = [cur, home]  # glide from wherever it is to home


# ---- Encoders and gyro ----
EM_PORTS = None     # detected on first use: ("EM1", "EM2") or (1, 2)
yaw_state = {"last": None, "acc": 0.0}


def enc_deg(i):
    global EM_PORTS
    if EM_PORTS is None:
        EM_PORTS = ()
        for cand in (("EM1", "EM2"), (1, 2)):
            try:
                float(mbot2.EM_get_angle(cand[0]))
                EM_PORTS = cand
                break
            except Exception:
                pass
    if not EM_PORTS:
        return None
    try:
        return float(mbot2.EM_get_angle(EM_PORTS[i]))
    except Exception:
        return None


def wheels_cm():
    """(left_cm, right_cm) travelled forward since boot, or None without encoders."""
    a, b = enc_deg(0), enc_deg(1)
    if a is None or b is None:
        return None
    return ENC_LEFT_SIGN * a * WHEEL_CM_PER_DEG, ENC_RIGHT_SIGN * b * WHEEL_CM_PER_DEG


def yaw_deg():
    """Unwrapped heading in degrees, + = turned left (CCW), or None without gyro."""
    try:
        y = float(cyberpi.get_yaw()) * YAW_SIGN
    except Exception:
        return None
    last = yaw_state["last"]
    if last is None:
        yaw_state["acc"] = y
    else:
        d = y - last
        if d > 180:
            d -= 360
        elif d < -180:
            d += 360
        yaw_state["acc"] += d
    yaw_state["last"] = y
    return yaw_state["acc"]


def fmt(v):
    return "none" if v is None else "%.1f" % (v if abs(v) >= 0.05 else 0.0)


def progress(m):
    """(cm driven, degrees turned) since motion m started."""
    d = dy = None
    w, y = wheels_cm(), yaw_deg()
    if w is not None and m.get("w0") is not None:
        d = ((w[0] - m["w0"][0]) + (w[1] - m["w0"][1])) / 2.0
    if y is not None and m.get("y0") is not None:
        dy = y - m["y0"]
    return d, dy


def report_done(m, reason):
    d, dy = progress(m)
    evt("done %s %s dist=%s yaw=%s t=%d" % (m["id"], reason, fmt(d), fmt(dy), now()))


def stop_all(reason, silent=False):
    global motion, program
    try:
        mbot2.drive_speed(0, 0)
        mbot2.EM_stop("ALL")
    except Exception:
        pass
    was_busy = motion is not None or program is not None
    if motion is not None and not silent:
        report_done(motion, reason)   # the host always learns what really happened
    for p in servo:             # freeze servos where they are (e.g. a closing gripper)
        if servo[p][0] != servo[p][1]:
            servo[p][1] = servo[p][0]
            was_busy = True
    motion = None
    program = None
    del inbox[:]
    if was_busy and not silent:
        evt("stopped " + reason)


def show_state(line2=""):
    cyberpi.console.clear()
    cyberpi.console.println("STACKY " + VERSION)
    cyberpi.console.println("LOCKED (A=unlock)" if locked else "READY (B=stop)")
    if line2:
        cyberpi.console.println(line2)


# ---- Parsing and validation (used for single commands and program steps) ----
def num(tok, default=None):
    try:
        return float(tok)
    except Exception:
        return default


def parse_step(tokens):
    """Return (cmd, args, seconds) with clamped args, or raise ValueError(reason)."""
    if not tokens:
        raise ValueError("empty")
    cmd = tokens[0]
    a = tokens[1:]
    if cmd in MOVES:
        pct = clamp(num(a[0] if a else None, 40), 0, 100)
        sec = clamp(num(a[1] if len(a) > 1 else None, 1.0), 0.1, MAX_MOVE_S)
        return cmd, (pct, sec), sec
    if cmd == "wait":
        sec = clamp(num(a[0] if a else None, 0.5), 0.0, MAX_MOVE_S)
        return cmd, (sec,), sec
    if cmd in CLOSED:
        lim = MAX_STEP_CM if cmd == "straight" else MAX_TURN_DEG
        amount = num(a[0] if a else None)
        if amount is None:
            raise ValueError("amount")
        amount = clamp(amount, -lim, lim)
        pct = clamp(num(a[1] if len(a) > 1 else None, 30), 5, 100)
        v_cm_s = MAX_RPM * pct / 100.0 * 6 * WHEEL_CM_PER_DEG   # rpm*6 = deg/s
        if cmd == "straight":
            est = abs(amount) / v_cm_s
        else:
            est = abs(amount) / (2 * v_cm_s / TRACK_CM * 57.2958)
        return cmd, (amount, pct), min(est * 1.5 + 1.0, MAX_MOVE_S)
    if cmd == "led":
        if len(a) < 3:
            raise ValueError("led_args")
        rgb = [int(clamp(num(x, 0), 0, 255)) for x in a[:3]]
        idx = a[3] if len(a) > 3 else "all"
        if idx != "all":
            idx = int(clamp(num(idx, 1), 1, 5))
        return cmd, (rgb, idx), 0.0
    if cmd in ("arm", "grip"):
        presets, lo, hi = (ARM_PRESETS, ARM_MIN, ARM_MAX) if cmd == "arm" else (GRIP_PRESETS, GRIP_MIN, GRIP_MAX)
        if not a:
            raise ValueError("angle")
        deg = presets.get(a[0])
        if deg is None:
            deg = num(a[0])
            if deg is None:
                raise ValueError("angle")
        deg = int(clamp(deg, lo, hi))
        return cmd, (deg,), 0.0
    if cmd == "home":
        return cmd, (), 0.0
    raise ValueError("unknown_cmd")


def start_step(cmd, args, cid):
    """Execute one validated step. Motions are non-blocking; the main loop ends them."""
    global motion
    if cmd in MOVES:
        pct, sec = args
        v = MAX_RPM * pct / 100.0
        l, r = {"fwd": (v, -v), "back": (-v, v), "right": (v, v), "left": (-v, -v)}[cmd]
        motion = {"kind": cmd, "end": time.ticks_add(now(), int(sec * 1000)), "id": cid,
                  "w0": wheels_cm(), "y0": yaw_deg()}
        mbot2.drive_speed(l, r)
    elif cmd in CLOSED:
        amount, pct = args
        timeout = parse_step([cmd, str(amount), str(pct)])[2]
        v = MAX_RPM * pct / 100.0
        if cmd == "straight":
            l, r = (v, -v) if amount >= 0 else (-v, v)
            direction = "fwd" if amount >= 0 else "back"
        else:
            l, r = (-v, -v) if amount >= 0 else (v, v)   # + = left (CCW)
            direction = "left" if amount >= 0 else "right"
        motion = {"kind": cmd, "dir": direction, "target": abs(amount), "v": (l, r), "slow": False,
                  "end": time.ticks_add(now(), int(timeout * 1000)), "id": cid,
                  "w0": wheels_cm(), "y0": yaw_deg()}
        if (cmd == "straight" and motion["w0"] is None) or (cmd == "turn" and motion["y0"] is None):
            motion["end"] = time.ticks_add(now(), int(timeout / 1.5 * 1000))   # no sensor: timed fallback
        mbot2.drive_speed(l, r)
    elif cmd == "led":
        rgb, idx = args
        cyberpi.led.on(rgb[0], rgb[1], rgb[2], idx)
    elif cmd == "arm":
        servo[ARM_PORT][1] = args[0]
    elif cmd == "grip":
        servo[GRIP_PORT][1] = args[0]
    elif cmd == "home":
        servo[ARM_PORT][1] = ARM_HOME
        servo[GRIP_PORT][1] = GRIP_HOME


def servos_settled():
    for p in servo:
        if servo[p][0] != servo[p][1]:
            return False
    return True


def distance_cm():
    try:
        d = float(mbuild.ultrasonic2.get(1))
        return d if d > 0 else None
    except Exception:
        return None


# ---- Command handling (runs in the main loop) ----
def handle(cid, tokens):
    global locked, program, last_id, odom_ms
    cmd = tokens[0]
    if cmd == "stop":
        stop_all("cmd")
        ack(cid, True)
        return
    if cmd == "read":
        d = distance_cm()
        w = wheels_cm()
        evt("sensors dist=%s batt=%s arm=%d grip=%d locked=%d l=%s r=%s yaw=%s t=%d" % (
            "none" if d is None else int(d), cyberpi.get_battery(),
            servo[ARM_PORT][0], servo[GRIP_PORT][0], 1 if locked else 0,
            fmt(w[0] if w else None), fmt(w[1] if w else None), fmt(yaw_deg()), now()))
        ack(cid, True)
        return
    if cmd == "sync":
        ack(cid, True, "%s %d" % (tokens[1] if len(tokens) > 1 else "0", now()))
        return
    if cmd == "odom":
        ms = num(tokens[1] if len(tokens) > 1 else None, 0)
        odom_ms = 0 if ms <= 0 else int(clamp(ms, ODOM_MIN_MS, 5000))
        ack(cid, True, str(odom_ms))
        return
    if locked:
        ack(cid, False, "locked")
        return
    if cmd == "prog":
        text = " ".join(tokens[1:])
        raw = [s.strip() for s in text.split(";") if s.strip()]
        if not raw:
            ack(cid, False, "empty")
            return
        if len(raw) > MAX_PROG_STEPS:
            ack(cid, False, "too_many_steps")
            return
        steps = []
        total = 0.0
        try:
            for s in raw:
                st = parse_step(s.split())
                steps.append(st)
                total += st[2]
        except ValueError as e:
            ack(cid, False, "step_" + str(e))
            return
        if total > MAX_PROG_S:
            ack(cid, False, "too_long")
            return
        stop_all("new_prog", silent=True)
        program = {"steps": steps, "idx": 0, "id": cid, "wait_until": None}
        ack(cid, True, "%d_steps %ds" % (len(steps), int(total)))
        return
    try:
        c, args, _ = parse_step(tokens)
    except ValueError as e:
        ack(cid, False, str(e))
        return
    if c == "wait":
        ack(cid, False, "wait_only_in_prog")
        return
    if c in MOVES or c in CLOSED:
        if motion is not None:
            report_done(motion, "replaced")
        stop_all("replaced", silent=True)  # a new motion replaces the current one
    start_step(c, args, cid)
    ack(cid, True)


# ---- Message handlers (BLE/USB "upload mode messages") ----
@cyberpi.event.upload_broadcast("sk")
def on_sk():
    global last_rx, last_id
    last_rx = now()
    parts = str(cyberpi.upload_broadcast.get("sk")).split()
    if len(parts) < 2:
        return
    cid, tokens = parts[0], parts[1:]
    if cid == last_id:          # host retry of a command we already took
        ack(cid, True, "dup")
        return
    last_id = cid
    if tokens[0] == "stop":     # stop acts immediately, not after the queue
        stop_all("cmd")
    inbox.append((cid, tokens))


@cyberpi.event.upload_broadcast("sk_hb")
def on_hb():
    global last_rx
    last_rx = now()


# ---- Main loop ----
t = {"servo": 0, "dist": 0, "hb_out": 0, "odom": 0, "a": False, "b": False}


def finish_motion(reason):
    global motion
    m = motion
    mbot2.drive_speed(0, 0)
    motion = None
    if program is None:
        report_done(m, reason)


def tick():
    """One pass of the control loop (called every ~10 ms)."""
    global locked, motion, program
    # Local buttons always win (polled: reliable regardless of event support)
    b = cyberpi.controller.is_press("b")
    a = cyberpi.controller.is_press("a")
    if b and not t["b"]:
        stop_all("button")
        locked = True
        cyberpi.led.on(80, 0, 0, "all")
        evt("locked")
        evt("button b")
        show_state()
    if a and not t["a"]:
        evt("button a")
        if locked:
            locked = False
            cyberpi.led.on(0, 0, 40, "all")
            evt("unlocked")
            show_state()
    t["b"], t["a"] = b, a

    # Commands from the host
    while inbox:
        cid, tokens = inbox.pop(0)
        try:
            handle(cid, tokens)
        except Exception:
            stop_all("error")
            ack(cid, False, "exception")

    # Watchdog: never keep moving if the host went silent
    if (motion is not None or program is not None) and since(last_rx) > WATCHDOG_S:
        stop_all("watchdog")

    # Closed-loop primitives: stop exactly when the encoders / gyro say so
    if motion is not None and motion["kind"] in CLOSED:
        d, dy = progress(motion)
        amt = d if motion["kind"] == "straight" else dy
        done_amt = abs(amt) if amt is not None else None
        if done_amt is not None:
            remaining = motion["target"] - done_amt
            tol, slow = (MOVE_TOL_CM, SLOW_CM) if motion["kind"] == "straight" else (TURN_TOL_DEG, SLOW_DEG)
            if remaining <= tol:
                finish_motion("target")
            elif remaining <= slow and not motion["slow"]:
                motion["slow"] = True
                l, r = motion["v"]
                mbot2.drive_speed(l * 0.4, r * 0.4)

    # End of timed motion (or closed-loop timeout)
    if motion is not None and time.ticks_diff(motion["end"], now()) <= 0:
        finish_motion("timeout" if motion["kind"] in CLOSED and motion.get("target") else "time")

    # Obstacle guard (forward only), checked every 100 ms
    if motion is not None and (motion["kind"] == "fwd" or motion.get("dir") == "fwd") \
            and time.ticks_diff(now(), t["dist"]) >= 100:
        t["dist"] = now()
        d = distance_cm()
        if d is not None and d < OBSTACLE_CM:
            report_done(motion, "obstacle")
            stop_all("obstacle", silent=True)
            evt("obstacle %d" % int(d))
            evt("stopped obstacle")

    # Servo glide
    if time.ticks_diff(now(), t["servo"]) >= SERVO_STEP_MS:
        t["servo"] = now()
        for p in servo:
            cur, tgt = servo[p]
            if cur != tgt:
                step = SERVO_STEP_DEG if tgt > cur else -SERVO_STEP_DEG
                cur = tgt if abs(tgt - cur) <= SERVO_STEP_DEG else cur + step
                servo[p][0] = cur
                mbot2.servo_set(cur, p)

    # Program runner: next step when the current one has finished
    if program is not None and motion is None and servos_settled():
        wu = program["wait_until"]
        if wu is None or time.ticks_diff(wu, now()) <= 0:
            program["wait_until"] = None
            if program["idx"] >= len(program["steps"]):
                pid = program["id"]
                program = None
                evt("done %s prog t=%d" % (pid, now()))
            else:
                c, args, sec = program["steps"][program["idx"]]
                program["idx"] += 1
                if c == "wait":
                    program["wait_until"] = time.ticks_add(now(), int(sec * 1000))
                else:
                    start_step(c, args, program["id"])

    # Odometry stream (v1.1)
    if odom_ms and time.ticks_diff(now(), t["odom"]) >= odom_ms:
        t["odom"] = now()
        w = wheels_cm()
        evt("odom t=%d l=%s r=%s yaw=%s" % (now(), fmt(w[0] if w else None),
                                            fmt(w[1] if w else None), fmt(yaw_deg())))

    # Our own heartbeat, so the host knows we are alive
    if time.ticks_diff(now(), t["hb_out"]) >= 2000:
        t["hb_out"] = now()
        evt("hb %s" % cyberpi.get_battery())


@cyberpi.event.start
def main():
    stop_all("boot", silent=True)
    servo_init(ARM_PORT, ARM_HOME, ARM_MIN, ARM_MAX)
    servo_init(GRIP_PORT, GRIP_HOME, GRIP_MIN, GRIP_MAX)
    cyberpi.led.on(0, 0, 40, "all")
    show_state()
    evt("ready " + VERSION)
    while True:
        tick()
        time.sleep(0.01)
