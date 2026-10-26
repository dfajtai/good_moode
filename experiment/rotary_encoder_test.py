#!/home/fajtai/scripts/.venv/bin/python
"""OBSOLETE experiment: volume is handled by moOde's own rotary encoder support (Peripherals).

Kept only as a pin-wiring test. Known problems, which are why moOde's support is used instead:
- the direction comes from the sign of encoder.steps (a running total), not from the last turn, so after
  turning right a few steps, turning left still sends "up" until the total crosses zero;
- every step is a separate, blocking HTTP call (0.3 s timeout) to a slow PHP command, so fast turning
  loses or delays steps and the real and the shown volume drift apart.
Do not run it together with moOde's rotary encoder setting or good_moode (they claim the same pins).
"""
import requests
import gpiozero
from signal import pause

# === OWN COMMANDS (KEPT) ===
VOLUME_BASE = "http://localhost/command/?cmd="
LEFT_URL  = f"{VOLUME_BASE}set_volume dn 5"
RIGHT_URL = f"{VOLUME_BASE}set_volume up 5"
SHORT_URL = f"{VOLUME_BASE}toggle_play_pause"
LONG_URL  = f"{VOLUME_BASE}set_volume 0"

# === PINS: S1=17, S2=27, KEY=22 ===
encoder = gpiozero.RotaryEncoder(17, 27, wrap=False)
button = gpiozero.Button(22, hold_time=1.0)

def send_http(url, emoji):
    try:
        requests.get(url, timeout=0.3)
        print(f"{emoji} OK")
    except:
        print(f"{emoji} FAIL")

def on_rotate():
    steps = encoder.steps
    if steps > 0:
        send_http(RIGHT_URL, "➡️ up 5")
    elif steps < 0:
        send_http(LEFT_URL, "⬅️ dn 5")

def short_press():
    send_http(SHORT_URL, "▶️ toggle")

def long_press():
    send_http(LONG_URL, "🔇 mute 0")

# === EVENT HANDLERS ===
encoder.when_rotated = on_rotate
button.when_pressed = short_press
button.when_held = long_press

print("🎛️  Rotary Encoder (17,27,22)")
print("⬅️  set_volume dn 5")
print("➡️  set_volume up 5")
print("▶️  toggle_play_pause (short press)")
print("🔇  set_volume 0 (1 s long press)")
print("\nTURN / PRESS! Ctrl+C quits")

pause()
