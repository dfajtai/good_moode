"""Generate docs/wiring.pdf: the wiring PLAN of the Pi Zero 2 W, the Waveshare PCM5122 HAT, the OLED,
the rotary encoder, the relay and the USB speaker.

    python scripts/local/make_wiring_pdf.py

Needs: pip install reportlab. Fonts come from the repo's fonts/ folder. The drawing is a plan, not a
verified build: every page says so. Edit the tables below and run the script again to update the PDF.
"""

from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "wiring.pdf"
pdfmetrics.registerFont(TTFont("R", str(ROOT / "fonts" / "NotoSans-Regular.ttf")))
pdfmetrics.registerFont(TTFont("B", str(ROOT / "fonts" / "NotoSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("I", str(ROOT / "fonts" / "NotoSans-Italic.ttf")))

W, H = landscape(A4)
NPAGES = 4
INK = HexColor("#1f2430"); MUTED = HexColor("#6b7280"); LINE = HexColor("#d1d5db")
BOARD = HexColor("#1b7f4b"); BOARD_D = HexColor("#13603a")
C_3V3 = HexColor("#e8890c"); C_5V = HexColor("#d92d20"); C_GND = HexColor("#111827")
C_SDA = HexColor("#2563eb"); C_SCL = HexColor("#b8860b")
C_S1 = HexColor("#16a34a"); C_S2 = HexColor("#9333ea"); C_KEY = HexColor("#0d9488")
C_HAT = HexColor("#be185d"); C_RLY = HexColor("#b45309")
C_PLAN = HexColor("#b91c1c")

PINS = {
    1: ("3V3", "3v3"), 2: ("5V", "5v"), 3: ("GPIO2 · SDA1", "gpio"), 4: ("5V", "5v"),
    5: ("GPIO3 · SCL1", "gpio"), 6: ("GND", "gnd"), 7: ("GPIO4", "gpio"), 8: ("GPIO14 · TXD", "gpio"),
    9: ("GND", "gnd"), 10: ("GPIO15 · RXD", "gpio"), 11: ("GPIO17", "gpio"), 12: ("GPIO18 · PCM CLK", "gpio"),
    13: ("GPIO27", "gpio"), 14: ("GND", "gnd"), 15: ("GPIO22", "gpio"), 16: ("GPIO23", "gpio"),
    17: ("3V3", "3v3"), 18: ("GPIO24", "gpio"), 19: ("GPIO10 · MOSI", "gpio"), 20: ("GND", "gnd"),
    21: ("GPIO9 · MISO", "gpio"), 22: ("GPIO25", "gpio"), 23: ("GPIO11 · SCLK", "gpio"), 24: ("GPIO8 · CE0", "gpio"),
    25: ("GND", "gnd"), 26: ("GPIO7 · CE1", "gpio"), 27: ("GPIO0 · ID_SD", "gpio"), 28: ("GPIO1 · ID_SC", "gpio"),
    29: ("GPIO5", "gpio"), 30: ("GND", "gnd"), 31: ("GPIO6", "gpio"), 32: ("GPIO12", "gpio"),
    33: ("GPIO13", "gpio"), 34: ("GND", "gnd"), 35: ("GPIO19 · PCM FS", "gpio"), 36: ("GPIO16", "gpio"),
    37: ("GPIO26", "gpio"), 38: ("GPIO20", "gpio"), 39: ("GND", "gnd"), 40: ("GPIO21 · PCM DOUT", "gpio"),
}
KIND_FILL = {"3v3": C_3V3, "5v": C_5V, "gnd": C_GND, "gpio": HexColor("#9ca3af")}

USED = {  # signal wires drawn between the header and a device
    3: (C_SDA, "OLED", False), 5: (C_SCL, "OLED", False),
    11: (C_S1, "ENC", False), 13: (C_S2, "ENC", False), 15: (C_KEY, "ENC", False),
    37: (C_RLY, "RELAY", False),
}
# power pins: label, colour, tag shown on the device ports
PWR = {2: ("5V · Pi power in", C_5V, None), 4: ("5V · OLED/encoder", C_5V, "A"),
       6: ("GND · Pi power in", C_GND, None), 14: ("GND · OLED/encoder", C_GND, "B")}
HAT = {2: "5V", 4: "5V", 3: "SDA", 5: "SCL", 6: "GND", 9: "GND", 12: "I2S CLK", 14: "GND", 20: "GND",
       25: "GND", 27: "EEPROM", 28: "EEPROM", 30: "GND", 34: "GND", 35: "I2S LRCLK", 39: "GND", 40: "I2S DAC"}
HAT_LABEL = {2: "5V · HAT", 4: "5V · HAT", 9: "GND · HAT", 25: "GND · HAT", 6: "GND · HAT", 14: "GND · HAT",
             20: "GND · HAT", 30: "GND · HAT", 34: "GND · HAT", 39: "GND · HAT", 12: "GPIO18 · I2S BCLK · HAT",
             35: "GPIO19 · I2S LRCLK · HAT", 40: "GPIO21 · I2S DAC · HAT", 27: "GPIO0 · EEPROM · HAT",
             28: "GPIO1 · EEPROM · HAT"}
SHARED_LABEL = {3: "GPIO2 · SDA1 (+HAT)", 5: "GPIO3 · SCL1 (+HAT)", 37: "GPIO26 · relay"}


def txt(c, x, y, s, font="R", size=9, color=INK, align="l"):
    c.setFont(font, size); c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)


def para(c, x, y, s, width, font="R", size=9, color=INK, lead=None):
    lead = lead or size * 1.35
    for ln in simpleSplit(s, font, size, width):
        txt(c, x, y, ln, font, size, color); y -= lead
    return y


def title(c, t, sub):
    txt(c, 40, H - 38, t, "B", 17)
    txt(c, 40, H - 55, sub, "R", 9, MUTED)
    c.setStrokeColor(LINE); c.setLineWidth(0.6); c.line(40, H - 63, W - 40, H - 63)
    # every page says that this is only a plan
    w = 262
    c.setFillColor(HexColor("#fef2f2")); c.setStrokeColor(HexColor("#dc2626")); c.setLineWidth(1)
    c.roundRect(W - 40 - w, H - 47, w, 19, 4, stroke=1, fill=1)
    txt(c, W - 40 - w / 2, H - 41, "PLAN ONLY – not yet verified on the hardware", "B", 8.5, C_PLAN, "c")


def footer(c, n):
    txt(c, 40, 22, "good_moode · wiring PLAN (not verified) · sources: good_moode/, moOde settings, "
                   "Waveshare PCM5122-Audio-Board-A wiki, user-supplied wiring", "R", 7, MUTED)
    txt(c, W - 40, 22, f"{n}/{NPAGES}", "R", 7, MUTED, "r")


def wire(c, pts, color, dashed=False):
    c.setStrokeColor(color); c.setLineWidth(2.2); c.setLineJoin(1); c.setLineCap(1)
    c.setDash(5, 3) if dashed else c.setDash()
    p = c.beginPath(); p.moveTo(*pts[0])
    for q in pts[1:]: p.lineTo(*q)
    c.drawPath(p, stroke=1, fill=0); c.setDash()
    for q in (pts[0], pts[-1]):
        c.setFillColor(color); c.circle(q[0], q[1], 2.6, stroke=0, fill=1)


def swatch(c, x, y, col, dashed=False, box=False):
    if box:
        c.setFillColor(col); c.roundRect(x, y - 1, 24, 9, 2, stroke=0, fill=1); return
    c.setStrokeColor(col); c.setLineWidth(2.4); c.setDash(4, 2) if dashed else c.setDash()
    c.line(x, y + 3, x + 24, y + 3); c.setDash()


def tag(c, x, y, letter):
    """A coloured circle: the wire continues at the other circle with the same letter."""
    col = C_5V if letter in ("A", "C") else C_GND
    c.setFillColor(col); c.circle(x, y, 6.5, stroke=0, fill=1); txt(c, x, y - 3, letter, "B", 8, white, "c")
    return col


# ---------------------------------------------------------------- page 1
def page1(c):
    title(c, "Raspberry Pi Zero 2 W – wiring",
          "Waveshare PCM5122 Audio HAT on the header + OLED (I2C) + rotary encoder (GPIO) + relay switching the USB speaker's 5 V. "
          "Pin numbers are physical (BOARD) numbers.")
    Y0, P = 486, 18.5
    y = lambda pin: Y0 - ((pin - 1) // 2) * P
    BW = 122
    xl_box, xl_pin, xr_pin, xr_box = 288, 288 + BW + 14, 288 + BW + 50, 288 + BW + 66
    px1 = xr_box + BW + 10
    c.setFillColor(HexColor("#f3f4f6")); c.setStrokeColor(LINE)
    c.roundRect(xl_box - 12, y(39) - 16, px1 - xl_box + 12, (Y0 - y(39)) + 32, 8, stroke=1, fill=1)
    txt(c, (xl_box + xr_box + BW) / 2, Y0 + 21, "GPIO header (40 pins)", "B", 9, INK, "c")
    for pin, (lab, kind) in PINS.items():
        left = pin % 2 == 1
        cy = y(pin); cx = xl_pin if left else xr_pin
        wired = pin in USED or pin in PWR; hat = pin in HAT
        if pin in PWR: fill = PWR[pin][1]; text = PWR[pin][0]; tcol = white
        elif pin in USED: fill = USED[pin][0]; text = SHARED_LABEL.get(pin, lab); tcol = white
        elif hat: fill = C_HAT; text = HAT_LABEL[pin]; tcol = white
        else: fill = white; text = lab; tcol = INK
        bx = xl_box if left else xr_box
        c.setFillColor(fill); c.setStrokeColor(C_HAT if (hat and wired) else (fill if (wired or hat) else LINE)); c.setLineWidth(2.2 if (hat and wired) else 0.8)
        c.roundRect(bx, cy - 7, BW, 14, 3, stroke=1, fill=1)
        txt(c, bx + (BW - 4 if left else 4), cy - 2.6, text, "B" if (wired or hat) else "R", 6.8, tcol, "r" if left else "l")
        if pin in PWR and PWR[pin][2]:
            c.setFillColor(white); c.circle(bx + BW - 10, cy, 5, stroke=0, fill=1)
            txt(c, bx + BW - 10, cy - 2.6, PWR[pin][2], "B", 7, PWR[pin][1], "c")
        c.setFillColor(KIND_FILL[kind]); c.setStrokeColor(C_HAT if hat else (INK if wired else HexColor("#6b7280"))); c.setLineWidth(1.8 if (hat or wired) else 0.5)
        if pin == 1: c.rect(cx - 6.5, cy - 6.5, 13, 13, stroke=1, fill=1)
        else: c.circle(cx, cy, 6.5, stroke=1, fill=1)
        txt(c, cx, cy - 2.4, str(pin), "B", 6.5, white if (kind != "gpio" or wired or hat) else INK, "c")
    txt(c, (xl_box + xr_box + BW) / 2, y(39) - 28, "Odd pins on the left, even pins on the right · pin 1 = square solder pad", "I", 6.5, MUTED, "c")

    def box(top, bottom, name, lines, ports):
        c.setFillColor(HexColor("#fffbeb")); c.setStrokeColor(INK); c.setLineWidth(1.2)
        c.roundRect(40, bottom, 180, top - bottom, 6, stroke=1, fill=1)
        ty = top - 14
        txt(c, 50, ty, name, "B", 10)
        for ln in lines:
            ty -= 11; txt(c, 50, ty, ln, "R", 7.5, MUTED)
        for port in ports:
            py, lab = port[0], port[1]; t = port[2] if len(port) > 2 else None
            c.setFillColor(INK); c.circle(220, py, 3.2, stroke=0, fill=1)
            txt(c, 214, py - 3, lab, "B", 8, INK, "r")
            if t:
                col = C_5V if t in ("A", "C") else C_GND
                c.setStrokeColor(col); c.setLineWidth(2.2); c.line(220, py, 236, py)
                tag(c, 244, py, t)

    box(y(1) + 12, y(7) - 12, "OLED display", ["SH1106, 128×64", "I2C bus 1, address 0x3C"],
        [(y(1), "VCC (5V)", "A"), (y(3), "SDA"), (y(5), "SCL"), (y(7), "GND", "B")])
    box(y(11) + 14, y(25) - 14, "Rotary encoder", ["with push button", "volume: moOde's own support"],
        [(y(11), "S1 / CLK"), (y(13), "S2 / DT"), (y(15), "KEY / SW"), (y(17), "+ (5V)", "A"), (y(25), "GND", "B")])
    para(c, 50, y(19) + 4, "The KEY button is the same GPIO22 as the main program's play/pause button.", 160, "I", 7.5, MUTED)

    # relay module: coil side to the header (right), contacts at the bottom
    r_top, r_bot = y(35) + 13, 120
    box(r_top, r_bot, "Relay module", ["5 V type", "GPIO26 high: music is playing", "otherwise low"],
        [(y(35), "VCC (5V)", "C"), (y(37), "IN"), (y(39), "GND", "D")])
    contacts = {"NC": 82, "COM": 128, "NO": 174}
    for name, x in contacts.items():
        c.setFillColor(INK if name != "NC" else MUTED); c.circle(x, r_bot, 3.2, stroke=0, fill=1)
        txt(c, x, r_bot + 7, name if name != "NC" else "NC (unused)", "B" if name != "NC" else "I", 8 if name != "NC" else 6.5,
            INK if name != "NC" else MUTED, "c")
    # COM: +5V straight from the power supply (same branch as the relay's VCC)
    c.setStrokeColor(C_5V); c.setLineWidth(2.2); c.line(contacts["COM"], r_bot, contacts["COM"], r_bot - 11)
    tag(c, contacts["COM"], r_bot - 17.5, "C")

    # USB speaker: power only
    s_top, s_bot = 88, 38
    c.setFillColor(HexColor("#fffbeb")); c.setStrokeColor(INK); c.setLineWidth(1.2)
    c.roundRect(40, s_bot, 180, s_top - s_bot, 6, stroke=1, fill=1)
    txt(c, 50, s_top - 26, "USB speaker (power)", "B", 10)
    txt(c, 50, s_top - 37, "USB socket on a cable (pigtail)", "R", 7.5, MUTED)
    txt(c, 50, s_top - 46, "VBUS + GND only, audio not shown", "R", 7.5, MUTED)
    gnd_x = 60; plus_x = contacts["NO"]
    for x, lab in ((gnd_x, "GND"), (plus_x, "+ (VBUS)")):
        c.setFillColor(INK); c.circle(x, s_top, 3.2, stroke=0, fill=1)
        txt(c, x, s_top - 9, lab, "B", 7.5, INK, "c")
    c.setStrokeColor(C_GND); c.setLineWidth(2.2); c.line(gnd_x, s_top, gnd_x, s_top + 10)
    tag(c, gnd_x, s_top + 16.5, "D")
    # NO -> speaker +: the switched 5 V
    wire(c, [(plus_x, r_bot), (plus_x, s_top)], C_5V)
    txt(c, plus_x + 7, (r_bot + s_top) / 2 - 2, "switched 5 V", "I", 7, MUTED)

    for pin, (col, dev, dashed) in USED.items():
        wire(c, [(220, y(pin)), (xl_box, y(pin))], col, dashed)

    lx, ly = px1 + 22, H - 92
    txt(c, lx, ly, "Legend", "B", 9.5); ly -= 16
    for col, name, bx_ in ((C_SDA, "I2C SDA (GPIO2)", False), (C_SCL, "I2C SCL (GPIO3)", False),
                           (C_S1, "Encoder S1/CLK (GPIO17)", False), (C_S2, "Encoder S2/DT (GPIO27)", False),
                           (C_KEY, "Button KEY (GPIO22)", False), (C_RLY, "Relay control (GPIO26)", False),
                           (C_5V, "5V (power)", True), (C_GND, "GND", True),
                           (C_HAT, "Pin used by the Waveshare PCM5122 HAT", True)):
        swatch(c, lx, ly, col, False, bx_); txt(c, lx + 30, ly, name, "R", 7.8); ly -= 13.5
    for t, name in (("A", "5V branch from pin 4: OLED, encoder"), ("B", "GND branch from pin 14: OLED, encoder"),
                    ("C", "relay VCC and COM: 5V from the supply"), ("D", "relay and speaker GND: from the supply")):
        tag(c, lx + 12, ly + 3.5, t); txt(c, lx + 30, ly, name, "R", 7.8); ly -= 14
    c.setStrokeColor(C_HAT); c.setLineWidth(2); c.roundRect(lx + 1, ly - 1, 22, 9, 2, stroke=1, fill=0)
    txt(c, lx + 30, ly, "magenta outline: the HAT uses it too", "R", 7.8); ly -= 20
    txt(c, lx, ly, "Notes", "B", 9.5); ly -= 13
    for note in ("The Pi is powered through pin 2 (5V) and pin 6 (GND); the HAT gets its power from there too.",
                 "OLED and encoder take 5V / GND from pins 4 / 14.",
                 "The relay switches only the speaker's +5V: supply +5V to COM, NO to the speaker's VBUS. "
                 "The speaker's GND is not switched. NC is unused.",
                 "The OLED shares the HAT's I2C bus (0x3C and 0x4C); no clash.",
                 "GPIO17, 27, 22 (encoder) and GPIO26 (relay) are free; the HAT does not use them.",
                 "With 5V powering, check that module pull-ups do not put 5V on the GPIOs (page 4)."):
        c.setFillColor(MUTED); c.circle(lx + 2, ly + 2.5, 1.3, stroke=0, fill=1)
        ly = para(c, lx + 9, ly, note, W - 40 - (lx + 9), "R", 7.2, INK, 9.4) - 3
    footer(c, 1)


# ---------------------------------------------------------------- page 2
def page2(c):
    title(c, "Wiring on the board (top view)",
          "Raspberry Pi Zero 2 W with the 40-pin header along the top edge. To scale (65 × 30 mm); the HAT sits on top of it.")
    S = 9.0
    bw, bh = 65 * S, 30 * S
    x0 = (W - bw) / 2; top = H - 140; bot = top - bh
    X = lambda mm: x0 + mm * S
    Yy = lambda mm: top - mm * S
    c.setFillColor(BOARD); c.setStrokeColor(BOARD_D); c.setLineWidth(1.5); c.roundRect(x0, bot, bw, bh, 12, stroke=1, fill=1)
    for hx, hy in ((3.5, 3.5), (61.5, 3.5), (3.5, 26.5), (61.5, 26.5)):
        c.setFillColor(white); c.setStrokeColor(HexColor("#d4a017")); c.setLineWidth(2); c.circle(X(hx), Yy(hy), 9, stroke=1, fill=1)
        c.setFillColor(BOARD); c.circle(X(hx), Yy(hy), 5, stroke=0, fill=1)

    def conn(x, y_, w, h):
        c.setFillColor(HexColor("#cfd4da")); c.setStrokeColor(HexColor("#6b7280")); c.setLineWidth(0.8); c.rect(x, y_, w, h, stroke=1, fill=1)
    conn(x0 - 6, Yy(22), 52, 81); txt(c, x0 + 14, Yy(14.6), "microSD", "R", 6.5, white, "c")
    conn(X(12.4) - 24.8, bot - 4, 49.5, 20); txt(c, X(12.4), bot + 22, "mini HDMI", "R", 6.5, white, "c")
    conn(X(41.4) - 14, bot - 4, 28, 18); txt(c, X(41.4), bot + 20, "USB (data)", "R", 6.5, white, "c")
    conn(X(54.0) - 14, bot - 4, 28, 18); txt(c, X(54.0), bot + 20, "USB (power)", "R", 6.5, white, "c")
    c.saveState(); c.translate(x0 + bw - 24, Yy(15)); c.rotate(90)
    c.setFillColor(HexColor("#d9d2c5")); c.rect(-30, -4, 60, 8, stroke=0, fill=1); txt(c, 0, -12, "camera (CSI)", "R", 6.5, white, "c"); c.restoreState()
    c.setFillColor(HexColor("#9aa3ad")); c.setStrokeColor(HexColor("#4b5563")); c.rect(X(27), Yy(25.5), 11 * S, 11 * S, stroke=1, fill=1)
    txt(c, X(32.5), Yy(20.3), "RP3A0", "B", 8, INK, "c"); txt(c, X(32.5), Yy(21.9), "Zero 2 W", "R", 6.5, INK, "c")

    c.setFillColor(HexColor("#1f2430")); c.rect(X(8.37) - 1.4 * S, Yy(3.5) - 1.4 * S, 48.26 * S + 2.8 * S, 2.54 * S + 2.8 * S, stroke=0, fill=1)
    px = lambda pin: X(8.37 + ((pin - 1) // 2) * 2.54)
    py = lambda pin: Yy(6.04) if pin % 2 else Yy(3.5)
    for pin in range(1, 41):
        wired = pin in USED or pin in PWR; hat = pin in HAT
        col = PWR[pin][1] if pin in PWR else (USED[pin][0] if pin in USED else (C_HAT if hat else HexColor("#c9a227")))
        cx, cy = px(pin), py(pin)
        c.setFillColor(col)
        if hat and wired: c.setStrokeColor(C_HAT); c.setLineWidth(2.2)
        elif wired: c.setStrokeColor(white); c.setLineWidth(1.6)
        elif hat: c.setStrokeColor(white); c.setLineWidth(1.2)
        else: c.setStrokeColor(HexColor("#8a6f10")); c.setLineWidth(0.5)
        if pin == 1: c.rect(cx - 6, cy - 6, 12, 12, stroke=1, fill=1)
        else: c.rect(cx - 5, cy - 5, 10, 10, stroke=1, fill=1)

    below = {3: (2, "SDA", "OLED+HAT"), 5: (1, "SCL", "OLED+HAT"), 9: (1, "GND", "HAT"),
             11: (2, "S1", "enc."), 13: (1, "S2", "enc."), 15: (2, "KEY", "enc."),
             25: (1, "GND", "HAT"), 27: (2, "ID_SD", "HAT"), 35: (1, "LRCLK", "HAT"), 37: (2, "REL", "relay"), 39: (1, "GND", "HAT")}
    for pin, (lvl, a_, b_) in below.items():
        col = USED[pin][0] if pin in USED else C_HAT
        cx = px(pin); ytop = py(pin) - 8 - (0 if lvl == 1 else 30)
        c.setStrokeColor(white); c.setLineWidth(1.1); c.line(cx, py(pin) - 6, cx, ytop)
        both = pin in HAT and pin in USED
        c.setFillColor(col); c.setStrokeColor(C_HAT if both else white); c.setLineWidth(1.6 if both else 1)
        c.roundRect(cx - 20, ytop - 24, 40, 24, 4, stroke=1, fill=1)
        txt(c, cx, ytop - 10, a_, "B", 8, white, "c"); txt(c, cx, ytop - 20, f"pin {pin} · {b_}", "R", 5.4, white, "c")
    above = {2: (1, "5V", "PWR", C_5V), 4: (2, "5V", "A", C_5V), 6: (1, "GND", "PWR", C_GND), 12: (1, "BCLK", "HAT", C_HAT),
             14: (2, "GND", "B", C_GND), 20: (1, "GND", "HAT", C_HAT), 28: (2, "ID_SC", "HAT", C_HAT),
             30: (1, "GND", "HAT", C_HAT), 34: (1, "GND", "HAT", C_HAT), 40: (1, "DAC", "HAT", C_HAT)}
    for pin, (lvl, a_, b_, col) in above.items():
        cx = px(pin); ybot = top + 8 + (0 if lvl == 1 else 28)
        c.setStrokeColor(col if col != C_GND else white); c.setLineWidth(1.1); c.line(cx, py(pin) + 6, cx, ybot)
        c.setFillColor(col); c.setStrokeColor(C_HAT if pin in PWR else white); c.setLineWidth(1.6 if pin in PWR else 1)
        c.roundRect(cx - 20, ybot, 40, 24, 4, stroke=1, fill=1)
        txt(c, cx, ybot + 14, a_, "B", 8, white, "c"); txt(c, cx, ybot + 5, f"pin {pin} · {b_}", "R", 5.4, white, "c")

    ly = bot - 26
    c.setFillColor(C_HAT); c.roundRect(x0, ly, 24, 9, 2, stroke=0, fill=1)
    txt(c, x0 + 30, ly + 1, "Pin used by the Waveshare PCM5122 Audio HAT (5V, GND, I2C, I2S, EEPROM)", "R", 8)
    c.setFillColor(HexColor("#c9a227")); c.rect(x0 + 380, ly, 9, 9, stroke=0, fill=1); txt(c, x0 + 395, ly + 1, "free pin", "R", 8)
    txt(c, x0, ly - 14, "PWR = Pi power (pin 2 5V, pin 6 GND) · A = 5V branch (pin 4) and B = GND branch (pin 14) for the OLED and the encoder · "
                        "magenta outline = the HAT uses it too.", "I", 7.5, MUTED)
    txt(c, x0, ly - 26, "Pin 1 (square) is on the left; even pins in the top row, odd pins in the bottom row. The 3.3V pins (1, 17) stay free.", "I", 7.5, MUTED)
    footer(c, 2)


# ---------------------------------------------------------------- page 3
def page3(c):
    title(c, "All pins and connections",
          "Hand-wired devices and the Waveshare PCM5122 HAT. The coloured bar is the colour used in the drawings.")
    heads = ["Device", "Signal", "Pi pin", "BCM", "Function", "Source"]
    cw = [78, 92, 100, 78, 250, 148]
    us = "user-supplied"
    rows = [
        (C_5V, "Pi power", "5V in", "2", "5V", "powers the Pi (and the HAT) through the header", us),
        (C_GND, "Pi power", "GND in", "6", "GND", "ground of the Pi's power", us),
        (C_5V, "OLED", "VCC", "4", "5V", "OLED supply, 5 V (branch A)", us),
        (C_SDA, "OLED", "SDA", "3", "GPIO2", "I2C-1 data, address 0x3C (shared with the HAT)", "good_moode/config.py (I2C_ADDR)"),
        (C_SCL, "OLED", "SCL", "5", "GPIO3", "I2C-1 clock (shared with the HAT)", "good_moode/state_machine.py"),
        (C_GND, "OLED", "GND", "14", "GND", "OLED ground (branch B)", us),
        (C_S1, "Rotary encoder", "S1 / CLK", "11", "GPIO17", "rotation: volume, handled by moOde", "moOde setting (user-supplied)"),
        (C_S2, "Rotary encoder", "S2 / DT", "13", "GPIO27", "second phase of the rotation, handled by moOde", "moOde setting (user-supplied)"),
        (C_KEY, "Rotary encoder", "KEY / SW (button)", "15", "GPIO22", "play/pause: mpc toggle, handled by good_moode", "good_moode/config.py (BUTTON_PIN)"),
        (C_5V, "Rotary encoder", "+ (if present)", "4", "5V", "module supply, 5 V (branch A)", us),
        (C_GND, "Rotary encoder", "GND", "14", "GND", "module ground (branch B)", us),
        (C_RLY, "Relay", "IN", "37", "GPIO26", "control: HIGH while playing, else LOW (2 s delay)", "good_moode/hardware.py (Relay)"),
        (C_5V, "Relay", "VCC", "–", "5V", "5 V straight from the supply (branch C)", us),
        (C_GND, "Relay", "GND", "–", "GND", "GND straight from the supply (branch D)", us),
        (C_5V, "Relay", "COM", "–", "5V", "contact common: +5V from the supply (branch C)", us),
        (C_5V, "Relay", "NO", "–", "5V (switched)", "normally open: to the USB speaker's VBUS", us),
        (C_5V, "USB speaker", "VBUS (+)", "–", "5V (switched)", "power from the relay's NO contact", us),
        (C_GND, "USB speaker", "GND", "–", "GND", "GND straight from the supply (branch D)", us),
        (C_HAT, "PCM5122 HAT", "5V", "2, 4", "5V", "power input (same rail as above)", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "GND", "6, 9, 14, 20, 25, 30, 34, 39", "GND", "ground", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "SDA / SCL", "3, 5", "GPIO2 / 3", "DAC control, I2C-1, address 0x4C", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "CLK", "12", "GPIO18", "I2S bit clock (BCLK)", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "LRCLK", "35", "GPIO19", "I2S word clock", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "DAC", "40", "GPIO21", "I2S data (Pi output)", "Waveshare wiki"),
        (C_HAT, "PCM5122 HAT", "ID_SD / ID_SC", "27, 28", "GPIO0 / 1", "EEPROM (I2C-0, 0x50): automatic driver loading", "Waveshare wiki (text)"),
    ]
    ty = H - 92
    xs = [40]
    for w_ in cw:
        xs.append(xs[-1] + w_)
    total = sum(cw)
    c.setFillColor(INK); c.rect(40, ty - 4, total, 15, stroke=0, fill=1)
    for i, h in enumerate(heads): txt(c, xs[i] + 6, ty, h, "B", 8, white)
    ty -= 15
    for r, row in enumerate(rows):
        col = row[0]; vals = row[1:]
        if r % 2 == 0:
            c.setFillColor(HexColor("#f3f4f6")); c.rect(40, ty - 4, total, 13, stroke=0, fill=1)
        c.setFillColor(col); c.rect(40, ty - 4, 3, 13, stroke=0, fill=1)
        for i, v in enumerate(vals):
            txt(c, xs[i] + 6, ty, v, "B" if i in (1, 3) else "R", 7.4 if i >= 4 else 8, MUTED if v == us else INK)
        ty -= 13
    ty -= 10
    c.setFillColor(HexColor("#fdf2f8")); c.setStrokeColor(C_HAT); c.setLineWidth(1)
    c.roundRect(40, ty - 40, total, 48, 6, stroke=1, fill=1)
    txt(c, 52, ty - 6, "I2C buses", "B", 9, C_HAT)
    txt(c, 52, ty - 20, "I2C-1 (GPIO2/3, pins 3 and 5):  0x3C = OLED   ·   0x4C = PCM5122 DAC (i2cdetect shows \"UU\" once the driver has claimed it)", "R", 8)
    txt(c, 52, ty - 32, "I2C-0 (GPIO0/1, pins 27 and 28):  0x50 = the HAT's EEPROM; the Pi uses it only to recognise the HAT automatically", "R", 8)
    footer(c, 3)


# ---------------------------------------------------------------- page 4
def page4(c):
    title(c, "Notes and checks",
          "Clashes, free pins, what the code does not tell, and how to check the wiring.")
    y = H - 92
    sections = [
        ("Power and clashes", [
            "The Pi is powered through header pin 2 (5V) and pin 6 (GND). The HAT, and the OLED and encoder (from pins 4 and 14), run from the same rail: "
            "pins 2 and 4 are the same 5V rail, and pins 6 and 14 the same GND.",
            "The OLED (0x3C) and the PCM5122 DAC control (0x4C) share I2C-1 at different addresses, so they do not disturb each other.",
            "The HAT does not use the encoder pins (GPIO17, 27, 22) or the relay pin (GPIO26). The 3.3V pins (1, 17) stay free. "
            "Still free: GPIO4, 14, 15, 23, 24, 5, 6, 12, 13, 16, 20, among others.",
        ]),
        ("The USB speaker and the relay", [
            "The relay switches only the speaker's +5V: the supply's +5V goes to COM, NO goes to the speaker's VBUS, and the speaker's GND comes straight "
            "from the supply. NC is unused. With GPIO26 high the contact closes and the speaker is powered; with the Pi off (or GPIO26 low) the speaker is off.",
            "Check the module's contact rating against the speaker's current, and that the supply can carry the Pi, HAT, OLED, the relay coil and the speaker together.",
            "The speaker is wired through a USB socket on a cable (pigtail), with VBUS and GND only. The audio path from the DAC to the speaker is not part of this drawing.",
        ]),
        ("Check: 5 V and the GPIOs (the Pi's pins tolerate 3.3 V only)", [
            "OLED: if the module's I2C pull-ups go to its VCC (5 V), 5 V reaches GPIO2/3 at rest. Measure SDA at rest: it must not exceed 3.3 V. "
            "Many modules pull up to the 3.3 V side, but it depends on the module.",
            "Encoder: a bare encoder needs no \"+\" pin (the Pi's inputs can use their internal pull-ups; check what moOde's encoder setting does). If the module (e.g. KY-040) has pull-ups to \"+\" and \"+\" gets 5 V, "
            "the GPIO17/27/22 idle level can be 5 V. Then connect \"+\" to 3.3V (pin 1 or 17) or leave it unconnected; the button needs only GND.",
            "Relay: the module input is driven by the Pi's 3.3 V signal. Most modules switch on that, but verify. Many cheap modules are active-low (LOW closes): "
            "there HIGH would release the relay. The stated behaviour (GPIO26 high while playing) means an active-high module or inverted logic. "
            "The Pi's GND and the relay supply's GND must be common (the same supply does that).",
            "Powering the Pi from the header bypasses its input protection (fuse): do not plug in micro-USB power at the same time, and use a stable 5 V supply "
            "(the HAT, OLED, relay and speaker all load it).",
        ]),
        ("What the code states", [
            "OLED: luma.oled sh1106, i2c(port=1, address=0x3C), 128×64 (good_moode/config.py: I2C_ADDR, state_machine.py). Button: BUTTON_PIN = 22 (BCM), /dev/gpiochip0, "
            "internal pull-up, a press pulls the pin to GND; a press runs mpc toggle.",
            "Rotary encoder: the volume is controlled by moOde's own rotary encoder support (S1 = GPIO17, S2 = GPIO27 are the pins set there). good_moode only shows the volume: it follows MPD's mixer events, so the bar changes as soon as moOde changes the volume.",
            "Relay (good_moode/config.py: RELAY_PIN = 26, good_moode/hardware.py: Relay): GPIO26 high while playing, low otherwise; it drops after 2 s of continuous idle "
            "(RELAY_OFF_DELAY) so a short pause or an mpc error does not click it, and it is switched off on shutdown.",
        ]),
        ("What the code does not state", [
            "Whether the HAT has a pass-through header: the Waveshare page does not say. If not, wires go to the Pi Zero's solder pads on the back or to a stacking header.",
            "The sound card is called IQaudIODAC according to Waveshare; pick the matching I2S device in moOde. Checks: aplay -l, i2cdetect -y 1 (3C and 4C or UU), pinctrl get 17,22,26,27.",
            "GPIO22 (the encoder's KEY) is the play/pause button of good_moode. Do not assign the same pin to the encoder's switch in moOde's settings, "
            "otherwise both react to one press (a double toggle). "
            "experiment/rotary_encoder_test.py is obsolete (moOde handles the encoder); do not run it next to moOde's encoder setting.",
        ]),
    ]
    for head, items in sections:
        txt(c, 40, y, head, "B", 10); y -= 5
        c.setStrokeColor(LINE); c.line(40, y, W - 40, y); y -= 11
        for it in items:
            c.setFillColor(MUTED); c.circle(46, y + 3, 1.6, stroke=0, fill=1)
            y = para(c, 56, y, it, W - 110, "R", 7.8, INK, 10) - 2
        y -= 3
    footer(c, 4)


def main() -> None:
    c = canvas.Canvas(str(OUT), pagesize=landscape(A4))
    c.setTitle("Raspberry Pi Zero 2 W + Waveshare PCM5122 HAT – wiring PLAN (good_moode)")
    c.setAuthor("good_moode")
    page1(c); c.showPage(); page2(c); c.showPage(); page3(c); c.showPage(); page4(c)
    c.save()
    print("saved", OUT)


if __name__ == "__main__":
    main()
