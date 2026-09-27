# stackchan/

Integration with [stackchan-mcp](../../stackchan-mcp). With option A, no firmware change is needed: the hub calls the gateway's existing tools (`set_avatar`, `move_head`, `say`, `set_all_leds`).
Only option C (direct BLE) would put a firmware patch here (enable NimBLE in the xiaozhi build).
