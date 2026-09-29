// Stacky teleop v1 packets, joystick side (header-only). Same layout as
// stackchan-mcp firmware/components/mbot_link/teleop_packet.h and
// hub/stacky_hub/teleop.py; test/golden_test.cc checks the golden vector.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace teleop {

constexpr uint16_t kPort = 8790;
enum : uint8_t { kCmdVel = 1, kStatus = 2, kHello = 3, kHelloAck = 4 };
enum : uint16_t {
    kEnable = 1 << 0, kEstop = 1 << 1, kArmUp = 1 << 2, kArmDown = 1 << 3,
    kGripOpen = 1 << 4, kGripClose = 1 << 5, kHome = 1 << 6,
};
enum : uint8_t { kMbotConnected = 1 << 0, kMbotLocked = 1 << 1, kTeleopActive = 1 << 2, kGatewayConnected = 1 << 3 };

struct Status {
    uint8_t flags = 0;
    uint8_t battery_pct = 0xFF;
    uint16_t dist_cm = 0xFFFF;
    uint8_t arm_deg = 0, grip_deg = 0;
    uint16_t rtt_ms = 0xFFFF;
};

inline void put16(uint8_t* p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
inline void put32(uint8_t* p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = (v >> (8 * i)) & 0xFF; }
inline uint16_t get16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }

inline size_t header(uint8_t type, uint32_t seq, uint32_t key, uint8_t* b) {
    b[0] = 'S'; b[1] = 'T'; b[2] = 1; b[3] = type;
    put32(b + 4, seq); put32(b + 8, key);
    return 12;
}

inline size_t encodeHello(uint32_t seq, uint32_t key, uint8_t* b) { return header(kHello, seq, key, b); }

inline size_t encodeCmdVel(uint32_t seq, uint32_t key, int16_t lin_mm_s, int16_t ang_mrad_s,
                           uint16_t buttons, uint16_t lease_ms, uint8_t* b) {
    header(kCmdVel, seq, key, b);
    put16(b + 12, (uint16_t)lin_mm_s);
    put16(b + 14, (uint16_t)ang_mrad_s);
    put16(b + 16, buttons);
    put16(b + 18, lease_ms);
    return 20;
}

// STATUS or HELLO_ACK -> true and fills `s`.
inline bool parseStatus(const uint8_t* b, size_t n, uint32_t key, Status* s) {
    if (n < 20 || b[0] != 'S' || b[1] != 'T' || b[2] != 1 || (b[3] != kStatus && b[3] != kHelloAck)) return false;
    uint32_t k = (uint32_t)b[8] | ((uint32_t)b[9] << 8) | ((uint32_t)b[10] << 16) | ((uint32_t)b[11] << 24);
    if (k != key) return false;
    s->flags = b[12]; s->battery_pct = b[13]; s->dist_cm = get16(b + 14);
    s->arm_deg = b[16]; s->grip_deg = b[17]; s->rtt_ms = get16(b + 18);
    return true;
}

}  // namespace teleop
