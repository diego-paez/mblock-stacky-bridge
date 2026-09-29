// Stacky teleop controller: M5StickC Plus + Mini JoyC hat -> Wi-Fi UDP (20 Hz) -> Stack-chan -> BLE -> mBot2.
// Spec: ../../docs/teleop.md
//
// Hold it upright: screen facing you, USB at the bottom, stick on top.
//
//   MENU            stick up/down = move, A = select. Shown at start and after every B.
//     TILT drive    tip forward/back = forward/reverse, roll left/right = turn; full speed at 45 deg,
//                   8 deg dead zone. Wheels move ONLY while A is held (dead-man).
//                   Stick: up/down = arm, left/right = gripper.
//     STICK drive   stick drives. Starts armed (stick must be centred).
//     ARM & grip    stick: up/down = arm, left/right = gripper. Wheels stay stopped.
//     Calibrate     hold the controller the way you will drive, press A: that pose is zero tilt.
//     Home arm      arm and gripper to 90 deg.
//   B (side)        E-STOP, back to the menu. Always works.
//   long stick press (1 s, while driving): arm and gripper home.
//
// Safety: nothing moves from the menu. If this controller goes silent (battery, range, crash),
// Stack-chan stops the mBot after 300 ms and the mBot stops by itself when its lease runs out.
#include <M5Unified.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "M5HatMiniJoyC.h"

#include "config.h"
#include "teleop_packet.h"

// Tilt signs, measured on this StickC Plus on 28 Sep 2026: tipping the top away and
// rolling the right side down both read negative from the raw IMU, so both are flipped.
#ifndef TILT_PITCH_SIGN
#define TILT_PITCH_SIGN -1
#endif
#ifndef TILT_ROLL_SIGN
#define TILT_ROLL_SIGN -1
#endif

namespace {

// ---- tuning ----
constexpr uint8_t kJoyAddr = 0x54;        // Mini JoyC hat, I2C on G0 (SDA) / G26 (SCL)
constexpr int kPeriodMs = 50;             // 20 Hz while driving
constexpr int kIdlePeriodMs = 200;        // 5 Hz otherwise (keeps the status coming)
constexpr int16_t kMaxLinMmS = 150;       // safe top speed; the mBot tops out near 204 mm/s
constexpr int16_t kMaxAngMradS = 1200;    // ~69 deg/s; Stack-chan and the mBot clamp at 90
constexpr uint16_t kLeaseMs = 300;
constexpr float kStickDeadzone = 0.15f;   // the resting X wanders by about +-100 counts
constexpr int kAdcSpan = 1500;            // counts from centre to full stick (measured travel >= 1700)
constexpr float kServoThreshold = 0.6f;
constexpr float kTiltDeadDeg = 8.0f;
constexpr float kTiltFullDeg = 45.0f;
constexpr float kTiltGiveUpDeg = 70.0f;   // beyond this it was dropped or put down: stop
constexpr uint32_t kLongPressMs = 1000;

enum class Mode { kMenu, kTilt, kStick, kArm, kCalibrate };
const char* const kMenuItems[] = {"TILT drive", "STICK drive", "ARM & grip", "Calibrate tilt", "Home arm"};
constexpr int kMenuCount = 5;

// ---- state ----
WiFiUDP udp;
IPAddress stackchan;
bool have_host = false;
uint32_t seq = 0;
teleop::Status status;
uint32_t last_status_ms = 0, last_hello_ms = 0, last_send_ms = 0, last_draw_ms = 0, last_dump_ms = 0;

M5HatMiniJoyC joyc;
M5Canvas canvas(&M5.Display);
bool joy_ok = false;
int center[2] = {2048, 2048};
int raw[3] = {0, 0, 1};                   // ADC X, ADC Y, button (1 = released)
float jx = 0, jy = 0;
bool press_prev = false, long_done = false, short_press = false, long_press = false;
uint32_t press_start_ms = 0;

Mode mode = Mode::kMenu;
int menu_idx = 0;
uint32_t menu_move_ms = 0;
bool stick_armed = false;
int estop_packets = 0;
bool home_pending = false;
bool tilt_calibrated = false;
float g_filt[3] = {0, 0, 1};
float pitch0 = 0, roll0 = 0;              // calibrated zero, degrees
float pitch = 0, roll = 0;                // relative to zero, degrees
bool tilt_lost = false;                   // beyond kTiltGiveUpDeg: needs A released and pressed again
int16_t lin = 0, ang = 0;
uint16_t buttons = 0;
const char* toast = nullptr;
uint32_t toast_until = 0;

// ---- inputs ----
bool readJoy(int out[3]) {
    Wire.beginTransmission(kJoyAddr);
    if (Wire.endTransmission() != 0) return false;
    out[0] = joyc.getADCValue(ADC_X);     // right = higher (measured 2026-09-27)
    out[1] = joyc.getADCValue(ADC_Y);     // up = higher
    out[2] = joyc.getButtonStatus() ? 1 : 0;
    return true;
}

void joyLed(uint8_t r, uint8_t g, uint8_t b) { joyc.setLEDColor(((uint32_t)r << 16) | ((uint32_t)g << 8) | b); }

float shape(float a, float dead) {
    if (a > 1) a = 1;
    if (a < -1) a = -1;
    float m = fabsf(a);
    return m < dead ? 0 : (a > 0 ? 1 : -1) * (m - dead) / (1 - dead);
}

void readStick(uint32_t now) {
    short_press = long_press = false;
    if (!readJoy(raw)) {
        joy_ok = false;
        jx = jy = 0;
        return;
    }
    joy_ok = true;
    jx = shape((float)(raw[0] - center[0]) / kAdcSpan, kStickDeadzone);
    jy = shape((float)(raw[1] - center[1]) / kAdcSpan, kStickDeadzone);
    const bool pressed = raw[2] == 0;
    if (pressed && !press_prev) {
        press_start_ms = now;
        long_done = false;
    }
    if (pressed && !long_done && now - press_start_ms >= kLongPressMs) {
        long_done = true;
        long_press = true;
    }
    if (!pressed && press_prev && !long_done) short_press = true;
    press_prev = pressed;
}

float wrap180(float d) {
    while (d > 180) d -= 360;
    while (d < -180) d += 360;
    return d;
}

// Pitch = rotation about the controller's left-right axis (top tips away = +),
// roll = rotation about its long axis (right side down = +). Accelerometer only:
// fine for a hand-held pose, and it cannot drift.
void readTilt() {
    float ax, ay, az;
    if (!M5.Imu.getAccel(&ax, &ay, &az)) return;
    const float k = 0.25f;                                  // low-pass, ~0.2 s at 20 Hz reads
    g_filt[0] += k * (ax - g_filt[0]);
    g_filt[1] += k * (ay - g_filt[1]);
    g_filt[2] += k * (az - g_filt[2]);
    const float p = atan2f(g_filt[1], g_filt[2]) * 57.2958f;
    const float r = atan2f(g_filt[0], g_filt[2]) * 57.2958f;
    pitch = TILT_PITCH_SIGN * wrap180(p - pitch0);
    roll = TILT_ROLL_SIGN * wrap180(r - roll0);
}

void calibrateTilt() {
    float s[3] = {0, 0, 0};
    for (int i = 0; i < 25; ++i) {                          // 0.5 s average
        float ax, ay, az;
        M5.Imu.getAccel(&ax, &ay, &az);
        s[0] += ax; s[1] += ay; s[2] += az;
        delay(20);
    }
    for (int i = 0; i < 3; ++i) g_filt[i] = s[i] / 25;
    pitch0 = atan2f(g_filt[1], g_filt[2]) * 57.2958f;
    roll0 = atan2f(g_filt[0], g_filt[2]) * 57.2958f;
    tilt_calibrated = true;
}

float tiltAxis(float deg) {
    float m = fabsf(deg);
    if (m < kTiltDeadDeg) return 0;
    float a = (m - kTiltDeadDeg) / (kTiltFullDeg - kTiltDeadDeg);
    if (a > 1) a = 1;
    return deg > 0 ? a : -a;
}

void showToast(const char* t, uint32_t now) {
    toast = t;
    toast_until = now + 1500;
}

void goMenu() {
    mode = Mode::kMenu;
    stick_armed = false;
    joyLed(0, 0, 30);
}

// ---- state machine ----
void update(uint32_t now) {
    if (M5.BtnB.wasPressed()) {                             // E-STOP: always, from anywhere
        estop_packets = 10;
        joyLed(60, 0, 0);
        showToast("E-STOP", now);
        mode = Mode::kMenu;
        stick_armed = false;
        return;
    }
    const bool a_pressed = M5.BtnA.wasPressed();

    switch (mode) {
    case Mode::kMenu:
        if (jy > 0.5f || jy < -0.5f) {
            if (now - menu_move_ms > 300) {
                menu_move_ms = now;
                menu_idx = (menu_idx + (jy > 0 ? kMenuCount - 1 : 1)) % kMenuCount;   // up = previous
            }
        } else {
            menu_move_ms = 0;
        }
        if (!a_pressed) break;
        switch (menu_idx) {
        case 0:
            if (!tilt_calibrated) {
                mode = Mode::kCalibrate;
                showToast("Calibrate first", now);
            } else {
                mode = Mode::kTilt;
                tilt_lost = false;
                joyLed(0, 40, 40);
            }
            break;
        case 1:
            if (fabsf(jx) < 0.05f && fabsf(jy) < 0.05f) {
                mode = Mode::kStick;
                stick_armed = true;
                joyLed(0, 60, 0);
            } else {
                showToast("Centre the stick", now);
            }
            break;
        case 2:
            mode = Mode::kArm;
            joyLed(0, 0, 60);
            break;
        case 3:
            mode = Mode::kCalibrate;
            break;
        case 4:
            home_pending = true;
            showToast("Home", now);
            break;
        }
        break;
    case Mode::kCalibrate:
        if (a_pressed) {
            calibrateTilt();
            showToast("Tilt zero set", now);
            goMenu();
            menu_idx = 0;
        }
        break;
    case Mode::kTilt:
    case Mode::kStick:
    case Mode::kArm:
        if (long_press) {
            home_pending = true;
            showToast("Home", now);
        }
        break;
    }
}

void buildCommand(uint32_t now) {
    buttons = 0;
    lin = ang = 0;
    if (estop_packets > 0) {
        buttons |= teleop::kEstop;
        --estop_packets;
        return;
    }
    if (!joy_ok && mode != Mode::kTilt) return;
    const bool servo_stick = mode == Mode::kTilt || mode == Mode::kArm;
    bool enable = false;

    if (mode == Mode::kStick && stick_armed) {
        enable = true;
        lin = (int16_t)(jy * kMaxLinMmS);
        ang = (int16_t)(-jx * kMaxAngMradS);                // stick right = turn right = negative (ROS)
    } else if (mode == Mode::kTilt) {
        const bool held = M5.BtnA.isPressed();
        if (!held) tilt_lost = false;                       // releasing A re-allows driving
        if (fabsf(pitch) > kTiltGiveUpDeg || fabsf(roll) > kTiltGiveUpDeg) tilt_lost = true;
        enable = true;                                      // servos work without A; wheels need A
        if (held && !tilt_lost) {
            lin = (int16_t)(tiltAxis(pitch) * kMaxLinMmS);  // tip forward = forward
            ang = (int16_t)(-tiltAxis(roll) * kMaxAngMradS);// roll right = turn right
        }
    } else if (mode == Mode::kArm) {
        enable = true;
    }
    if (!enable) return;
    buttons |= teleop::kEnable;
    if (home_pending) {
        buttons |= teleop::kHome;
        home_pending = false;
    }
    if (servo_stick && joy_ok) {
        if (jy > kServoThreshold) buttons |= teleop::kArmUp;
        if (jy < -kServoThreshold) buttons |= teleop::kArmDown;
        if (jx > kServoThreshold) buttons |= teleop::kGripOpen;
        if (jx < -kServoThreshold) buttons |= teleop::kGripClose;
    }
    (void)now;
}

// ---- screen (135 x 240, portrait) ----
constexpr uint16_t kBlue = 0x3D7F;       // robot body
constexpr uint16_t kDarkBlue = 0x1A6B;

bool linkUp(uint32_t now) { return have_host && now - last_status_ms < 2000; }

// The little robot: slides and looks where it is going. face: 0 sleep, 1 happy, 2 estop, 3 surprised
void drawRobot(int cx, int cy, float fwd, float turn, int face, uint16_t body) {
    const int ox = (int)(turn * 18), oy = (int)(-fwd * 22);
    const int x = cx + ox - 30, y = cy + oy - 24;
    if (fabsf(fwd) > 0.05f || fabsf(turn) > 0.05f) {         // motion lines behind it
        for (int i = 1; i <= 3; ++i) {
            const int lx = cx - (int)(turn * 18 * i * 0.6f), ly = cy + (int)(fwd * 22 * i * 0.6f);
            canvas.drawFastHLine(lx - 18, ly + 26, 36, TFT_DARKGREY);
        }
    }
    canvas.fillRect(x - 4, y + 34, 10, 16, TFT_DARKGREY);    // wheels
    canvas.fillRect(x + 54, y + 34, 10, 16, TFT_DARKGREY);
    canvas.fillRoundRect(x, y, 60, 48, 10, body);
    canvas.drawRoundRect(x, y, 60, 48, 10, kDarkBlue);
    canvas.drawFastVLine(x + 30, y - 10, 10, TFT_LIGHTGREY);  // antenna
    canvas.fillCircle(x + 30, y - 12, 3, face == 2 ? TFT_RED : TFT_YELLOW);
    const int ex1 = x + 18, ex2 = x + 42, ey = y + 18;
    const int px = (int)(turn * 4), py = (int)(-fwd * 3);
    switch (face) {
    case 0:                                                  // asleep
        canvas.drawFastHLine(ex1 - 6, ey, 12, TFT_WHITE);
        canvas.drawFastHLine(ex2 - 6, ey, 12, TFT_WHITE);
        canvas.drawFastHLine(x + 24, y + 36, 12, TFT_WHITE);
        break;
    case 2:                                                  // E-stop: X eyes
        canvas.drawLine(ex1 - 5, ey - 5, ex1 + 5, ey + 5, TFT_WHITE);
        canvas.drawLine(ex1 - 5, ey + 5, ex1 + 5, ey - 5, TFT_WHITE);
        canvas.drawLine(ex2 - 5, ey - 5, ex2 + 5, ey + 5, TFT_WHITE);
        canvas.drawLine(ex2 - 5, ey + 5, ex2 + 5, ey - 5, TFT_WHITE);
        canvas.drawFastHLine(x + 22, y + 37, 16, TFT_WHITE);
        break;
    default:
        canvas.fillCircle(ex1, ey, 7, TFT_WHITE);
        canvas.fillCircle(ex2, ey, 7, TFT_WHITE);
        canvas.fillCircle(ex1 + px, ey + py, 3, TFT_BLACK);
        canvas.fillCircle(ex2 + px, ey + py, 3, TFT_BLACK);
        if (face == 3) canvas.drawCircle(x + 30, y + 37, 4, TFT_WHITE);
        else canvas.drawArc(x + 30, y + 30, 10, 9, 30, 150, TFT_WHITE);   // smile
        break;
    }
}

void drawBar(int x, int y, int w, const char* label, int v, int lo, int hi, uint16_t col) {
    canvas.setCursor(x, y);
    canvas.printf("%s %3d", label, v);
    const int bx = x + 52, bw = w - 52;
    canvas.drawRect(bx, y, bw, 8, TFT_DARKGREY);
    int f = v <= lo ? 0 : v >= hi ? bw - 2 : (v - lo) * (bw - 2) / (hi - lo);
    canvas.fillRect(bx + 1, y + 1, f, 6, col);
}

void draw(uint32_t now) {
    canvas.fillScreen(TFT_BLACK);
    canvas.setTextSize(1);
    canvas.setTextColor(TFT_WHITE);
    const bool link = linkUp(now);
    const bool mbot = link && (status.flags & teleop::kMbotConnected);

    // top bar: mode + links
    const char* title = "MENU";
    uint16_t tc = TFT_DARKGREY;
    switch (mode) {
    case Mode::kTilt: title = "TILT"; tc = TFT_DARKCYAN; break;
    case Mode::kStick: title = "STICK"; tc = TFT_DARKGREEN; break;
    case Mode::kArm: title = "ARM"; tc = TFT_NAVY; break;
    case Mode::kCalibrate: title = "CALIBRATE"; tc = TFT_PURPLE; break;
    default: break;
    }
    canvas.fillRect(0, 0, 135, 16, tc);
    canvas.setCursor(4, 4);
    canvas.print(title);
    canvas.setCursor(80, 4);
    canvas.printf("%3d%%", (int)M5.Power.getBatteryLevel());
    // Link dot: red = no Wi-Fi, orange = looking for Stack-chan, yellow = no mBot, blue = all linked.
    const uint16_t dot = WiFi.status() != WL_CONNECTED ? TFT_RED : !link ? TFT_ORANGE : mbot ? 0x04FF : TFT_YELLOW;
    canvas.fillCircle(126, 8, 6, dot);
    canvas.drawCircle(126, 8, 6, TFT_WHITE);

    if (mode == Mode::kMenu) {
        drawRobot(67, 52, 0, 0, estop_packets > 0 ? 2 : 0, estop_packets > 0 ? TFT_RED : kBlue);
        for (int i = 0; i < kMenuCount; ++i) {
            const int y = 96 + i * 20;
            if (i == menu_idx) canvas.fillRoundRect(2, y - 3, 131, 17, 4, TFT_DARKCYAN);
            canvas.setCursor(10, y + 1);
            canvas.print(kMenuItems[i]);
            if (i == 0 && !tilt_calibrated) {
                canvas.setCursor(112, y + 1);
                canvas.print("*");
            }
        }
        canvas.setCursor(4, 200);
        canvas.print("stick: move  A: select");
    } else if (mode == Mode::kCalibrate) {
        drawRobot(67, 60, 0, 0, 1, kBlue);
        canvas.setCursor(6, 110);
        canvas.println("Hold it the way");
        canvas.setCursor(6, 124);
        canvas.println("you will drive,");
        canvas.setCursor(6, 138);
        canvas.println("then press A.");
        canvas.setCursor(6, 160);
        canvas.printf("pitch %+5.1f roll %+5.1f", pitch, roll);
    } else {
        const float f = (float)lin / kMaxLinMmS, t = (float)-ang / kMaxAngMradS;
        int face = 1;
        uint16_t body = kBlue;
        if (mode == Mode::kTilt && !M5.BtnA.isPressed()) { face = 0; }
        if (tilt_lost && mode == Mode::kTilt) { face = 3; body = TFT_ORANGE; }
        if (link && status.dist_cm != 0xFFFF && status.dist_cm < 15) face = 3;
        drawRobot(67, 78, f, t, face, body);
        canvas.setCursor(4, 138);
        if (mode == Mode::kTilt) {
            canvas.printf("tilt %+4.0f  roll %+4.0f", pitch, roll);
            canvas.setCursor(4, 150);
            canvas.print(M5.BtnA.isPressed() ? (tilt_lost ? "too steep: let go of A" : "DRIVING") : "hold A to drive");
        } else if (mode == Mode::kStick) {
            canvas.printf("v %+4d mm/s", lin);
            canvas.setCursor(4, 150);
            canvas.printf("w %+5d mrad/s", ang);
        } else {
            canvas.print("stick: arm / gripper");
        }
        drawBar(4, 166, 127, "arm ", status.arm_deg, 45, 115, TFT_CYAN);
        drawBar(4, 180, 127, "grip", status.grip_deg, 50, 115, TFT_MAGENTA);
    }

    // bottom: robot status
    canvas.drawFastHLine(0, 204, 135, TFT_DARKGREY);
    canvas.setCursor(4, 210);
    if (WiFi.status() != WL_CONNECTED) canvas.print("Wi-Fi connecting...");
    else if (!link) canvas.print("finding Stack-chan...");
    else if (!mbot) canvas.print("mBot not linked");
    else {
        canvas.printf("mBot %s%%", status.battery_pct == 0xFF ? "?" : String(status.battery_pct).c_str());
        if (status.dist_cm != 0xFFFF) canvas.printf("  %dcm", status.dist_cm);
        if (status.flags & teleop::kMbotLocked) canvas.print(" LOCK");
    }
    canvas.setCursor(4, 224);
    canvas.print("B = STOP / menu");

    if (toast && now < toast_until) {
        canvas.fillRoundRect(8, 100, 119, 26, 6, TFT_WHITE);
        canvas.setTextColor(TFT_BLACK);
        canvas.setCursor(14, 109);
        canvas.print(toast);
    }
    canvas.pushSprite(0, 0);
}

// ---- network ----
void sendPacket(size_t n, const uint8_t* b, IPAddress to) {
    udp.beginPacket(to, teleop::kPort);
    udp.write(b, n);
    udp.endPacket();
}

void hello(uint32_t now) {
    if (now - last_hello_ms < 1000) return;
    last_hello_ms = now;
    uint8_t b[12];
    size_t n = teleop::encodeHello(++seq, TELEOP_KEY, b);
    // Broadcasts do not always reach Stack-chan (measured 28 Sep), so ask its known
    // address first and broadcast as a fallback.
    IPAddress known;
    if (strlen(STACKCHAN_IP) > 0 && known.fromString(STACKCHAN_IP)) sendPacket(n, b, known);
    sendPacket(n, b, WiFi.broadcastIP());
}

void receive(uint32_t now) {
    while (udp.parsePacket() > 0) {
        uint8_t b[64];
        int len = udp.read(b, sizeof(b));
        teleop::Status s;
        if (len > 0 && teleop::parseStatus(b, len, TELEOP_KEY, &s)) {
            status = s;
            stackchan = udp.remoteIP();
            have_host = true;
            last_status_ms = now;
        }
    }
}

}  // namespace

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    M5.Power.setExtOutput(true);                         // 5 V to the hat
    M5.Display.setRotation(0);                           // portrait, USB at the bottom
    canvas.setColorDepth(16);
    canvas.createSprite(135, 240);
    Serial.begin(115200);
    // I2C bus recovery: if the hat was left mid-transfer, clock SCL until it lets go of SDA.
    pinMode(0, INPUT_PULLUP);
    pinMode(26, OUTPUT_OPEN_DRAIN);
    for (int i = 0; i < 9 && digitalRead(0) == LOW; ++i) {
        digitalWrite(26, LOW);
        delayMicroseconds(10);
        digitalWrite(26, HIGH);
        delayMicroseconds(10);
    }
    joyc.begin(&Wire, kJoyAddr, 0, 26, 100000UL);
    Wire.setTimeOut(20);
    delay(100);
    int first[3] = {0, 0, 1};
    if (readJoy(first)) {                                // stick at rest = centre
        center[0] = first[0];
        center[1] = first[1];
        joy_ok = true;
    }
    Serial.printf("Mini JoyC %s, rest adc %d %d\n", joy_ok ? "ok" : "MISSING", first[0], first[1]);
    joyLed(0, 0, 30);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);                                // lowest latency
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    udp.begin(teleop::kPort);
}

void loop() {
    M5.update();
    const uint32_t now = millis();
    readStick(now);
    readTilt();
    update(now);

    if (WiFi.status() == WL_CONNECTED) {
        receive(now);
        if (!linkUp(now)) hello(now);
        const bool fast = mode == Mode::kTilt || mode == Mode::kStick || mode == Mode::kArm || estop_packets > 0;
        if (have_host && now - last_send_ms >= (uint32_t)(fast ? kPeriodMs : kIdlePeriodMs)) {
            last_send_ms = now;
            buildCommand(now);
            uint8_t b[20];
            size_t n = teleop::encodeCmdVel(++seq, TELEOP_KEY, lin, ang, buttons, kLeaseMs, b);
            sendPacket(n, b, stackchan);
        }
    }
    if (now - last_draw_ms >= 100) {
        last_draw_ms = now;
        draw(now);
    }
    if (now - last_dump_ms >= 250) {                     // for the tilt check over USB
        last_dump_ms = now;
        Serial.printf("imu g %+.2f %+.2f %+.2f  pitch %+6.1f roll %+6.1f  stick %+.2f %+.2f  mode %d link %d  wifi %d %s\n",
                      g_filt[0], g_filt[1], g_filt[2], pitch, roll, jx, jy, (int)mode, linkUp(now),
                      (int)WiFi.status(), WiFi.localIP().toString().c_str());
    }
    delay(5);
}
