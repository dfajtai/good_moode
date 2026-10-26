"""Timestamped logging for the service log (state_machine.log)."""

import time


def log(*args, **kwargs) -> None:
    """print() with a timestamp, for the service log (state_machine.log)."""
    kwargs.setdefault("flush", True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, **kwargs)
