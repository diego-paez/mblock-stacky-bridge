"""Fake CyberPiOS modules so mbot/micropython/stacky_runtime.py can be tested on a PC."""
import importlib.util
import os
import sys
import types

RUNTIME = os.path.join(os.path.dirname(__file__), "..", "mbot", "micropython", "stacky_runtime.py")


class Clock:
    def __init__(self):
        self.ms = 0


def load_runtime():
    """Import the runtime with fakes installed. Returns (runtime_module, log, clock)."""
    log = []          # everything the runtime did, as tuples
    clock = Clock()
    sent = {}         # topic -> list of values the runtime broadcast

    cyberpi = types.ModuleType("cyberpi")

    class _Event:
        def upload_broadcast(self, topic):
            def deco(fn):
                cyberpi._handlers[topic] = fn
                return fn
            return deco

        def start(self, fn):
            cyberpi._start = fn  # never run the infinite loop in tests
            return fn

    class _UB:
        def __init__(self):
            self.values = {}

        def set(self, topic, value):
            sent.setdefault(topic, []).append(value)

        def get(self, topic):
            return self.values.get(topic)

    cyberpi._handlers = {}
    cyberpi.event = _Event()
    cyberpi.upload_broadcast = _UB()
    cyberpi.led = types.SimpleNamespace(on=lambda r, g, b, i="all": log.append(("led", r, g, b, i)))
    cyberpi.console = types.SimpleNamespace(clear=lambda: None, println=lambda s: None)
    cyberpi.controller = types.SimpleNamespace(is_press=lambda k: False)
    cyberpi.get_battery = lambda: 90
    cyberpi.get_yaw = lambda: get_yaw()

    mbot2 = types.ModuleType("mbot2")
    # Simple differential-drive physics on the fake clock, so closed-loop code is exercised.
    phys = types.SimpleNamespace(l=0.0, r=0.0, a1=0.0, a2=0.0, yaw=0.0, x=0.0, last=0,
                                 wheel_cm_per_deg=6.5 * 3.14159 / 360, track=11.5, em_ports=("EM1", "EM2"))

    def integrate():
        dt = (clock.ms - phys.last) / 1000.0
        phys.last = clock.ms
        if dt <= 0:
            return
        phys.a1 += phys.l * 6 * dt              # EM1: + = left wheel forward
        phys.a2 += phys.r * 6 * dt              # EM2: mirrored
        vl = phys.l * 6 * phys.wheel_cm_per_deg
        vr = -phys.r * 6 * phys.wheel_cm_per_deg
        phys.yaw += (vr - vl) / phys.track * 57.2958 * dt   # + = CCW
        phys.x += (vl + vr) / 2 * dt

    def drive_speed(l, r):
        integrate()
        phys.l, phys.r = l, r
        log.append(("drive", l, r))

    def em_get_angle(port):
        integrate()
        if port not in phys.em_ports:
            raise ValueError("bad port")
        return phys.a1 if port == phys.em_ports[0] else phys.a2

    def get_yaw():
        integrate()
        y = (phys.yaw + 180) % 360 - 180        # wraps like a real IMU
        return y

    mbot2.drive_speed = drive_speed
    mbot2.EM_get_angle = em_get_angle
    mbot2.EM_stop = lambda p: log.append(("em_stop",))
    mbot2.servo_set = lambda a, p: log.append(("servo", p, a))
    mbot2.servo_get = lambda p: 120

    mbuild = types.ModuleType("mbuild")
    mbuild.distance = 300.0
    mbuild.ultrasonic2 = types.SimpleNamespace(get=lambda i: mbuild.distance)

    fake_time = types.ModuleType("time")
    fake_time.ticks_ms = lambda: clock.ms
    fake_time.ticks_diff = lambda a, b: a - b
    fake_time.ticks_add = lambda a, b: a + b
    fake_time.sleep = lambda s: None

    saved = {k: sys.modules.get(k) for k in ("cyberpi", "mbot2", "mbuild", "time")}
    sys.modules.update(cyberpi=cyberpi, mbot2=mbot2, mbuild=mbuild, time=fake_time)
    try:
        spec = importlib.util.spec_from_file_location("stacky_runtime", RUNTIME)
        rt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rt)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    rt._fake = types.SimpleNamespace(cyberpi=cyberpi, mbot2=mbot2, mbuild=mbuild, log=log, sent=sent, clock=clock,
                                     phys=phys)
    rt.servo_init(rt.ARM_PORT, rt.ARM_HOME, rt.ARM_MIN, rt.ARM_MAX)
    rt.servo_init(rt.GRIP_PORT, rt.GRIP_HOME, rt.GRIP_MIN, rt.GRIP_MAX)
    return rt


def send(rt, text, topic="sk"):
    """Deliver a host message exactly like CyberPiOS would, then let the main loop take it."""
    rt._fake.cyberpi.upload_broadcast.values[topic] = text
    rt._fake.cyberpi._handlers[topic]()
    while rt.inbox:
        cid, tokens = rt.inbox.pop(0)
        rt.handle(cid, tokens)


def last(rt, topic):
    return rt._fake.sent.get(topic, [None])[-1]
