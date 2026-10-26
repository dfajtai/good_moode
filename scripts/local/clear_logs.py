"""Scenario: delete the logs on the Pi (it is the opposite of download.py).

    python scripts/local/clear_logs.py            # our logs: diagnostics log + rotated copies, launcher error
                                                  # file, program log (asks first)
    python scripts/local/clear_logs.py --all      # also empty moOde's log and the systemd journals
    python scripts/local/clear_logs.py --yes      # do not ask

Download first if you want to keep them: python scripts/local/download.py. The copies already in
pi_logs/ on the PC are not touched, and logging itself is not switched off (see deploy.py --logging).
"""

import sys

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.clear_logs(include_system="--all" in sys.argv, assume_yes="--yes" in sys.argv)
finally:
    pi.close()
