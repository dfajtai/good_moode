#!/usr/bin/env python3
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
import time

serial = i2c(port=1, address=0x3C)
device = sh1106(serial, width=128, height=64)

device.clear()
time.sleep(2)

device.show()
i = 0
while True:
    print(i)
    time.sleep(1)
    i+=1