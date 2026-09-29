"""Build docs/link-map/stacky-link-map.pdf, the printable architecture manual.

Edit the tables and boxes below, then run:
    python docs/link-map/make_link_map.py        (needs: pip install reportlab)
"""
import os

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stacky-link-map.pdf")
DATE = "28 Sep 2026"

INK = colors.HexColor("#18202b")
MUTED = colors.HexColor("#5b6574")
RULE = colors.HexColor("#c9ced4")
SOFT = colors.HexColor("#eef0f2")
C = {  # one colour per kind of link
    "mcp": colors.HexColor("#6b4fa0"),
    "wifi": colors.HexColor("#0b7f86"),
    "ble": colors.HexColor("#4a55c9"),
    "udp": colors.HexColor("#b86e0c"),
    "web": colors.HexColor("#7d8591"),
}
STATE = {  # state -> (text colour, background)
    "ok": (colors.HexColor("#1f7a45"), colors.HexColor("#e3f2e9")),
    "wait": (colors.HexColor("#9a6200"), colors.HexColor("#fbefd9")),
    "plan": (colors.HexColor("#46607a"), colors.HexColor("#e4ebf2")),
}

# ---------------------------------------------------------------- schematic --
# Boxes: key -> (x, top, width, height, title, lines). Units are points, top-down.
W, H = 515, 470
FRAME = A4[0] - 40 * mm   # printable width between the margins (482 pt)
K = FRAME / W             # the schematic is drawn at 515 pt and scaled to fit
BOXES = {
    "claude": (60, 10, 170, 46, "Claude / LLM", ["voice, tools, explorer brain"]),
    "hub": (290, 10, 150, 46, "stacky-hub", ["Mac MCP server, explorer"]),
    "gw": (60, 110, 380, 60, "Gateway daemon (Mac 192.168.13.12)",
           ["MCP :8767 - WebSocket :8765 - photos :8768", "speech in/out - mbot_* tool limits"]),
    "joy": (0, 240, 110, 62, "Controller", ["StickC Plus + Mini JoyC", "tilt - stick - E-stop"]),
    "sc": (170, 240, 180, 70, "Stack-chan (CoreS3)", ["command checker - heartbeat", "teleop UDP - camera - voice"]),
    "ota": (400, 240, 95, 46, "Update server", ["boot check only"]),
    "mbot": (170, 390, 180, 62, "mBot2 (CyberPi)", ["stacky_runtime v1.2", "obstacle - watchdog - lease"]),
}
# Links: (points top-down, colour, dashed, label, label position). Routed so nothing crosses.
LINKS = [
    ([(145, 56), (145, 110)], "mcp", False, "MCP", (151, 86)),
    ([(365, 56), (365, 110)], "mcp", True, "MCP over HTTP", (371, 86)),
    ([(260, 170), (260, 240)], "wifi", False, "Wi-Fi WebSocket :8765", (266, 208)),
    ([(260, 310), (260, 390)], "ble", False, "BLE GATT - Stacky v1.2", (266, 352)),
    ([(110, 271), (170, 271)], "udp", True, "UDP 20 Hz", (114, 262)),
    ([(350, 263), (400, 263)], "web", True, "HTTPS", (358, 254)),
    ([(440, 33), (505, 33), (505, 421), (350, 421)], "ble", True, "Mac BLE (optional)", (380, 412)),
]


class Schematic(Flowable):
    def wrap(self, *_):
        return W * K, H * K

    def draw(self):
        d = Drawing(W, H)
        y = lambda t: H - t  # noqa: E731  top-down -> PDF coordinates
        for x, top, w, h, title, lines in BOXES.values():
            d.add(Rect(x, y(top + h), w, h, rx=6, ry=6, fillColor=colors.white, strokeColor=RULE, strokeWidth=1.2))
            d.add(String(x + 8, y(top + 16), title, fontName="Helvetica-Bold", fontSize=9.5, fillColor=INK))
            for i, line in enumerate(lines):
                d.add(String(x + 8, y(top + 30 + 11 * i), line, fontName="Helvetica", fontSize=7.3, fillColor=MUTED))
        for pts, kind, dashed, label, (lx, lt) in LINKS:
            col = C[kind]
            for (x1, t1), (x2, t2) in zip(pts, pts[1:]):
                seg = Line(x1, y(t1), x2, y(t2), strokeColor=col, strokeWidth=1.8)
                if dashed:
                    seg.strokeDashArray = [4, 3]
                d.add(seg)
            (x1, t1), (x2, t2) = pts[-2], pts[-1]  # arrowhead at the end
            dx, dy = (x2 > x1) - (x2 < x1), (t2 > t1) - (t2 < t1)
            tip = (x2, y(t2))
            if dx:
                base = [(x2 - 7 * dx, y(t2) + 3.5), (x2 - 7 * dx, y(t2) - 3.5)]
            else:
                base = [(x2 - 3.5, y(t2) + 7 * dy), (x2 + 3.5, y(t2) + 7 * dy)]
            d.add(Polygon([*tip, *base[0], *base[1]], fillColor=col, strokeColor=col, strokeWidth=0.5))
            d.add(String(lx, y(lt), label, fontName="Helvetica-Bold", fontSize=7.3, fillColor=col))
        d.scale(K, K)
        d.width, d.height = W * K, H * K
        d.drawOn(self.canv, 0, 0)


def legend():
    d = Drawing(FRAME, 22)
    items = [("mcp", "MCP"), ("wifi", "Wi-Fi WebSocket"), ("ble", "Bluetooth LE"), ("udp", "Wi-Fi UDP"),
             ("web", "Internet")]
    x = 0
    for kind, text in items:
        d.add(Line(x, 12, x + 22, 12, strokeColor=C[kind], strokeWidth=2))
        d.add(String(x + 27, 9, text, fontName="Helvetica", fontSize=8, fillColor=INK))
        x += 36 + len(text) * 4.3
    d.add(String(0, -4, "Solid = verified on the hardware.   Dashed = built, not yet tested on the hardware.",
                 fontName="Helvetica", fontSize=8, fillColor=MUTED))
    return d


# ------------------------------------------------------------------- tables --
LINK_ROWS = [
    ("Claude -> gateway", "MCP, localhost :8767", "JSON-RPC tool calls", "ok", "Working"),
    ("stacky-hub -> gateway", "MCP over HTTP", "JSON-RPC tool calls", "plan", "Simulation only"),
    ("Gateway -> Stack-chan", "Wi-Fi WebSocket :8765", "MCP + audio + events", "ok", "Working"),
    ("Stack-chan -> mBot", "BLE GATT, MTU 512", "F3 frame + Stacky text", "ok", "Working (robot runs v1.1)"),
    ("Controller -> Stack-chan", "Wi-Fi UDP :8790, 20 Hz", "20-byte teleop packet", "wait", "Not tested yet"),
    ("Stack-chan -> update server", "HTTPS, at boot", "Version check", "wait", "Skipped after 1 failure"),
    ("Mac -> mBot", "BLE or USB", "F3 frame + Stacky text", "ok", "Working when Stack-chan is off"),
]
GUARD_ROWS = [
    ("Wheel speed", "max 60 rpm (about 20 cm/s)", "Gateway, Stack-chan, mBot", "ok", "Active"),
    ("Timed move", "max 5 s", "Gateway, Stack-chan, mBot", "ok", "Active"),
    ("Step / turn", "max 30 cm / 180 deg", "Gateway, Stack-chan, mBot", "ok", "Active"),
    ("Arm S4", "40-120 deg, home 90", "Gateway, Stack-chan, mBot", "ok", "Active"),
    ("Gripper S3", "45-120 deg, home 90", "Gateway, Stack-chan, mBot", "ok", "Active"),
    ("Program", "max 20 steps, 30 s", "Gateway, Stack-chan, mBot", "plan", "Not tested on robot"),
    ("Obstacle", "stop forward under 10 cm", "mBot", "ok", "Active"),
    ("Watchdog", "stop after 3 s of silence", "mBot", "ok", "Active"),
    ("Buttons", "B = stop + lock, A = unlock", "mBot", "ok", "Active"),
    ("Drive lease", "stop 300 ms after last drive", "Stack-chan, mBot", "wait", "Needs mBot v1.2"),
    ("Dead-man", "stop 300 ms after last packet", "Stack-chan", "wait", "Not tested yet"),
    ("Tilt driving", "wheels move only while A is held", "Controller", "wait", "Not tested yet"),
    ("Limits in sync", "all layers use the same values", "tests/test_limits.py", "ok", "Active"),
]
EDIT_ROWS = [
    ("A safety limit", "docs/protocol.md, hub/stacky_hub/limits.py, mbot/micropython/stacky_runtime.py, "
     "firmware mbot_limits.h", "Run pytest. test_limits.py fails if they differ."),
    ("An mBot command", "stacky_runtime.py (parse_step, start_step), firmware mbot_command.cc, gateway mbot.py",
     "Upload the runtime to the mBot. Flash Stack-chan."),
    ("The joystick packet", "firmware teleop_packet.h, hub/stacky_hub/teleop.py, remote/joyc/src/teleop_packet.h",
     "Update the golden vector in all three."),
    ("Controller buttons or modes", "remote/joyc/src/main.cpp", "Flash the StickC, then power-cycle it."),
    ("Teleop key or port", "Kconfig STACKCHAN_MBOT_TELEOP_KEY / _PORT, remote/joyc/include/config.h",
     "Flash Stack-chan and the StickC."),
    ("Stack-chan Wi-Fi", "Hotspot Xiaozhi-3679, page 192.168.4.1", "Keep the Mac on the same network."),
    ("Gateway address", "gateway_config_set tool", "Stack-chan reconnects by itself."),
]
RAM_ROWS = [
    ("Add a memory report tool", "measure", "none"),
    ("Turn off face detection in the mBot build", "about 24 KB", "low"),
    ("Move the audio encoder stack to PSRAM", "about 24 KB", "medium"),
]
OPEN_ITEMS = [
    "Upload stacky_runtime.py v1.2 to the mBot (needed for any drive command).",
    "Flash Stack-chan with the teleop listener.",
    "Check the controller tilt directions, then do a first capped drive.",
    "Check the head direction, then run a first supervised exploration.",
]

ST = {
    "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK),
    "sub": ParagraphStyle("s", fontName="Helvetica", fontSize=10, leading=14, textColor=MUTED),
    "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=6,
                        spaceAfter=6),
    "cell": ParagraphStyle("c", fontName="Helvetica", fontSize=8.3, leading=10.5, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName="Helvetica-Bold", fontSize=8.3, leading=10.5, textColor=INK),
    "body": ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK),
}


def table(header, rows, widths, state_col=None):
    data = [[Paragraph(h, ST["cellb"]) for h in header]]
    styles = [("BACKGROUND", (0, 0), (-1, 0), SOFT), ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
              ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5),
              ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    for r, row in enumerate(rows, start=1):
        cells = list(row)
        if state_col is not None:
            key, text = cells[state_col], cells[state_col + 1]
            fg, bg = STATE[key]
            cells = cells[:state_col] + [Paragraph(text, ParagraphStyle("st", parent=ST["cellb"], textColor=fg))]
            styles.append(("BACKGROUND", (state_col, r), (state_col, r), bg))
        data.append([c if isinstance(c, Paragraph) else Paragraph(c, ST["cellb"] if i == 0 else ST["cell"])
                     for i, c in enumerate(cells)])
    total = sum(widths)
    t = Table(data, colWidths=[w * FRAME / total for w in widths], repeatRows=1)
    t.setStyle(TableStyle(styles))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, "Stacky Link Map - " + DATE)
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, "page %d" % doc.page)
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title="Stacky Link Map", author="Stacky")
    s = [Paragraph("Stacky Link Map", ST["title"]),
         Paragraph("Stack-chan, mBot2 and controller: architecture manual. " + DATE, ST["sub"]),
         Spacer(1, 14), Schematic(), Spacer(1, 10), legend(), PageBreak()]

    s += [Paragraph("Links", ST["h"]),
          table(["Link", "Transport", "Carries", "State"], LINK_ROWS, [118, 120, 120, 157], state_col=3),
          Spacer(1, 14),
          Paragraph("Safety limits", ST["h"]),
          Paragraph("Each limit is enforced in every layer listed. The mBot always has the last word.", ST["body"]),
          Spacer(1, 6),
          table(["Limit", "Value", "Enforced in", "State"], GUARD_ROWS, [80, 150, 150, 135], state_col=3),
          PageBreak()]

    s += [Paragraph("How to change it", ST["h"]),
          table(["To change", "Edit", "Then"], EDIT_ROWS, [100, 255, 160]),
          Spacer(1, 14),
          KeepTogether([
              Paragraph("Stack-chan memory", ST["h"]),
              Paragraph("Internal RAM: 341 KB. Built-in use: 165 KB. Free before Bluetooth: 55-65 KB. "
                        "Free after Bluetooth: 25-35 KB. <b>Lowest seen: 11.7 KB.</b> "
                        "Low memory breaks Bluetooth, TLS or Wi-Fi.", ST["body"]),
              Spacer(1, 6),
              table(["Next fix", "Saves", "Risk"], RAM_ROWS, [300, 110, 105])]),
          Spacer(1, 14),
          Paragraph("Open items", ST["h"])]
    s += [Paragraph("%d. %s" % (i + 1, item), ST["body"]) for i, item in enumerate(OPEN_ITEMS)]
    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
