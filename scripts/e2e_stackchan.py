"""End-to-end test of the 1-to-1 path: MCP client -> stackchan-mcp gateway (feat/mbot-ble)
-> Wi-Fi -> Stack-chan firmware -> BLE -> mBot runtime.

  .venv/bin/python scripts/e2e_stackchan.py get_status mbot_status
  .venv/bin/python scripts/e2e_stackchan.py 'mbot_set_led r=0 g=255 b=0' 'mbot_move direction=forward speed_percent=30 seconds=1'

Each argument is "tool key=value ..." (values parsed as int/float when possible), or "sleep=<s>".
The gateway is started from ../stackchan-mcp-mbot/gateway with `uv run stackchan-mcp`.
"""
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

GATEWAY = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "stackchan-mcp-mbot", "gateway"))


def parse(arg):
    name, *kvs = arg.split()
    args = {}
    for kv in kvs:
        k, v = kv.split("=", 1)
        for cast in (int, float):
            try:
                v = cast(v)
                break
            except ValueError:
                pass
        args[k] = v
    return name, args


def text(res):
    return " | ".join(getattr(c, "text", f"<{c.type}>") for c in res.content)


async def main(calls):
    params = StdioServerParameters(command="uv", args=["run", "stackchan-mcp"], cwd=GATEWAY,
                                   env={**os.environ})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print(f"gateway up, {len(names)} tools, mbot tools: {[n for n in names if n.startswith('mbot')]}")
            # wait for Stack-chan to connect its WebSocket
            for _ in range(int(os.environ.get('E2E_WAIT', 90))):
                st = text(await s.call_tool("get_status", {}))
                try:
                    j = json.loads(st)
                    if j.get("connected") and j.get("initialized"):
                        break
                except ValueError:
                    pass
                await asyncio.sleep(1)
            print("status:", st[:300])
            for c in calls:
                if c.startswith("sleep="):
                    await asyncio.sleep(float(c.split("=", 1)[1]))
                    continue
                name, args = parse(c)
                t0 = time.time()
                res = await s.call_tool(name, args)
                print(f"{name} {args} -> ({time.time() - t0:.2f}s) {text(res)[:400]}")


asyncio.run(main(sys.argv[1:]))
