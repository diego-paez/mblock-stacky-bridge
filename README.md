# mblock-stacky-bridge

Stack-chan (M5Stack CoreS3 + [stackchan-mcp](../stackchan-mcp)) keeps talking to an LLM **and** drives a
Makeblock mBot2 (CyberPi) **safely**, over a **one-to-one BLE link**. CyberPiOS is kept, so the child can
still program the mBot in mBlock blocks.

```
 voice ─► Stack-chan mic ─► stackchan-mcp gateway (PC) ─► LLM (Claude)
                                   │
                   ┌───────────────┴────────────────┐
     Path B (target, 1-to-1)                 Path A (works today)
     gateway self.mbot.* ─► Stack-chan       stacky-hub MCP ─► Mac BLE
          NimBLE central ─┐                              ┌──┘
                          ▼  BLE FFE1, Stacky v1 msgs    ▼
                      mBot2 CyberPi running stacky_runtime.py
                      (enforces all limits, watchdog, B = e-stop)
```

## Status

| Part | State |
|---|---|
| BLE link, framing, both message channels | ✅ verified on hardware (docs/architecture.md, Tests A and B) |
| `mbot/micropython/stacky_runtime.py`: safe executor on the mBot | ✅ 40 simulated tests + first hardware run OK 2026-09-26 (drive, clamping, arm, gripper, done events) |
| Protocol v1.1: closed-loop `straight`/`turn`, odometry, sync, `done` outcomes | ✅ runtime + hub + 88 tests (sim physics, full-stack loopback). ⏳ calibration on hardware (`scripts/calibrate_mbot.py`) |
| Camera exploration (`hub/stacky_hub/explore.py`, `explore_*` tools) | ✅ simulated room: no collisions, delay compensation, goal-from-photo. ⏳ hardware. See docs/explore.md |
| `hub/`: Python library + `stacky-hub` MCP server (Mac path) | ✅ tests + stdio smoke test + CLI over BLE on hardware |
| Stack-chan firmware + gateway `self.mbot.*` (1-to-1 path) | ⚠️ flashed 2026-09-26: Stack-chan works normally (activation, gateway, tools), but **BLE controller init fails with ESP_ERR_NO_MEM**: not enough internal SRAM next to Wi-Fi + TLS + wake word + camera. See docs/architecture.md "Internal RAM budget". Branch `feat/mbot-ble`, uncommitted |

## Safety (enforced on the mBot, and again on the host)

- Speed ≤ 60 rpm (about 20 cm/s). Each motion ≤ 5 s. Programs ≤ 20 steps / 30 s.
- Arm S4 40–120° (home 90). Gripper S3 45 (closed) – 120° (open) (home 90). Servos glide at 90°/s.
- Forward motion stops below 10 cm. Watchdog stops everything 3 s after the host goes silent.
- **Button B = emergency stop + lock**, button A = unlock. Only named commands are accepted: never code.

Full spec: [docs/protocol.md](docs/protocol.md). Voice setup: [docs/voice.md](docs/voice.md). Exploration: [docs/explore.md](docs/explore.md).

## Quick start (Mac path)

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt "mcp>=1.27,<2" pytest
# 1. mBlock → Python editor → Upload mode → upload mbot/micropython/stacky_runtime.py
# 2. manual test:
.venv/bin/python scripts/mbot_cli.py          # then: led 0 255 0 | fwd 30 1 | arm up | grip close | stop
# 3. tests:
cd tests && ../.venv/bin/python -m pytest -q
```

## Layout

| Path | Contents |
|---|---|
| `docs/` | architecture + verified facts, protocol v1, decisions, voice |
| `mbot/micropython/stacky_runtime.py` | the program the mBot runs |
| `mbot/examples/` | link tests |
| `hub/stacky_hub/` | `transports/f3.py` codec, `ble.py`/`usb.py`, `link.py` (acks, retry, heartbeat), `commands.py` (host limits), `server.py` (MCP) |
| `scripts/` | `mbot_cli.py` (manual), `link_test.py`, `ble_probe.py` / `check_usb_live.py` (diagnostics) |
| `tests/` | runtime safety, host validation, end-to-end loopback, limits consistency |
