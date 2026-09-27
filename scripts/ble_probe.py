"""Run read-only expressions on a CyberPi over BLE (F3 live protocol). Usage: ble_probe.py EXPR [EXPR...]"""
import asyncio, os, sys
from bleak import BleakScanner, BleakClient
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))
from stacky_hub.transports import f3

W = "0000ffe3-0000-1000-8000-00805f9b34fb"; N = "0000ffe2-0000-1000-8000-00805f9b34fb"

async def main(exprs, name_prefix="Makeblock_LE"):
    dev = await BleakScanner.find_device_by_filter(lambda d, a: (a.local_name or "").startswith(name_prefix), timeout=10)
    if not dev: sys.exit("no CyberPi advertising")
    dec = f3.Decoder(); got = {}
    async with BleakClient(dev, disconnected_callback=lambda c: print("!! disconnected")) as c:
        await c.start_notify(N, lambda _, d: [got.__setitem__(fr.idx, fr.text) for fr in dec.feed(d)])
        for i, e in enumerate(exprs, start=10):
            if not c.is_connected: print(f"{e[:45]:47s} -> (not sent, link down)"); continue
            await c.write_gatt_char(W, f3.encode_script(e, i, True), response=False)
            for _ in range(20):
                if i in got: break
                await asyncio.sleep(0.1)
            print(f"{e[:45]:47s} -> {got.get(i)}", flush=True)

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
