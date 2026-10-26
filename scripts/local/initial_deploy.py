"""Scenario: first-time setup of a freshly installed moOde system.

    python scripts/local/initial_deploy.py
    python scripts/local/initial_deploy.py --logging          # also switch logging on (off by default)
    python scripts/local/initial_deploy.py --reset-stations   # also delete moOde's default radio stations (asks first)
    python scripts/local/initial_deploy.py --reset-stations --yes   # ... without asking

Enables I2C, creates ~/scripts and its virtual environment, uploads the files, installs
the Python libraries and installs moOde's ready-script (which
starts the program after every boot). It can be repeated safely. Adding the radio
station stays manual, in moOde's web UI.
"""

import sys

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.setup(reset_stations="--reset-stations" in sys.argv, assume_yes="--yes" in sys.argv,
             logging="--logging" in sys.argv)
finally:
    pi.close()
