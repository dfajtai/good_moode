"""Scenario: push the scripts to the Pi and make them take effect.

    python local_script/deploy.py

Uploads scripts/*.py|sh to ~/scripts, installs ready-script.sh into moOde,
restarts the OLED program and the logger, then prints the Pi's status so you
can see everything is running.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from pi_remote import connect_from_env  # noqa: E402

pi = connect_from_env()
try:
    pi.deploy()
    print("\n--- újraindítás ---")
    pi.restart()
    print("\n--- állapot ---")
    pi.status()
finally:
    pi.close()
