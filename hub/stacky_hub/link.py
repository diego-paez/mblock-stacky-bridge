"""MbotLink: Stacky v1 over a byte transport (BLE or USB), following docs/protocol.md.

- one `sk` command in flight at a time, wait for `sk_ack`, retry once with the same id
- `stop` bypasses the queue
- `sk_hb` heartbeat every HB_PERIOD_S while connected (the mBot watchdog relies on it)
"""
import asyncio
import collections
import dataclasses
import json
import logging
import time

from . import limits as L
from .transports import f3

log = logging.getLogger("stacky.link")


MOTION_CMDS = ("fwd", "back", "left", "right", "straight", "turn", "prog")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclasses.dataclass
class Done:
    """Outcome of one motion, as measured on the robot (protocol v1.1)."""
    id: str
    reason: str               # target | time | timeout | obstacle | cmd | button | watchdog | replaced | prog
    dist_cm: float = None     # actually driven (None if no encoders)
    yaw_deg: float = None     # actually turned, + = left (None if no gyro)
    mbot_ms: int = None       # robot clock when it ended
    host_time: float = 0.0    # time.time() when the host received it

    @property
    def interrupted(self):
        return self.reason not in ("target", "time", "prog")


class MbotLink:
    def __init__(self, transport):
        self.t = transport
        self.t.on_bytes = self._on_bytes
        self._dec = f3.Decoder()
        self._id = 0
        self._hb = 0
        self._lock = asyncio.Lock()
        self._acks = {}
        self._sensors_waiter = None
        self._hb_task = None
        self.events = collections.deque(maxlen=50)   # (time, text)
        self.last_sensors = None
        self.odom = None            # latest {"t": mbot_ms, "l": cm, "r": cm, "yaw": deg, "host_time": s}
        self.dones = collections.OrderedDict()       # id -> Done (most recent 50)
        self._done_waiters = {}
        self.rtt_s = None           # last measured round trip (sync)
        self.clock_offset_ms = None  # mbot_ms - host_ms (host ms = monotonic*1000)
        self.on_event = None                          # optional callback(text)

    # ---- connection ----
    @property
    def connected(self):
        return self.t.connected

    async def ensure_connected(self):
        if not self.t.connected:
            await self.t.connect()
            self._dec = f3.Decoder()
        if self._hb_task is None or self._hb_task.done():
            self._hb_task = asyncio.create_task(self._heartbeat())

    async def close(self):
        if self._hb_task:
            self._hb_task.cancel()
        await self.t.close()

    async def _heartbeat(self):
        while True:
            try:
                if self.t.connected:
                    self._hb = self._hb % 9999 + 1
                    await self.t.write(f3.encode_message("sk_hb", self._hb))
            except Exception as e:  # keep beating; reconnect happens on next command
                log.warning("heartbeat failed: %s", e)
            await asyncio.sleep(L.HB_PERIOD_S)

    # ---- incoming ----
    def _on_bytes(self, data):
        for fr in self._dec.feed(data):
            if fr.type != f3.TYPE_UPLOAD_MSG:
                continue
            try:
                msg = json.loads(fr.text)
            except ValueError:
                continue
            topic, value = msg.get("message"), str(msg.get("value", ""))
            if topic == "sk_ack":
                parts = value.split(" ", 2)
                fut = self._acks.get(parts[0])
                if fut and not fut.done():
                    fut.set_result((len(parts) > 1 and parts[1] == "ok", parts[2].strip() if len(parts) > 2 else ""))
            elif topic == "sk_evt":
                self._event(value)

    def _event(self, text):
        if text.startswith("odom "):
            kv = dict(p.split("=", 1) for p in text.split()[1:] if "=" in p)
            self.odom = {"t": _num(kv.get("t")), "l": _num(kv.get("l")), "r": _num(kv.get("r")),
                         "yaw": _num(kv.get("yaw")), "host_time": time.time()}
            return
        if text.startswith("done "):
            parts = text.split()
            kv = dict(p.split("=", 1) for p in parts[3:] if "=" in p)
            d = Done(id=parts[1], reason=parts[2] if len(parts) > 2 else "?",
                     dist_cm=_num(kv.get("dist")), yaw_deg=_num(kv.get("yaw")),
                     mbot_ms=int(_num(kv["t"])) if _num(kv.get("t")) is not None else None,
                     host_time=time.time())
            self.dones[d.id] = d
            while len(self.dones) > 50:
                self.dones.popitem(last=False)
            fut = self._done_waiters.pop(d.id, None)
            if fut and not fut.done():
                fut.set_result(d)
        if not text.startswith("hb "):
            self.events.append((time.time(), text))
            log.info("mbot event: %s", text)
        if text.startswith("sensors "):
            self.last_sensors = dict(kv.split("=", 1) for kv in text.split()[1:] if "=" in kv)
            if self._sensors_waiter and not self._sensors_waiter.done():
                self._sensors_waiter.set_result(self.last_sensors)
        if self.on_event:
            self.on_event(text)

    # ---- outgoing ----
    def _next_id(self):
        self._id = self._id % 9999 + 1
        return str(self._id)

    async def _send_and_wait(self, cid, text):
        fut = asyncio.get_running_loop().create_future()
        self._acks[cid] = fut
        try:
            for attempt in range(2):  # retry once with the SAME id (mBot dedupes)
                await self.t.write(f3.encode_message("sk", f"{cid} {text}"))
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), L.ACK_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.warning("no ack for %s (attempt %d)", cid, attempt + 1)
            return False, "no_ack"
        finally:
            self._acks.pop(cid, None)

    async def command(self, text):
        """Send one command; returns (ok, detail). Serialized, except stop."""
        _, ok, detail = await self.command_id(text)
        return ok, detail

    async def command_id(self, text):
        """Like command() but also returns the id, to wait for its `done`."""
        await self.ensure_connected()
        cid = self._next_id()
        if text.split()[0] == "stop":
            ok, detail = await self._send_and_wait(cid, text)
            return cid, ok, detail
        async with self._lock:
            # register the done-waiter before sending, so a very fast `done` is not missed
            if text.split()[0] in MOTION_CMDS:
                self._done_waiters[cid] = asyncio.get_running_loop().create_future()
            ok, detail = await self._send_and_wait(cid, text)
            if not ok:
                self._done_waiters.pop(cid, None)
            return cid, ok, detail

    async def wait_done(self, cid, timeout):
        """Wait for the `done` of command `cid`. Returns Done, or None on timeout."""
        if cid in self.dones:
            return self.dones[cid]
        fut = self._done_waiters.get(cid)
        if fut is None:
            fut = self._done_waiters[cid] = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            return None

    async def motion(self, text, timeout=None):
        """Send a motion and wait for its outcome. On a lost `done`, stop the robot to be safe."""
        cid, ok, detail = await self.command_id(text)
        if not ok:
            return None, detail
        d = await self.wait_done(cid, timeout or (L.MAX_MOVE_S + 2.0))
        if d is None:
            await self.stop()
            d = await self.wait_done(cid, 1.0)
        return d, detail

    async def sync(self):
        """Measure round-trip time and robot clock offset. Returns (rtt_s, offset_ms)."""
        host_ms = int(time.monotonic() * 1000)
        t0 = time.monotonic()
        ok, detail = await self.command(f"sync {host_ms}")
        rtt = time.monotonic() - t0
        parts = detail.split()
        if ok and len(parts) >= 2 and _num(parts[1]) is not None:
            self.rtt_s = rtt
            self.clock_offset_ms = _num(parts[1]) - (host_ms + rtt * 500)
        return self.rtt_s, self.clock_offset_ms

    async def stop(self):
        return await self.command("stop")

    async def read_sensors(self, timeout=2.0):
        self._sensors_waiter = asyncio.get_running_loop().create_future()
        ok, detail = await self.command("read")
        if not ok:
            return None
        try:
            return await asyncio.wait_for(self._sensors_waiter, timeout)
        except asyncio.TimeoutError:
            return None
