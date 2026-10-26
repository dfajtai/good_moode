"""Decide when to resume playback that MPD stopped because of an error.

When the Wi-Fi drops, MPD's stream connection times out and MPD stops with an ``ERROR:`` line in its
status. Without help it stays stopped until somebody presses play. ``PlaybackRecovery`` tells the
main loop when to try ``mpc play`` again. It never touches MPD itself, so it can be tested with a
fake clock.

It resumes only when ALL of this holds, so that it never fights the user:

- the program has seen MPD *playing* and then stopped with an error (a stop without an error, e.g.
  pause or stop from the button or the web UI, is left alone);
- the error is still there (a manual play or stop clears it, which ends the recovery);
- less than ``window`` seconds have passed since the error appeared.

Retries are spaced by ``backoff`` seconds (the last value repeats), so a network that stays down is
not hammered.
"""

from typing import Optional, Sequence

from .config import RECOVER_BACKOFF, RECOVER_WINDOW, STATE_PLAYING
from .log import log


class PlaybackRecovery:
    """State of one recovery; call ``update`` on every MPD status poll."""

    def __init__(self, window: float = RECOVER_WINDOW, backoff: Sequence[float] = RECOVER_BACKOFF) -> None:
        self.window = window
        self.backoff = tuple(backoff)
        self._was_playing = False        # MPD was playing and has not stopped with an error since
        self._since: Optional[float] = None  # when the current recovery began; None = not recovering
        self._next_try = 0.0
        self._attempts = 0

    @property
    def recovering(self) -> bool:
        """True while a recovery is in progress."""
        return self._since is not None

    def _reset(self) -> None:
        self._since = None
        self._attempts = 0

    def update(self, state: int, error: Optional[str], now: float) -> bool:
        """Feed one status poll (``now`` = a monotonic time); returns True if playback should be resumed now."""
        if state == STATE_PLAYING:
            if self.recovering:
                log(f"[RECOVER] playing again after {self._attempts} attempt(s)")
            self._was_playing = True
            self._reset()
            return False

        if not self.recovering:
            if error and self._was_playing:
                self._was_playing = False
                self._since = now
                self._attempts = 0
                self._next_try = now + self.backoff[0]
                log(f"[RECOVER] MPD stopped with an error: {error}")
            return False

        if not error:
            log("[RECOVER] the error is gone (stopped or played by hand): giving up")
            self._reset()
            return False
        if now - self._since > self.window:
            log(f"[RECOVER] still failing after {self.window:.0f} s: giving up")
            self._reset()
            return False
        if now < self._next_try:
            return False

        self._attempts += 1
        self._next_try = now + self.backoff[min(self._attempts, len(self.backoff) - 1)]
        log(f"[RECOVER] resuming playback, attempt {self._attempts}")
        return True
