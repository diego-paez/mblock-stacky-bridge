# Stacky protocol v1.1

v1.1 (2026-09-27) adds closed-loop exploration primitives, odometry and clock sync. It is backwards compatible: every v1 command works unchanged, and `done` events carry extra fields.

One-to-one BLE link: **Stack-chan (or the Mac hub) = central**, **mBot2 CyberPi = peripheral**, stock CyberPiOS.
GATT: service `FFE1`, write `FFE3` (write-without-response), notify `FFE2`. The same bytes also work over USB (CH340, 115200 8N1).

## Transport frame (Makeblock "upload mode message", verified on hardware)

```
F3 | (F3+lenL+lenH)&FF | lenL lenH | 03 | {"message":"<topic>","value":"<text>"} | 00 | sum(payload)&FF | F4
payload = 03 + JSON + 00      len = len(payload)
```

The mBot sends JSON with spaces after `:` and `,`, so parsers must accept both forms.
Reference codec: `hub/stacky_hub/transports/f3.py`. The C++ port lives in the firmware.

Stacky uses **only this message channel for control**. Makeblock's live-script channel (type `0x28`, which runs arbitrary Python) is **never used for control**, because it would bypass the mBot-side safety checks. It is used only for diagnostics from the Mac.

## Topics

| Direction | Topic | Value |
|---|---|---|
| host → mBot | `sk` | `<id> <cmd> <args…>` (space-separated ASCII, id = 1..9999) |
| host → mBot | `sk_hb` | `<counter>`: heartbeat, sent every HB_PERIOD_S while connected; the value must change every time |
| mBot → host | `sk_ack` | `<id> ok [detail]` or `<id> err <reason>` |
| mBot → host | `sk_evt` | `<name> [args…]` |

## Delivery rules (important)

- The CyberPi keeps only the **latest value per topic**. So the host must **send one `sk` command at a time and wait for its `sk_ack`** (timeout 1.5 s) before sending the next.
  - On timeout, retry **once with the same id**. The mBot ignores a repeated id and answers `ok dup`.
  - `stop` is the exception: send it immediately, even while waiting for another ack.
- Heartbeats go on their own topic (`sk_hb`) so they can never overwrite a command.
- `sk_ack ok` means *accepted*. For motions and programs, completion arrives later as `sk_evt done <id>`.

## Commands (host → mBot)

Every argument is **clamped on the mBot** to the limits below. The host (Stack-chan firmware or the hub) clamps too; the two layers are independent.

| cmd | args | meaning |
|---|---|---|
| `fwd` / `back` | `<speed%> <sec>` | drive straight. speed% is 0..100 of MAX_RPM |
| `left` / `right` | `<speed%> <sec>` | turn in place |
| `stop` | | stop motors, cancel program, and drop the queue. Always accepted, even when locked |
| `led` | `<r> <g> <b> [all\|1..5]` | CyberPi LEDs, 0..255 |
| `arm` | `<deg>\|up\|down\|home` | S4 arm |
| `grip` | `<deg>\|open\|close\|home` | S3 gripper |
| `home` | | arm and gripper to 90° |
| `read` | | reply `sk_evt sensors dist=<cm> batt=<%> arm=<deg> grip=<deg> locked=<0/1>` |
| `prog` | `<step>;<step>;…` | run a mini-program (see below) |
| `straight` | `<cm> [speed%]` | **v1.1** drive straight *by distance*, closed-loop on the wheel encoders. cm −30..30 (− = backwards), speed default 30% |
| `turn` | `<deg> [speed%]` | **v1.1** turn in place *by angle*, closed-loop on the gyro. deg −180..180, **+ = left (counter-clockwise)**, speed default 30% |
| `odom` | `<period_ms>` | **v1.1** stream `odom` events every 100–5000 ms. 0 = off. Allowed while locked |
| `drive` | `<v_cm_s> <w_deg_s> [lease_ms]` | **v1.2** teleop in the ROS `cmd_vel` shape: forward speed (cm/s, ± ≈20) and turn rate (deg/s, ±90, **+ = left**). The wheels keep going only until the lease runs out (≤ 500 ms), so the sender must renew it; a renewal keeps the same session and ends with one `done … lease`. Forward is refused (set to 0) while an obstacle is closer than `OBSTACLE_CM`. Not allowed in programs |
| `sync` | `<host_ms>` | **v1.1** reply `sk_ack <id> ok <host_ms> <mbot_ms>`, used to measure round-trip time and clock offset. Allowed while locked |

Mini-program steps: any `fwd/back/left/right/straight/turn/led/arm/grip/home` command, plus `wait <sec>`.
Program limits:
- at most 20 steps and 30 s in total, checked before it starts (it is rejected as a whole if it exceeds them);
- runs one step at a time, and `stop` aborts it.

## Events (mBot → host)

`ready <version>` (at start) · `done <id> <reason> dist=<cm> yaw=<deg> t=<mbot_ms>` (a motion ended; see below) · `done <id> prog t=<ms>` (program finished) · `obstacle <cm>` (auto-stop) · `odom t=<ms> l=<cm> r=<cm> yaw=<deg>` (v1.1 stream) ·
`stopped <reason>` (`cmd` / `button` / `watchdog` / `obstacle`) · `locked` / `unlocked` · `button a|b` · `sensors …` · `hb <batt%>` (every 2 s)

### `done` reasons (v1.1)

Every motion ends with exactly one `done`, even when it is interrupted. `dist` and `yaw` are what the robot **actually** did during that motion, measured by the encoders and gyro (`none` if unavailable). This is what lets a slow host keep an exact pose estimate.

| reason | meaning |
|---|---|
| `target` | a `straight`/`turn` reached its distance/angle |
| `time` | a timed `fwd/back/left/right` ran its duration |
| `timeout` | a `straight`/`turn` did not reach its target in time (blocked wheel, no encoders) |
| `obstacle` | forward motion stopped by the ultrasonic guard |
| `cmd` / `button` / `watchdog` | stopped by `stop`, button B, or host silence |
| `replaced` | a new motion command replaced it |
| `lease` | **v1.2** a `drive` session ended because no renewal arrived in time |

## Exploration with delays (v1.1 design rule)

A camera decision on the host can take 1–10 s (photo upload plus vision model). So the robot is **never steered continuously from images**:

1. **Stop.** No motion is running (the last `done` has arrived).
2. **Look.** Stack-chan pans its head and takes photos, and the host reads `sensors`. The robot is stationary, so every image has an exact pose.
3. **Decide.** Local rules run in milliseconds; the LLM runs in seconds and only sets goals.
4. **Step.** Send one bounded `straight` (≤ 30 cm) or `turn` (≤ 180°). The mBot closes the loop itself, so link or LLM delay cannot change how far it goes.
5. **Wait** for `done`, then update the pose from its `dist`/`yaw`, and repeat.

The heartbeat watchdog still applies: if the host dies mid-step, the mBot stops within 3 s and reports `done … watchdog`.

## Safety limits (single source of truth, mirrored in code and checked by `tests/test_limits.py`)

| Name | Value | Why |
|---|---|---|
| `MAX_RPM` | 60 | about 20 cm/s with 6.5 cm wheels. 100% speed = 60 RPM |
| `MAX_MOVE_S` | 5.0 | a single motion never lasts longer |
| `MAX_PROG_S` | 30.0 | a whole program |
| `MAX_PROG_STEPS` | 20 | |
| `ARM_MIN/MAX/HOME` (S4) | 45 / 115 / 90 | 5° inside the hardware range 40 (lowest) – 120 (highest) |
| `GRIP_MIN/MAX/HOME` (S3) | 50 / 115 / 90 | 5° inside the hardware range 45 (closed) – 120 (open) |
| `SERVO_DEG_PER_S` | 40 | servos glide in 1° steps every 25 ms; they never jump and never move at start-up |
| `OBSTACLE_CM` | 10 | forward motion auto-stops below this (if an ultrasonic sensor is present) |
| `WATCHDOG_S` | 3.0 | while anything moves, stop if no `sk_hb`/`sk` arrives for this long |
| `HB_PERIOD_S` | 1.0 | host sends `sk_hb` every period while connected |
| `MAX_STEP_CM` | 30 | v1.1: longest single `straight` |
| `MAX_TURN_DEG` | 180 | v1.1: largest single `turn` |
| `MAX_DRIVE_LEASE_MS` | 500 | v1.2: longest a `drive` runs without renewal |
| `MAX_DRIVE_DEG_S` | 90 | v1.2: fastest `drive` turn rate |

## Local controls on the mBot (always win)

- **Button B = emergency stop + lock.** It stops everything, and motion commands then answer `err locked`.
- **Button A = unlock.**
- The screen shows `LOCKED` or `READY`.
