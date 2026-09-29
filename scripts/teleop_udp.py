"""Stand-in for the Wi-Fi joystick: talks Stacky teleop v1 (UDP) straight to Stack-chan.

  .venv/bin/python scripts/teleop_udp.py hello              # find Stack-chan, measure Wi-Fi round trip
  .venv/bin/python scripts/teleop_udp.py test               # short, slow drive pattern (mBot on the floor!)
  .venv/bin/python scripts/teleop_udp.py deadman            # drive, then go silent: the mBot must stop by itself

--host defaults to broadcast discovery. The mBot moves at most ~5 cm/s here.
"""
import argparse
import os
import socket
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub import teleop as T  # noqa: E402

RATE_HZ = 20


class Joy:
    def __init__(self, host, key):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.settimeout(0.3)
        self.host, self.key, self.seq = host, key, 0
        self.status = None

    def _next(self):
        self.seq += 1
        return self.seq

    def discover(self):
        target = self.host or "255.255.255.255"
        for _ in range(10):
            self._drain()   # a late reply to an earlier ping must not count for this one
            t0 = time.perf_counter()
            self.sock.sendto(T.encode_hello(self._next(), self.key), (target, T.PORT))
            try:
                data, addr = self.sock.recvfrom(64)
            except socket.timeout:
                continue
            p = T.parse(data)
            if p and p[0] == T.HELLO_ACK:
                self.host = addr[0]
                self.status = p[3]
                return addr[0], (time.perf_counter() - t0) * 1000
        return None, None

    def _drain(self):
        self.sock.setblocking(False)
        try:
            while True:
                self.sock.recv(64)
        except (BlockingIOError, socket.timeout):
            pass
        finally:
            self.sock.settimeout(0.3)

    def send(self, lin_mm_s, ang_mrad_s, buttons):
        self.sock.sendto(T.encode_cmd_vel(self._next(), lin_mm_s, ang_mrad_s, buttons, 300, self.key),
                         (self.host, T.PORT))
        self.sock.setblocking(False)
        try:
            while True:
                p = T.parse(self.sock.recv(64))
                if p and p[0] == T.STATUS:
                    self.status = p[3]
        except (BlockingIOError, socket.timeout):
            pass
        finally:
            self.sock.settimeout(0.3)

    def hold(self, seconds, lin=0, ang=0, buttons=T.ENABLE):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.send(lin, ang, buttons)
            time.sleep(1 / RATE_HZ)


def show(st):
    if st is None:
        return "no status"
    return ("mbot=%s locked=%s active=%s gateway=%s batt=%s dist=%s arm=%s grip=%s ble_rtt=%s ms" % (
        st.mbot_connected, bool(st.flags & T.MBOT_LOCKED), bool(st.flags & T.TELEOP_ACTIVE),
        bool(st.flags & T.GATEWAY_CONNECTED), st.battery_pct, st.dist_cm, st.arm_deg, st.grip_deg, st.rtt_ms))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["hello", "test", "deadman"])
    ap.add_argument("--host", default=None)
    ap.add_argument("--key", type=lambda s: int(s, 0), default=T.DEFAULT_KEY)
    a = ap.parse_args()
    j = Joy(a.host, a.key)
    host, rtt = j.discover()
    if not host:
        sys.exit("no HELLO_ACK: is Stack-chan on the same Wi-Fi, with the teleop firmware and the same key?")
    print("Stack-chan at %s (first round trip %.1f ms)" % (host, rtt))
    print(show(j.status))

    if a.mode == "hello":
        rtts = []
        for _ in range(30):
            _, r = j.discover()
            if r is not None:
                rtts.append(r)
            time.sleep(0.05)
        if rtts:
            print("Wi-Fi UDP round trip over %d pings: median %.1f ms, p90 %.1f ms, max %.1f ms" % (
                len(rtts), statistics.median(rtts), sorted(rtts)[int(len(rtts) * 0.9) - 1], max(rtts)))
        return

    if not j.status or not j.status.mbot_connected:
        sys.exit("Stack-chan is not linked to the mBot yet")
    try:
        if a.mode == "test":
            print("enable, standing still 1 s");        j.hold(1.0)
            print("forward 5 cm/s for 1.5 s");         j.hold(1.5, lin=50)
            print("turn left 30 deg/s for 1.5 s");      j.hold(1.5, ang=524)
            print("gentle curve for 1.5 s");            j.hold(1.5, lin=40, ang=-300)
            print("disable (stop)");                    j.hold(0.5, buttons=0)
        else:
            print("forward 5 cm/s for 1 s, then silence: the mBot must stop within ~0.3 s")
            j.hold(1.0, lin=50)
            time.sleep(1.0)
        print(show(j.status))
    finally:
        for _ in range(3):
            j.send(0, 0, 0)   # disabled: Stack-chan stops the mBot


if __name__ == "__main__":
    main()
