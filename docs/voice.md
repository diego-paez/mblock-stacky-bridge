# Voice control (today: Mac path; later: Stack-chan 1-to-1 path)

## Path A: works today
Stack-chan mic → stackchan-mcp `listen()` → Claude → stacky-hub `mbot_*` → Mac BLE → mBot.

1. Upload `mbot/micropython/stacky_runtime.py` to the CyberPi (mBlock → Python → Upload).
2. Copy `.mcp.json.example` to `.mcp.json` (or add both servers to Claude Desktop/Code).
3. Start Claude in this folder and give it the loop below.

## Path B: target, 1-to-1
Same tools, but they are implemented in the Stack-chan firmware (`self.mbot.*` via the stackchan-mcp gateway, branch `feat/mbot-ble`). Stack-chan talks BLE directly to the mBot. Use **one path at a time**: the mBot accepts one BLE central.

## Loop prompt (paste into Claude)
> You are Stack-chan's brain and you also drive the mBot. Repeat: call `listen(language="ja", motion="thinking")`. If the child asks the mBot to move, use `mbot_move` (speed 30-50%, 1-2 s), `mbot_arm`, `mbot_gripper`, `mbot_set_led`, or `mbot_run_program` for sequences. If you hear とまれ / 止まって / ストップ / stop, call `mbot_stop` **first**, before anything else. Confirm briefly with `say()` in Japanese and a matching `set_avatar` face. If a tool answers REFUSED: locked, tell the child to press button A on the mBot.

## Example phrases
| Child says | Tools |
|---|---|
| まえにすすんで | `mbot_move(forward, 40, 1.5)` |
| うしろ | `mbot_move(backward, 40, 1)` |
| ひだりにまがって | `mbot_move(left, 40, 0.8)` |
| とまれ | `mbot_stop` |
| あかくして | `mbot_set_led(255, 0, 0)` |
| うでをあげて / さげて | `mbot_arm(up)` / `mbot_arm(down)` |
| つかんで / はなして | `mbot_gripper(close)` / `mbot_gripper(open)` |
| おどって | `mbot_run_program("led 255 0 255; left 50 0.5; right 50 0.5; arm up; arm down; led 0 0 0")` |
