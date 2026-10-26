#!/home/fajtai/scripts/.venv/bin/python
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
