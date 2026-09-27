"""Check the encoder / gyro conventions of stacky_runtime.py v1.1 on the real mBot.

Uses only v1 *timed* moves (which work whatever the calibration), and reads the
runtime's own l/r/yaw values before and after each one:

  .venv/bin/python scripts/calibrate_mbot.py        # Mac BLE (Stack-chan must not hold the link)
  .venv/bin/python scripts/calibrate_mbot.py --usb

Put the mBot on the floor with ~40 cm free in front of it. It moves slowly, twice.
If a sign is wrong, edit the constant it names in mbot/micropython/stacky_runtime.py,
re-upload, and run this again until everything says OK.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub.link import MbotLink  # noqa: E402


async def read(link):
    s = await link.read_sensors()
    if not s:
        sys.exit("no sensor reply: is stacky_runtime v1.1 running on the mBot?")
    f = lambda k: None if s.get(k) in (None, "none") else float(s[k])  # noqa: E731
    return f("l"), f("r"), f("yaw")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", action="store_true")
    args = ap.parse_args()
    if args.usb:
        from stacky_hub.transports.usb import UsbTransport as T
    else:
        from stacky_hub.transports.ble import BleTransport as T
    link = MbotLink(T())
    await link.ensure_connected()
    ok = True
    try:
        rtt, off = await link.sync()
        print(f"link round trip: {rtt * 1000:.0f} ms")

        l0, r0, y0 = await read(link)
        if l0 is None:
            print("ENCODERS: not readable -> check EM port detection in enc_deg() (tries 'EM1'/'EM2' and 1/2)")
            ok = False
        print("driving FORWARD slowly for 1.5 s ...")
        d, _ = await link.motion("fwd 25 1.5")
        await asyncio.sleep(0.5)
        l1, r1, y1 = await read(link)
        if l0 is not None:
            dl, dr = l1 - l0, r1 - r0
            print(f"  left wheel {dl:+.1f} cm, right wheel {dr:+.1f} cm  (done: {d})")
            if dl <= 0:
                print("  -> LEFT sign wrong: flip ENC_LEFT_SIGN"); ok = False
            if dr <= 0:
                print("  -> RIGHT sign wrong: flip ENC_RIGHT_SIGN"); ok = False
            if dl > 0 and dr > 0:
                print(f"  OK. Measure the real distance with a ruler: runtime says {(dl + dr) / 2:.1f} cm."
                      "  If different, scale WHEEL_CM_PER_DEG.")

        print("turning LEFT (counter-clockwise, seen from above) for 1 s ...")
        d, _ = await link.motion("left 30 1")
        await asyncio.sleep(0.5)
        l2, r2, y2 = await read(link)
        if y1 is None:
            print("GYRO: not readable"); ok = False
        else:
            dy = y2 - y1
            print(f"  yaw change {dy:+.1f} deg")
            if dy <= 0:
                print("  -> YAW sign wrong: flip YAW_SIGN"); ok = False
            else:
                print("  OK. Check with a protractor that the robot really turned about that much.")
        print("\nALL OK" if ok else "\nFix the constants above, re-upload the runtime, run again.")
    finally:
        await link.stop()
        await link.close()


asyncio.run(main())
