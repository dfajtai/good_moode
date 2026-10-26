"""Constants of the moOde OLED / button / relay program.

Everything that is a setting (pins, addresses, URLs, intervals) lives here, so
the other modules only import names from this one.
"""

from pathlib import Path

I2C_ADDR = 0x3C  # I2C address of the SH1106 OLED display

PACKAGE_DIR = Path(__file__).resolve().parent
FONT_BIG = str(PACKAGE_DIR / "fonts" / "NotoSans-Bold.ttf")
FONT_SMALL = str(PACKAGE_DIR / "fonts" / "NotoSans-Medium.ttf")

STREAM_URL = "http://stream.radiomost.hu:8200/live.mp3"

MOODE_VOL_URL = "http://localhost/command/?cmd=get_volume"

# The station's automation has been sending this fixed placeholder (followed
# by a phone number) as StreamTitle instead of the real track since ~2026-07,
# so it carries no information; see radio.is_placeholder_title().
PLACEHOLDER_TITLE_PREFIX = "telefon-sms-viber"
STATION_NAME = "Kaposvár Most"

# Playback states used throughout the package. Plain ints (not an Enum) to
# keep comparisons against MPD's own status output cheap and simple.
STATE_IDLE = 0
STATE_PLAYING = 1

BUTTON_PIN = 22  # BCM numbering, i.e. physical pin 15 on the 40-pin header
GPIO_CHIP = "/dev/gpiochip0"

RELAY_PIN = 26  # BCM numbering, i.e. physical pin 37; high = playing, low = otherwise
RELAY_OFF_DELAY = 2.0  # seconds of continuous idle before the relay drops (see hardware.Relay)

SHIFT_MAX = 3  # largest burn-in shift in pixels (shift cycles 0..SHIFT_MAX)

POLL_INTERVAL = 0.2  # seconds between MPD status polls in the main loop

# Each ICY metadata read opens a new connection to the radio stream and pulls
# ~16 KB through the Pi's (weak) Wi-Fi, competing with MPD's own stream. Read it
# rarely; the scroll animation and clock still tick every PlayingScreen interval.
METADATA_INTERVAL = 5.0  # seconds between ICY metadata reads
VOLUME_INTERVAL = 1.0  # seconds between volume reads (each one runs ``mpc``)
