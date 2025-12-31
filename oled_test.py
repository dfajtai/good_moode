#!/usr/bin/env python3
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas
from PIL import ImageFont
import time

serial = i2c(port=1, address=0x3C)
device = sh1106(serial, width=128, height=64)

font = ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12
)

while True:
    with canvas(device) as draw:
        draw.text((0, 0), "OLED OK", fill="white", font=font)
        draw.text((0, 16), "1234567890", fill="white", font=font)
        draw.text((0, 32), "abcdef ABCDEF", fill="white", font=font)
        draw.text((0, 48), "áéíóöőúüű", fill="white", font=font)

    time.sleep(1)