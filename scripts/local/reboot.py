"""Scenario: reboot the Pi.

    python scripts/local/reboot.py          # asks for confirmation
    python scripts/local/reboot.py --yes    # no question
"""

import sys

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.power("reboot", assume_yes="--yes" in sys.argv)
finally:
    pi.close()
