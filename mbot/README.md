# mbot/ (CyberPi side)

- `micropython/stacky.py`: library uploaded to the CyberPi (upload mode, keeps CyberPiOS). Connects, parses protocol messages, runs the heartbeat watchdog, exposes `on_command`, `send_event`, and `behavior(name)` registration.
- `mblock-extension/`: mBlock 5 extension (Extension Builder) producing "Stacky" blocks that call `stacky.py`.
- `examples/`: kid-level projects (obstacle → Stack-chan surprised, button → Stack-chan speaks, …).

First spike to run on the real device: which networking modules CyberPiOS MicroPython exposes (`umqtt`? `usocket`?).
