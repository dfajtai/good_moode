"""Scenario: push the scripts to the Pi and make them take effect.

    python scripts/local/deploy.py

Uploads the good_moode/ package and scripts/pi/*.sh to ~/scripts, installs ready-script.sh into moOde,
restarts the OLED program and the logger, then prints the Pi's status so you
can see everything is running.
"""

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.deploy()
    print("\n--- újraindítás ---")
    pi.restart()
    print("\n--- állapot ---")
    pi.status()
finally:
    pi.close()
