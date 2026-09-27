"""Call tools on the stackchan-mcp gateway DAEMON (HTTP), e.g. to test the 1-to-1 path.

  .venv/bin/python scripts/gw_call.py get_status mbot_status 'mbot_step distance_cm=10' sleep=3 mbot_odometry
Token: STACKCHAN_TOKEN env var, else read from ../stackchan-mcp-mbot/gateway/.env.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub.adapters import GatewayClient  # noqa: E402

ENV = os.path.join(os.path.dirname(__file__), "..", "..", "stackchan-mcp-mbot", "gateway", ".env")


def token():
    if os.environ.get("STACKCHAN_TOKEN"):
        return os.environ["STACKCHAN_TOKEN"]
    try:
        for line in open(ENV):
            if line.startswith("STACKCHAN_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        return None


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


async def main(calls):
    async with GatewayClient(os.environ.get("GW_URL", "http://127.0.0.1:8767/mcp"), token()) as gw:
        for c in calls:
            if c.startswith("sleep="):
                await asyncio.sleep(float(c.split("=", 1)[1]))
                continue
            name, args = parse(c)
            t0 = time.time()
            out = await gw.call(name, args)
            print(f"{name} {args} -> ({time.time() - t0:.2f}s) {out[:400]}")


asyncio.run(main(sys.argv[1:]))
