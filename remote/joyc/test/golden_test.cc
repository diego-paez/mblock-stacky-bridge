// Host check of the joystick encoder against the shared golden vector (run by tests/test_teleop.py).
#include <cstdio>
#include <cstring>

#include "../src/teleop_packet.h"

int main() {
    uint8_t b[20];
    size_t n = teleop::encodeCmdVel(7, 0x12345678u, 150, -500, teleop::kEnable | teleop::kArmUp, 300, b);
    char hex[41] = {0};
    for (size_t i = 0; i < n; ++i) snprintf(hex + 2 * i, 3, "%02x", b[i]);
    printf("%s\n", hex);
    return strcmp(hex, "53540101070000007856341296000cfe05002c01") == 0 ? 0 : 1;
}
