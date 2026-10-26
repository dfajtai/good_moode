"""Timestamped logging. run_app.sh sends the output to /dev/null unless a log file is set there."""

import time


def log(*args, **kwargs) -> None:
    """print() with a timestamp; the output goes where run_app.sh points it (off by default)."""
    kwargs.setdefault("flush", True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, **kwargs)
