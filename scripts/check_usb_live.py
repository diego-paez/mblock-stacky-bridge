"""Talk to a CyberPi over USB with our own F3 codec (no makeblock package).

Close mBlock first: it holds the serial port.

  .venv/bin/python scripts/check_usb_live.py                # auto-detect CH340
  .venv/bin/python scripts/check_usb_live.py --probe        # also run check 1 over USB
  .venv/bin/python scripts/check_usb_live.py --port /dev/cu.usbserial-210
"""
import argparse
import os
import sys
import time

import serial
from serial.tools import list_ports

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub.transports import f3  # noqa: E402

CH340 = (0x1A86, 0x7523)

PROBE_MODULES = ["network", "espnow", "bluetooth", "ubluetooth", "socket", "usocket",
                 "umqtt.simple", "machine", "_thread", "uasyncio", "communication"]


def find_port():
    for p in list_ports.comports():
        if (p.vid, p.pid) == CH340:
            return p.device
    sys.exit("No CyberPi (CH340 1A86:7523) found. Is it plugged in and switched on?")


class Link:
    def __init__(self, port):
        self.ser = serial.Serial(port, 115200, timeout=0.05)
        self.dec = f3.Decoder()
        self.idx = 1
        time.sleep(2)  # CyberPi resets on open, like makeblock's SerialPort does

    def run(self, script, timeout=2.0):
        self.idx = self.idx % 0xFFFF + 1
        self.ser.write(f3.encode_script(script, self.idx, with_response=True))
        end = time.time() + timeout
        while time.time() < end:
            for fr in self.dec.feed(self.ser.read(256)):
                if fr.idx == self.idx:
                    return fr.text
        return None

    def send(self, script):
        self.idx = self.idx % 0xFFFF + 1
        self.ser.write(f3.encode_script(script, self.idx, with_response=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--probe", action="store_true", help="try importing comm modules on the CyberPi")
    args = ap.parse_args()

    port = args.port or find_port()
    print("port:", port)
    try:
        link = Link(port)
    except serial.SerialException as e:
        sys.exit(f"Cannot open {port}: {e}\n-> Quit mBlock (it keeps the port open) and retry.")

    link.ser.write(f3.GOTO_ONLINE)
    time.sleep(0.5)

    for _ in range(10):  # makeblock also polls get_bri() until the board answers
        r = link.run("cyberpi.get_bri()", 0.5)
        if r is not None:
            break
    print("light sensor:", r)
    if r is None:
        sys.exit("No answer. Try pressing the CyberPi home button, or run once from mBlock in Live mode.")

    print("firmware:", link.run("cyberpi.get_firmware_version()"))
    print("battery :", link.run("cyberpi.get_battery()"))
    print("mac     :", link.run("cyberpi.get_mac_address()", 3))
    link.send('cyberpi.console.println("stacky USB OK")')

    if args.probe:
        # Live-mode scripts are eval'd; __import__ is an expression, so wrap each try in one.
        for name in PROBE_MODULES:
            r = link.run("str(__import__('%s'))" % name)
            print(f"  {name:14s} -> {r}")


if __name__ == "__main__":
    main()
