#!/usr/bin/env python3
import time
import subprocess
import threading

from PIL import ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas

# ================== KONFIG ==================

NOWPLAYING_CMD = ["python3", "/home/fajtai/scripts/nowplaying.py"]

FONT_BIG = "./fonts/NotoSans-Bold.ttf"
FONT_SMALL = "./fonts/NotoSans-Medium.ttf"

I2C_ADDR = 0x3C

STATE_IDLE = 0
STATE_PLAYING = 1

POLL_INTERVAL = 1.0
CLOCK_SHIFT_INTERVAL = 60

# ================== OLED ==================

serial = i2c(port=1, address=I2C_ADDR)
device = sh1106(serial, width=128, height=64)

font_big = ImageFont.truetype(FONT_BIG, 28)
font_small = ImageFont.truetype(FONT_SMALL, 11)

# ================== MPD POLL ==================

def get_state():
    try:
        out = subprocess.check_output(["mpc", "status"], text=True)
        if "[playing]" in out:
            return STATE_PLAYING
    except:
        pass
    return STATE_IDLE


# ================== IDLE DRAW ==================

blink = True
last_blink = 0
shift_x = 0
shift_y = 0
last_shift = 0


def draw_idle():
    global blink, last_blink, shift_x, shift_y, last_shift

    now = time.time()

    if now - last_blink >= 1:
        blink = not blink
        last_blink = now

    if now - last_shift >= CLOCK_SHIFT_INTERVAL:
        shift_x = (shift_x + 1) % 4
        shift_y = (shift_y + 1) % 4
        last_shift = now

    t = time.localtime()
    colon = ":" if blink else " "
    timestr = f"{t.tm_hour:02d}{colon}{t.tm_min:02d}"
    datestr = time.strftime("%Y-%m-%d")

    with canvas(device) as draw:
        w, _ = draw.textbbox((0, 0), timestr, font=font_big)[2:]
        x = (128 - w) // 2 + shift_x
        y = 12 + shift_y

        draw.text((x, y), timestr, font=font_big, fill="white")

        dw, _ = draw.textbbox((0, 0), datestr, font=font_small)[2:]
        draw.text(((128 - dw) // 2), y + 34, datestr, font=font_small, fill="white")


# ================== NOW PLAYING PROCESS ==================

np_proc = None


def start_nowplaying():
    global np_proc
    if np_proc and np_proc.poll() is None:
        return
    np_proc = subprocess.Popen(NOWPLAYING_CMD)


def stop_nowplaying():
    global np_proc
    if np_proc and np_proc.poll() is None:
        np_proc.terminate()
        try:
            np_proc.wait(timeout=2)
        except:
            np_proc.kill()
    np_proc = None


# ================== MAIN STATE MACHINE ==================

def main():
    state = get_state()
    last_poll = 0

    if state == STATE_PLAYING:
        start_nowplaying()

    while True:
        now = time.monotonic()

        if now - last_poll >= POLL_INTERVAL:
            new_state = get_state()

            if new_state != state:
                state = new_state

                if state == STATE_PLAYING:
                    start_nowplaying()
                else:
                    stop_nowplaying()

            last_poll = now

        if state == STATE_IDLE:
            draw_idle()

        time.sleep(0.05)


# ================== START ==================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop_nowplaying()
