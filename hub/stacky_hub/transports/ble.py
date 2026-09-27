"""BLE transport to a CyberPi (Makeblock serial-over-BLE service FFE1)."""
import logging

from bleak import BleakClient, BleakScanner

log = logging.getLogger("stacky.ble")

SERVICE = "0000ffe1-0000-1000-8000-00805f9b34fb"
WRITE = "0000ffe3-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ffe2-0000-1000-8000-00805f9b34fb"


class BleTransport:
    def __init__(self, name_prefix="Makeblock_LE", address=None):
        self.name_prefix = name_prefix
        self.address = address
        self.client = None
        self.on_bytes = lambda data: None

    @property
    def connected(self):
        return self.client is not None and self.client.is_connected

    async def connect(self):
        if self.address:
            dev = await BleakScanner.find_device_by_address(self.address, timeout=10)
        else:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, a: (a.local_name or d.name or "").startswith(self.name_prefix), timeout=10)
        if dev is None:
            raise ConnectionError(f"no mBot advertising as {self.address or self.name_prefix + '*'} "
                                  "(is it on, and not connected to something else?)")
        self.client = BleakClient(dev, disconnected_callback=lambda c: log.warning("BLE disconnected"))
        await self.client.connect()
        await self.client.start_notify(NOTIFY, lambda _, d: self.on_bytes(bytes(d)))
        log.info("BLE connected to %s", dev.name)

    async def write(self, data: bytes):
        await self.client.write_gatt_char(WRITE, data, response=False)

    async def close(self):
        if self.client:
            await self.client.disconnect()
