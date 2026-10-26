"""Timestamped logging that is completely inert unless logging mode is on.

Logging mode is on when the environment variable GOOD_MOODE_LOGGING=1 (run_app.sh sets it if the
flag file ~/scripts/logging.on exists, i.e. after ``deploy.py --logging``) or when the program is
run by hand in a terminal. Otherwise log() returns at once: nothing is formatted or printed.
"""

import os
import sys
import time

# Decided once at start-up. Tests and tools can set it: ``good_moode.log.LOGGING = True``.
LOGGING = os.environ.get("GOOD_MOODE_LOGGING") == "1" or sys.stdout.isatty()


def log(*args, **kwargs) -> None:
    """print() with a timestamp (flushed at once); does nothing outside logging mode."""
    if not LOGGING:
        return
    kwargs.setdefault("flush", True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, **kwargs)
