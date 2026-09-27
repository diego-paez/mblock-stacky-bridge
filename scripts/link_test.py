"""Host side of the Stacky link test. Upload mbot/examples/stacky_link_test.py first.

  .venv/bin/python scripts/link_test.py ble      # 1-to-1 BLE (FFE1 service)
  .venv/bin/python scripts/link_test.py usb      # USB CH340 (quit mBlock first)

Sends ping/face messages and prints EVERY frame received, raw, so we can
confirm what CyberPi emits for cyberpi.upload_broadcast.set(...).
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub.transports import f3  # noqa: E402

SVC_WRITE = "0000ffe3-0000-1000-8000-00805f9b34fb"
SVC_NOTIFY = "0000ffe2-0000-1000-8000-00805f9b34fb"
DURATION = 20  # seconds; press button A on the CyberPi during this time


def show(data, dec):
    for fr in dec.feed(data):
        print(f"  <- type=0x{fr.type:02x} {fr.text!r}   raw={fr.raw.hex(' ')}")


async def run_ble():
    from bleak import BleakClient, BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: (a.local_name or "").startswith("Makeblock_LE"), timeout=10)
    if not dev:
        sys.exit("No Makeblock_LE* device advertising. Power-cycle the CyberPi.")
    dec = f3.Decoder()
    async with BleakClient(dev) as c:
        print("BLE connected:", dev.name)
        await c.start_notify(SVC_NOTIFY, lambda _, d: show(d, dec))
        t0 = time.time()
        n = 0
        while time.time() - t0 < DURATION:
            n += 1
            await c.write_gatt_char(SVC_WRITE, f3.encode_message("ping", n), response=False)
            print(f"  -> ping {n}")
            if n == 2:
                await c.write_gatt_char(SVC_WRITE, f3.encode_message("face", "happy"), response=False)
                print("  -> face happy")
            await asyncio.sleep(3)


def run_usb():
    import serial
    from serial.tools import list_ports
    port = next((p.device for p in list_ports.comports() if (p.vid, p.pid) == (0x1A86, 0x7523)), None)
    if not port:
        sys.exit("No CH340 CyberPi on USB.")
    ser = serial.Serial(port, 115200, timeout=0.05)
    print("USB", port, "(board resets on open, waiting 3 s)")
    time.sleep(3)
    dec = f3.Decoder()
    t0 = time.time()
    n = 0
    next_ping = 0
    while time.time() - t0 < DURATION:
        if time.time() >= next_ping:
            n += 1
            ser.write(f3.encode_message("ping", n))
            print(f"  -> ping {n}")
            next_ping = time.time() + 3
        data = ser.read(256)
        if data:
            show(data, dec)
            # print() output from the program shows up here as plain text
            txt = bytes(b for b in data if 32 <= b < 127 or b == 10)
            if txt.strip() and 0xF3 not in data:
                print("  <- text:", txt.decode().strip())


if __name__ == "__main__":
    mode = (sys.argv[1:] or ["ble"])[0]
    asyncio.run(run_ble()) if mode == "ble" else run_usb()
