"""Scenario: remote control of the playback on the Pi.

    python scripts/local/remote.py play
    python scripts/local/remote.py pause          # pause / resume: toggle
    python scripts/local/remote.py toggle
    python scripts/local/remote.py stop
    python scripts/local/remote.py mute           # volume 0, the old volume is remembered
    python scripts/local/remote.py unmute
    python scripts/local/remote.py volume 40      # set the volume (0-100)
    python scripts/local/remote.py volume +5      # or change it: +5 / -5

It runs ``mpc`` on the Pi over SSH and prints the playback status afterwards. For several commands in a
row use the interactive shell: python scripts/local/pi_remote.py
"""

import sys

from pi_remote import PLAYBACK_ACTIONS, connect_from_env  # same folder

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args or args[0] not in PLAYBACK_ACTIONS:
    sys.exit(__doc__)

pi = connect_from_env()
try:
    ok = pi.control(args[0], args[1] if len(args) > 1 else None)
finally:
    pi.close()
sys.exit(0 if ok else 1)
