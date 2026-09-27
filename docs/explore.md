# Camera-guided exploration (Stack-chan riding on the mBot)

## The problem: delays

| Step | Typical time |
|---|---|
| BLE command → mBot ack | 20–100 ms |
| Stack-chan photo → gateway | 0.5–1 s |
| Vision/LLM decision | 2–10 s |
| Head move + settle | ~0.6 s |

A robot that is driving while a 5 s decision is being made has moved on by the time the answer
arrives, so a continuous camera → steering loop would steer from stale images. So the controller
never does that.

## The controller: stop → look → decide → step

```
 ┌──────── STOP ────────┐   robot stationary: the last `done` has arrived
 │  LOOK   head −50/0/+50°, photos tagged with the exact pose; ultrasonic range
 │  DECIDE local rules (ms): continue into open, unvisited space; scan 8×45° when blocked
 │         LLM (s, asynchronous): looks at photos, sets goals by photo id
 │  STEP   ONE bounded primitive: straight ≤ 30 cm or turn ≤ 180°,
 │         closed-loop ON THE mBot (encoders/gyro), with the obstacle guard
 └── done <id> <reason> dist= yaw=   → pose update from what really happened
```

How each delay is neutralised:

1. **Photo latency.** The robot is stopped while photos are taken, so each image's pose is exact (`Observation.pose`).
2. **LLM latency.** `explore_set_goal(observation_id, bearing, distance)` is anchored to the pose where *that photo* was taken and turned into a world-frame point. Wherever the robot has moved since, the goal points to the same place (tested in `test_goal_from_old_photo_is_compensated_for_later_motion`).
3. **Link latency.** Steps are distances/angles executed and measured on the mBot, not velocity × time from the host. Adding 50 ms to every call changes nothing (`test_link_delay_does_not_change_outcome`).
4. **Lost messages.** If a `done` doesn't arrive, the host sends `stop`. If the host dies, the mBot watchdog stops within 3 s and reports `done … watchdog`.
5. **Drift.** The pose is integrated from the robot's *measured* `dist`/`yaw`, not from what was commanded (`test_gyro_noise_is_absorbed_by_measured_outcomes`).

## Architecture

```
Claude ──MCP──► stacky-hub (explore_start/status/set_goal/clear_goal/stop, mbot_*)
  │                  │ MCP over HTTP (camera, head, and, on the 1-to-1 path, the mBot)
  └───MCP (HTTP)──► stackchan-mcp gateway DAEMON ──Wi-Fi──► Stack-chan ──BLE──► mBot
```

The gateway must run in daemon mode so that Claude and stacky-hub can share it:

```bash
cd ../stackchan-mcp-mbot/gateway && uv run stackchan-mcp serve --transport streamable-http   # :8767/mcp
```

| stacky-hub flag | Meaning |
|---|---|
| `--robot gateway` | **1-to-1 path:** drive the mBot through Stack-chan's BLE (`mbot_step`/`mbot_turn` gateway tools) |
| `--robot link` (default) | Mac path: stacky-hub's own BLE link to the mBot (then Stack-chan must not hold the link) |
| `--head-yaw-sign -1` | if `move_head(yaw>0)` turns the head to the right |
| `--head-pitch 45` | camera tilt while exploring |

## Before the first run (calibration, once)

1. Upload `mbot/micropython/stacky_runtime.py` (v1.1) to the mBot.
2. Run `scripts/calibrate_mbot.py` (Mac BLE; Stack-chan off or not linked). It checks the encoder and gyro signs and prints which constant to flip.
3. Check the head: `move_head(yaw=40)` should turn Stack-chan's head to *its own* left. If not, use `--head-yaw-sign -1`.

## Safety

- Speed ≤ 50% of 60 rpm while exploring (≈10 cm/s), steps ≤ 30 cm, `max_steps` and `max_minutes` bounds.
- On the mBot: obstacle guard (< 10 cm, forward), watchdog, button B = emergency stop + lock (exploration ends with `robot:button`).
- `mbot_stop` is always instant. `explore_stop` ends after the current bounded step.
- **Known limit:** the ultrasonic sensor only sees a ±15° cone ahead. The robot can scrape an obstacle beside it (a chair leg passing at its side). Explore where there is room, and watch the first runs.
- Stack-chan on top raises the centre of gravity: keep speeds low. The runtime's slow-down near each target avoids jerky stops.
