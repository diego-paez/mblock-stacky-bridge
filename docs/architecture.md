# Architecture

Status 2026-09-26. **[V]** = verified on our hardware or in code; **[R]** = from research with sources; **[?]** = still to test.

## Decision: 1-to-1 BLE, Stack-chan = central, mBot2 = peripheral, CyberPiOS kept

```
   LLM client ──stdio MCP──► stackchan-mcp gateway (PC) ──Wi-Fi WS──► Stack-chan (CoreS3)
                                                                          │ NimBLE central
                                                                          │ write FFE3 / notify FFE2
                                                                          ▼  (1-to-1, no router)
                                                                     mBot2 (CyberPi, stock CyberPiOS)
                                                                     child's mBlock program running
```

Fallbacks, in order: **USB** (CoreS3 as USB host → CyberPi CH340; or PC → CyberPi for development), then Wi-Fi.

## What we verified on the real CyberPi (firmware 44.01.011)

| Fact | Evidence |
|---|---|
| USB is a CH340 (1A86:7523), 115200 8N1 | [V] `list_ports` |
| BLE name `Makeblock_LE<mac>`, service `FFE1`, write `FFE3`, notify `FFE2`, MTU 515 | [V] bleak GATT dump; [R] same UUIDs in mBlock web bundle |
| BLE carries the same F3 frames as USB | [V] `cyberpi.get_bri()`, `get_firmware_version()`, `get_battery()` answered over BLE |
| Live-mode namespace already has `mbot2`, `mbuild`, `event`, `communication`, `_thread`; `network` imports | [V] |
| Importing `espnow` in live mode kills the BLE link (needs power cycle) | [V]. Never send it. |
| Our codec (`hub/stacky_hub/transports/f3.py`) is byte-identical to Makeblock's and pi-codey's | [V] tests against both |

## The two message channels over one link

| Channel | Frame | Who handles it on the mBot | Use it for |
|---|---|---|---|
| **Upload-mode message** (type `0x03`) | `F3 hchk lenL lenH 03 {"message":m,"value":v} 00 chk F4` | The **child's uploaded program**: `@cyberpi.event.upload_broadcast(m)`, or the mBlock block "when I receive upload mode message" | Named events both ways: `face`, `go`, `obstacle`, `button`. The kid stays in charge of behavior. |
| **Live script** (type `0x28`) | `F3 … 28 mode idx <python> … F4`, reply `{"ret":…}` | CyberPiOS directly (runs the code, no program needed) | LLM-direct control and queries: drive, read sensors, show text |

**Test A: PASSED [V] (2026-09-26, BLE from Mac).** The uploaded program received every `ping`/`face` over BLE and answered `pong`. `upload_broadcast.set()` pushes unsolicited notifications on FFE2 (`alive` every 2 s), so no polling is needed. Device-sent JSON has spaces (`{"message": "pong", "value": "1"}`). Checksum rules are the same as ours.
**Test B: PASSED [V].** Live `0x28` scripts (`get_battery()` → `{"ret":90}` in about 0.1 s, `console.println`) run while the uploaded program keeps running. Its `alive` events never stopped, and `ping` got its `pong` about 0.4 s later. Both channels coexist on one BLE link.
The `face` message showed on the CyberPi screen [V, run 2].
[?] Button events: `@cyberpi.event.is_press("a")` sent nothing in two runs. Retesting with Makeblock's own form `import event` / `@event.is_press("a")`, plus a polled button-B fallback.

**Safety consequence:** live scripts execute arbitrary Python on the mBot. The Stack-chan side must send only a **whitelist of generated scripts** (drive with duration, stop, read sensors, show text) and never forward LLM text as code. Known-bad: `import espnow`.

## Stack-chan side (stackchan-mcp fork)

- BT is off today (`sdkconfig:962`). Copy the factory StackChan NimBLE config (`BT_NIMBLE_MEM_ALLOC_MODE_EXTERNAL=y`, MTU 512), which is proven on the same CoreS3 [R].
- Flash headroom is about 1.3 MB per OTA slot; NimBLE is about 100–150 KB [R]. Internal SRAM is the tight resource, so measure with `SystemInfo::PrintHeapStats()`.
- Wi-Fi STA + BLE connected is "stable" per ESP-IDF coexistence tables [R]. Risk: audio stutter. Measure it.
- New code:
  - `firmware/components/mbot_link/`: NimBLE central + F3 codec, a C++ port of `f3.py`.
  - `self.mbot.*` MCP tools in `boards/stackchan/`. Template: `esp_sparkbot_board.cc:178-230`, a chassis driven over UART.
  - mBot → Stack-chan events via `Application::SendStackChanEvent`, with a new `event_type: "mbot"`.
  - Local reflex table (offline play).
- Gateway: add `self.mbot.*` entries to `tool_map` and `_TOOL_LANES`, or run a separate `stacky-hub` MCP server (decision Q2) that calls them.

## USB fallback details

- PC → CyberPi over USB: working code in `scripts/check_usb_live.py` and `scripts/link_test.py usb`. mBlock must be closed, because it holds the port. Opening the port resets the board (DTR).
- CoreS3 as USB host: `espressif/usb_host_ch34x_vcp`. OTG 5 V needs AW9523 P0 bit 5 (the firmware currently writes P0=0b00000111, so it is off). USB flashing and logging are lost while in host mode [R].

## Rejected or parked

- **ESP-NOW `wifi_broadcast`:** proprietary frame, channel must match the router's, `espnow` import killed BLE. Parked.
- **Wired UART:** the CyberPi has UART2 on GPIO32/25 but no header, so it would need soldering. Rejected.
- **Arduino on CyberPi:** erases CyberPiOS and the child's blocks. Rejected.

## Internal RAM budget on the CoreS3 (measured 2026-09-26)

| Build | Free internal RAM (idle / minimum) | Result |
|---|---|---|
| no BLE (BLE init failed) | ~46 KB at boot, ~14 KB minimum during TLS | everything works |
| BLE started 4 s after boot | ~11 KB / 6.5 KB | **activation hangs** (TLS to api.tenclass.net cannot allocate) |
| BLE after activation (start gate) | 32 KB → `esp_bt_controller_init -4 Malloc failed` | Stack-chan OK, **no mBot link** |
| + `SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y` | 17 KB at boot, `esp-aes: Failed to allocate memory` | worse, reverted |

The BLE controller needs roughly 35–40 KB of internal (non-PSRAM) RAM, and it cannot use PSRAM. The Mac path (stacky-hub over the Mac's BLE) is unaffected and works.
