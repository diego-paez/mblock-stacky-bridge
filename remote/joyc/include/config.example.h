// Copy to config.h (git-ignored) and fill in. host/make_config.py can do it for you.
#pragma once
#define WIFI_SSID     "your-2.4GHz-ssid"
#define WIFI_PASSWORD "your-password"
// Must match CONFIG_STACKCHAN_MBOT_TELEOP_KEY in the Stack-chan firmware.
#define TELEOP_KEY    0x53544b31u
// Optional: Stack-chan's IP. Empty = find it by broadcast.
#define STACKCHAN_IP  ""
