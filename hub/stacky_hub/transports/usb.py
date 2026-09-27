"""USB transport to a CyberPi (CH340, 115200 8N1). Quit mBlock first: it holds the port."""
import asyncio
import logging
import threading

import serial
from serial.tools import list_ports

log = logging.getLogger("stacky.usb")
CH340 = (0x1A86, 0x7523)


class UsbTransport:
    def __init__(self, port=None):
        self.port = port
        self.ser = None
        self.on_bytes = lambda data: None
        self._loop = None

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    async def connect(self):
        port = self.port or next((p.device for p in list_ports.comports() if (p.vid, p.pid) == CH340), None)
        if not port:
            raise ConnectionError("no CyberPi (CH340) on USB")
        self._loop = asyncio.get_running_loop()
        self.ser = serial.Serial(port, 115200, timeout=0.05)
        await asyncio.sleep(3)  # opening the port resets the CyberPi; wait for the program to start
        threading.Thread(target=self._reader, daemon=True).start()
        log.info("USB connected on %s", port)

    def _reader(self):
        while self.connected:
            try:
                data = self.ser.read(256)
            except serial.SerialException:
                break
            if data:
                self._loop.call_soon_threadsafe(self.on_bytes, data)

    async def write(self, data: bytes):
        await asyncio.to_thread(self.ser.write, data)

    async def close(self):
        if self.ser:
            self.ser.close()
