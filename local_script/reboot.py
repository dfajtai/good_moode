"""Scenario: reboot the Pi.

    python local_script/reboot.py          # asks for confirmation
    python local_script/reboot.py --yes    # no question
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from pi_remote import connect_from_env  # noqa: E402

pi = connect_from_env()
try:
    pi.power("reboot", assume_yes="--yes" in sys.argv)
finally:
    pi.close()
