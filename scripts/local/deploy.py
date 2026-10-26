"""Scenario: push the code to the Pi and make it take effect (for updates).

    python scripts/local/deploy.py              # logging OFF: nothing is written to the Pi's SD card
    python scripts/local/deploy.py --logging    # logging ON: program log + diagnostics logger

Uploads the good_moode/ package and scripts/pi/*.sh|txt to ~/scripts, installs ready-script.sh into
moOde, restarts the OLED program and the logger, then prints the Pi's status so you can see that
everything is running. Every deploy sets the logging switch to what the command line says. For a
freshly installed moOde use initial_deploy.py first.
"""

import sys

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.deploy(logging="--logging" in sys.argv)
    print("\n--- restarting ---")
    pi.restart()
    print("\n--- status ---")
    pi.status()
finally:
    pi.close()
