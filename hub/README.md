# hub/ (PC side, Python): `stacky_hub`

- `transports/f3.py`: Makeblock F3 codec (byte-verified against hardware). Reference for the C++ port.
- `transports/ble.py`, `transports/usb.py`: byte transports.
- `link.py`: `MbotLink`, protocol v1 delivery rules (one command in flight, ack + one retry with the same id, stop bypasses the queue, `sk_hb` heartbeat).
- `commands.py` + `limits.py`: host-side validation and clamping.
- `server.py`: `stacky-hub` MCP server (`python -m stacky_hub [--usb]`).
