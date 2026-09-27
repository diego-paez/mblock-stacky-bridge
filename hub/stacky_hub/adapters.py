"""Connect the Explorer to real hardware.

- LinkRobot:    mBot over MbotLink (Mac BLE/USB path, protocol v1.1).
- GatewayRobot: mBot through Stack-chan (1-to-1 BLE path) via the stackchan-mcp gateway tools.
- GatewayEyes:  Stack-chan head + camera via the gateway (move_head, take_photo).

The gateway is reached as an MCP client over Streamable HTTP, so it must run in
daemon mode (`stackchan-mcp serve --transport streamable-http`, default
http://127.0.0.1:8767/mcp). Claude connects to the same daemon at the same time.
"""
import asyncio
import contextlib
import json
import logging

from . import commands as C
from .explore import StepResult

log = logging.getLogger("stacky.adapters")


def _step_result(done, refused_detail=None):
    if done is None:
        return StepResult("refused" if refused_detail else "lost", None, None)
    return StepResult(done.reason, done.dist_cm, done.yaw_deg)


class LinkRobot:
    """Explorer robot backed by MbotLink (the Mac talks to the mBot directly)."""

    def __init__(self, link):
        self.link = link

    async def front_cm(self):
        s = await self.link.read_sensors()
        try:
            return float(s["dist"]) if s and s.get("dist") not in (None, "none") else None
        except ValueError:
            return None

    async def straight(self, cm, speed_percent):
        text, _ = C.step(cm, speed_percent)
        done, detail = await self.link.motion(text)
        return _step_result(done, detail if done is None else None)

    async def turn(self, deg, speed_percent):
        text, _ = C.turn(deg, speed_percent)
        done, detail = await self.link.motion(text)
        return _step_result(done, detail if done is None else None)

    async def stop(self):
        with contextlib.suppress(Exception):
            await self.link.stop()


class GatewayClient:
    """Small MCP client for the stackchan-mcp gateway daemon."""

    def __init__(self, url="http://127.0.0.1:8767/mcp", token=None):
        self.url, self.token = url, token
        self._stack = None
        self.session = None

    async def __aenter__(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        self._stack = contextlib.AsyncExitStack()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        read, write, _ = await self._stack.enter_async_context(streamablehttp_client(self.url, headers=headers))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def call(self, name, args=None):
        res = await self.session.call_tool(name, args or {})
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        return "\n".join(texts)

    async def call_json(self, name, args=None):
        text = await self.call(name, args)
        try:
            return json.loads(text)
        except ValueError:
            return {"raw": text}


class GatewayEyes:
    """Stack-chan's head camera. head yaw is relative to the body; + = left in explorer terms."""

    def __init__(self, gw: GatewayClient, pitch=45, yaw_sign=1, settle_s=0.6, speed=None):
        self.gw, self.pitch, self.yaw_sign, self.settle_s, self.speed = gw, pitch, yaw_sign, settle_s, speed

    async def look(self, head_yaw_deg, question):
        args = {"yaw": int(max(-90, min(90, head_yaw_deg * self.yaw_sign))), "pitch": int(self.pitch)}
        if self.speed:
            args["speed"] = self.speed
        await self.gw.call("move_head", args)
        await asyncio.sleep(self.settle_s)          # let the head stop shaking before the photo
        if not question:
            return None
        res = await self.gw.call_json("take_photo", {"question": question})
        return res.get("image_path") or res.get("raw")


class GatewayRobot:
    """mBot through Stack-chan's own BLE link (1-to-1 path), via the gateway's mbot_* tools.

    Relies on the v1.1 gateway tools mbot_step / mbot_turn / mbot_read_sensors / mbot_stop and
    on mbot_odometry exposing the last `done` (id, reason, dist, yaw)."""

    def __init__(self, gw: GatewayClient, poll_s=0.25, timeout_s=8.0):
        self.gw, self.poll_s, self.timeout_s = gw, poll_s, timeout_s

    async def front_cm(self):
        s = await self.gw.call_json("mbot_read_sensors")
        dist = s.get("dist")
        if dist is None and isinstance(s.get("sensors"), dict):
            dist = s["sensors"].get("dist")
        try:
            return float(dist)
        except (TypeError, ValueError):
            return None

    async def _motion(self, tool, args):
        res = await self.gw.call_json(tool, args)
        if not res.get("ok", True) or res.get("error"):
            return StepResult("refused", None, None)
        cid = str(res.get("id", ""))
        deadline = asyncio.get_running_loop().time() + self.timeout_s
        while asyncio.get_running_loop().time() < deadline:
            od = await self.gw.call_json("mbot_odometry")
            done = od.get("last_done") or {}
            if cid and str(done.get("id")) == cid:
                return StepResult(done.get("reason", "?"), done.get("dist"), done.get("yaw"))
            await asyncio.sleep(self.poll_s)
        await self.stop()
        return StepResult("lost", None, None)

    async def straight(self, cm, speed_percent):
        return await self._motion("mbot_step", {"distance_cm": int(round(cm)), "speed_percent": int(speed_percent)})

    async def turn(self, deg, speed_percent):
        return await self._motion("mbot_turn", {"degrees": int(round(deg)), "speed_percent": int(speed_percent)})

    async def stop(self):
        with contextlib.suppress(Exception):
            await self.gw.call("mbot_stop")
