"""GPIO hardware on the Pi: the play/pause push-button and the relay (libgpiod 2.x)."""

import threading
import time
from typing import Callable, Optional

import gpiod
from gpiod.line import Bias, Direction, Value

from .config import GPIO_CHIP, RELAY_OFF_DELAY
from .log import log


class GPIOButton:
    """Simple polling-based GPIO push-button using libgpiod 2.x.

    Works without root if the invoking user has permission to access
    ``GPIO_CHIP``. The line is configured as an input with an internal
    pull-up, so the resting (not pressed) state reads as ACTIVE (1) and a
    press pulls it to INACTIVE (0) — i.e. a falling edge is a press.

    Runs its own polling thread rather than using edge-triggered
    interrupts, for simplicity; ``POLL_SLEEP`` (hardcoded to 0.01s below)
    keeps CPU usage low while still catching short presses reliably.

    Bug fix history (2026-09): the initial value stored in ``_last_state``
    used to be the raw ``gpiod.line.Value`` enum object, while every
    subsequent read in ``_loop`` compared against its plain ``int``
    (``.value``). Comparing an int to a non-IntEnum member never evaluates
    equal, so the very first poll could register a false "change" and the
    state tracking could drift out of sync with reality under real-world
    timing (display thread + asyncio loop running concurrently). Both are
    now normalized to ``int`` consistently. Debug prints were also added
    (instead of a bare ``except: pass``) so a recurring failure shows up in
    the log instead of silently making the button look unresponsive.
    """

    def __init__(self, line: int, on_press: Callable[[], None], debounce: float = 0.1) -> None:
        """
        Args:
            line: GPIO line number (BCM numbering) the button is wired to.
            on_press: Callback invoked (with no arguments) on each detected
                press, from the polling thread.
            debounce: Minimum seconds between two accepted presses; any
                press detected sooner than this after the previous one is
                ignored.
        """
        self.line = line
        self.on_press = on_press
        self.debounce = debounce

        self._last_press = 0.0
        self._stop = threading.Event()

        self.request = gpiod.request_lines(
            GPIO_CHIP,
            consumer="moode-button",
            config={
                line: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                )
            },
        )

        # Normalize to int right away, so it's directly comparable with the
        # int values produced in _loop() below (see bug fix note above).
        initial: Value = self.request.get_value(self.line)
        self._last_state: int = initial.value if hasattr(initial, "value") else initial
        log(f"[GPIO] initial state = {self._last_state}", flush=True)

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        """Poll the GPIO line for a falling edge (button press).

        Runs in its own daemon thread for the lifetime of the object. Any
        exception is logged instead of silently swallowed, so a recurring
        failure (e.g. a stale GPIO line request) shows up in
        state_machine.log instead of just making the button look
        unresponsive.
        """
        POLL_SLEEP = 0.01  # seconds between line reads

        while not self._stop.is_set():
            try:
                value: Value = self.request.get_value(self.line)
                state: int = value.value if hasattr(value, "value") else value

                if state != self._last_state:
                    log(f"[GPIO] state change: {self._last_state} -> {state}", flush=True)

                    # Falling edge (1 -> 0) = button press, since the line
                    # idles high (pull-up) and is pulled low on press.
                    if state == 0 and self._last_state == 1:
                        now = time.monotonic()
                        if now - self._last_press >= self.debounce:
                            self._last_press = now
                            log("[GPIO] PRESS detected, calling on_press()", flush=True)
                            if self.on_press:
                                self.on_press()

                self._last_state = state

            except Exception as exc:
                log(f"[GPIO ERROR] {exc}", flush=True)

            time.sleep(POLL_SLEEP)

    def stop(self) -> None:
        """Stop the polling thread and release the GPIO line request."""
        self._stop.set()
        try:
            self.request.release()
        except Exception:
            pass


class Relay:
    """Relay output that follows the playback state.

    The line is driven high while music is playing and low otherwise, and is
    low from construction until the first ``update(True)``. Dropping the relay
    is delayed by ``off_delay`` seconds of continuous idle: ``get_state()``
    reports idle on any ``mpc`` failure or timeout, and a short pause or
    track change should not click the relay (and whatever it powers) off
    and on again. Switching on is immediate.
    """

    def __init__(self, line: int, off_delay: float = RELAY_OFF_DELAY) -> None:
        """
        Args:
            line: GPIO line number (BCM numbering) the relay input is wired to.
            off_delay: Seconds of continuous idle before the relay is
                switched off.
        """
        self.line = line
        self.off_delay = off_delay

        self._on = False
        self._idle_since: Optional[float] = None

        self.request = gpiod.request_lines(
            GPIO_CHIP,
            consumer="moode-relay",
            config={
                line: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE,
                )
            },
        )
        log(f"[RELAY] GPIO{line} ready, relay off", flush=True)

    def _set(self, on: bool) -> None:
        """Drive the line; a failure is logged and retried on the next update."""
        try:
            self.request.set_value(self.line, Value.ACTIVE if on else Value.INACTIVE)
            self._on = on
            log(f"[RELAY] {'ON' if on else 'OFF'}", flush=True)
        except Exception as exc:
            log(f"[RELAY ERROR] {exc}", flush=True)

    def update(self, playing: bool) -> None:
        """Feed the current playback state; call this on every state poll."""
        if playing:
            self._idle_since = None
            if not self._on:
                self._set(True)
            return

        if not self._on:
            return
        now = time.monotonic()
        if self._idle_since is None:
            self._idle_since = now
        elif now - self._idle_since >= self.off_delay:
            self._set(False)

    def stop(self) -> None:
        """Switch the relay off and release the GPIO line request."""
        try:
            self.request.set_value(self.line, Value.INACTIVE)
        except Exception:
            pass
        try:
            self.request.release()
        except Exception:
            pass
