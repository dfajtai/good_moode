"""Scenario: is the radio all right? A grouped table, like top.

    python scripts/local/status.py              # once
    python scripts/local/status.py --watch      # redraw every 3 seconds until Ctrl+C
    python scripts/local/status.py --watch 10   # ... every 10 seconds
    python scripts/local/status.py --raw        # also print the raw output under the table

The table starts with the verdict (STATUS: OK or the list of problems, each marked "!!" in its cell), then
PLAYBACK (state, volume, title, stream), PROGRAM (running, relay, logging), NETWORK (Wi-Fi link and its retry /
beacon counters, router / internet / stream host pings with loss and round-trip times, DNS and TCP timing, packet
counts), SYSTEM (temperature, power, memory, disk) and MOODE / MPD (moOde's own status, what ALSA really outputs,
MPD version and uptime). Every line has the same fixed width. The exit code is 0 when all is well and 1 when there
is a problem, so it can be used in scripts.
"""

import sys
import time

from pi_remote import connect_from_env  # same folder

args = sys.argv[1:]
raw = "--raw" in args
watch = None
if "--watch" in args:
    after = args[args.index("--watch") + 1:][:1]
    watch = float(after[0]) if after and after[0].replace(".", "", 1).isdigit() else 3.0

pi = connect_from_env()
healthy = True
try:
    if watch is None:
        healthy = pi.status(raw=raw)
    else:
        try:
            while True:
                print("\033[H\033[2J", end="")  # clear the screen and go to the top, like top
                healthy = pi.status(raw=raw)
                print(f"\n(every {watch:g} s + the ~3 s the checks take; Ctrl+C quits)")
                time.sleep(watch)
        except KeyboardInterrupt:
            print()
finally:
    pi.close()
sys.exit(0 if healthy else 1)
