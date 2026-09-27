"""Manual test of the full host stack (limits -> link -> BLE/USB -> stacky_runtime on the mBot).

  .venv/bin/python scripts/mbot_cli.py                 # interactive, BLE
  .venv/bin/python scripts/mbot_cli.py --usb
Type protocol commands:  fwd 30 1 | back 30 1 | left 40 0.5 | stop | led 0 255 0 | arm up |
                          grip open | home | read | prog led 0 0 255; fwd 30 1; arm down
Events from the robot are printed as they arrive. Ctrl-D to quit (sends stop).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub import commands as C  # noqa: E402
from stacky_hub.link import MbotLink  # noqa: E402


def build(line):
    tok = line.split()
    if "stop" in tok:            # "grip stop", "arm stop", "stop" -> always a full stop
        return "stop", "stop"
    c, a = tok[0], tok[1:]
    if c in C.DIRECTIONS:
        return C.move(c, *a[:2])
    if c == "led":
        return C.led(*a[:4])
    if c == "arm":
        return C.arm(a[0])
    if c in ("grip", "gripper"):
        return C.gripper(a[0])
    if c == "prog":
        return C.program(line.split(" ", 1)[1])
    if c in ("stop", "home", "read"):
        return c, c
    raise C.CommandError(f"unknown {c!r}")


def ainput(loop, prompt):
    """input() on a daemon thread, so Ctrl-C never waits for the keyboard."""
    import threading
    fut = loop.create_future()

    def worker():
        try:
            line = input(prompt)
            loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(line))
        except EOFError as e:
            loop.call_soon_threadsafe(lambda: fut.done() or fut.set_exception(e))
    threading.Thread(target=worker, daemon=True).start()
    return fut


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", action="store_true")
    args = ap.parse_args()
    if args.usb:
        from stacky_hub.transports.usb import UsbTransport as T
    else:
        from stacky_hub.transports.ble import BleTransport as T
    link = MbotLink(T())
    link.on_event = lambda e: None if e.startswith("hb ") else print(f"\n  <- {e}\n> ", end="", flush=True)
    await link.ensure_connected()
    print("connected. commands: fwd/back/left/right <speed%> <sec>, stop, led r g b, arm up|down|home|deg, "
          "grip open|close|home|deg, home, read, prog <steps; ...>")
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await ainput(loop, "> ")
            if not line.strip():
                continue
            try:
                text, note = build(line.strip())
            except (C.CommandError, TypeError, IndexError) as e:
                print("  invalid:", e)
                continue
            print("  ->", text, "|", note)
            print("  <- ack", await link.command(text))
    except (EOFError, asyncio.CancelledError):
        pass
    finally:
        print("\n  -> stop (quitting)")
        try:
            await link.stop()
        finally:
            await link.close()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass  # stop was already sent in main()'s finally
