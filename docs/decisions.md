# Open decisions

| # | Question | Options | Default if undecided | Answer |
|---|---|---|---|---|
| Q1 | Transport mBot ↔ system | A Wi-Fi→PC hub · B USB/BLE-dongle live · C direct BLE one-to-one · D ESP-NOW | A (+B for bring-up) | **Wi-Fi now (A), direct BLE later (C)**, behind a pluggable transport |
| Q2 | Where the Stack-chan-side library lives | separate `stacky-hub` MCP server · plugin/PR inside stackchan-mcp gateway | separate server | **Separate `stacky-hub` MCP server** |
| Q3 | Keep CyberPiOS on mBot2 | keep (blocks + MicroPython upload) · replace with Arduino | keep | **Keep CyberPiOS.** Phase-1 spike: probe Makeblock BLE for a reusable channel |
| Q4 | Remote controller | skip for now · CyberPi joystick as remote · port ESP-NOW remote into stackchan-mcp fw | skip | **Skip for now** |
| Q5 | Who decides reactions to mBot events | LLM every time · local reflex rules + LLM for conversation | reflex + LLM | **Local reflex rules (child-editable) + LLM notified** |
| Q6 | Must it work without router/PC? | no · yes (forces C/D) | no | **Yes, basic offline play**, so phase 2 BLE carries local reflexes |

## Consequences of the answers so far

- Offline play plus BLE later means the **reflex rules must run on the robots**, not only in the hub.
  In phase 1 the hub runs them; in phase 2 the same rule table is pushed to the robots and runs over BLE when the hub is unreachable.
- Therefore the protocol stays compact (under 180 B JSON, or a binary encoding later) and transport-agnostic from day one.
- Phase 2 needs BLE in a stackchan-mcp **firmware fork** (NimBLE, coexisting with Wi-Fi). Q3 decides the mBot side.
- **Reprogramming scope:** all four levels: trigger kid-made behaviors, tune params, sandboxed JSON mini-programs (`seq`), and render mini-programs as mBlock-style blocks for the child.
- **Language:** kid-facing blocks and phrases in **Japanese** (identifiers and code stay English).
- **Risk to watch:** keeping CyberPiOS *and* doing direct BLE depends on the spike. If Makeblock's BLE can't carry our messages, the offline fallback is ESP-NOW via `wifi_broadcast` reverse-engineering, or revisiting Arduino.

## Update 2026-09-26 (after research + hardware tests)
- **Transport: 1-to-1 BLE, Stack-chan (NimBLE central) → CyberPi (stock BLE peripheral FFE1/FFE3/FFE2)**, verified from the Mac. Fallback: USB. Wi-Fi only for Stack-chan ↔ PC/LLM.
- Protocol = Makeblock F3 framing. Upload-mode messages (0x03, JSON) for kid-program events; live scripts (0x28) for direct LLM control. Our own JSON envelope (docs/protocol.md) rides inside the 0x03 `value` when needed.
- Test A (messages both ways with kid's program) and Test B (live scripts concurrent with the program) **passed** on hardware, 2026-09-26.
