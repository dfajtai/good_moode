"""OLED status display, play/pause button and relay for moOde audio player.

Runs as a standalone process alongside moOde/MPD on a Raspberry Pi. It polls
MPD's playback state and drives a small SH1106 OLED display through two
screens:

- IdleScreen:    a simple clock, shown while nothing is playing.
- PlayingScreen: scrolling "title - artist", a large clock and a thin volume
                 bar, shown while MPD is playing. Track info comes from the
                 radio stream's ICY metadata, not from MPD directly.

A single GPIO push-button (BCM pin ``BUTTON_PIN``) toggles play/pause via
``mpc toggle``, polled from a background thread (see ``GPIOButton``).

A relay on BCM pin ``RELAY_PIN`` (e.g. switching the amplifier's power) is
driven high while MPD is playing and low otherwise (see ``Relay``).

Entry point: ``main()``; run it with ``python -m good_moode``.
"""

import asyncio
import time
from typing import Optional

from luma.core.interface.serial import i2c
from luma.oled.device import sh1106

from .config import (
    BUTTON_PIN, I2C_ADDR, POLL_INTERVAL, RELAY_PIN, STATE_IDLE, STATE_PLAYING, STREAM_URL,
)
from .hardware import GPIOButton, Relay
from .log import log
from .mpd import VolumeWatcher, ensure_stream_playing, get_state, read_status, resume_playback, toggle_play_pause
from .recovery import PlaybackRecovery
from .screens import IdleScreen, PlayingScreen


class MoodeStateMachine:
    """Top-level orchestrator: owns the display, button, relay and both screens.

    Polls MPD's playback state (``get_state``) and switches between
    ``IdleScreen`` and ``PlayingScreen`` accordingly, driving the
    asyncio-based metadata polling task for the playing screen only while
    it's actually needed.
    """

    def __init__(self) -> None:
        # Kick playback into the same state a manual "Play" from the web UI
        # would produce, before anything else (screens, GPIO) is set up.
        # See ensure_stream_playing() docstring for why this is needed.
        ensure_stream_playing()

        self.serial = i2c(port=1, address=I2C_ADDR)
        self.display = sh1106(self.serial, width=128, height=64)

        self.state: Optional[int] = None
        self.recovery = PlaybackRecovery()  # resumes playback after an MPD error, see recovery.py
        self.last_poll = 0.0

        self.idle_screen = IdleScreen(self.display)
        # The volume is changed by moOde itself; follow MPD's mixer events so the bar shows the real value at once.
        self.volume = VolumeWatcher()
        self.volume.start()
        self.playing_screen = PlayingScreen(
            self.display, STREAM_URL, volume_source=lambda: self.volume.value, volume_interval=0.0
        )
        self.play_task: asyncio.Task | None = None

        self.CONTRAST_IDLE = 40
        self.CONTRAST_PLAYING = 180

        self.button = GPIOButton(BUTTON_PIN, toggle_play_pause)

        # A relay that can't be set up (e.g. the line is already taken) must
        # not take the display down with it; it is only logged.
        self.relay: Optional[Relay] = None
        try:
            self.relay = Relay(RELAY_PIN)
        except Exception as exc:
            log(f"[RELAY ERROR] could not set up GPIO{RELAY_PIN}: {exc}", flush=True)

        # Establish the initial screen/state based on what MPD is actually
        # doing right now (rather than defaulting to idle and waiting for
        # the first poll in run()).
        self.state = get_state()
        self._apply_state(self.state)
        if self.relay:
            self.relay.update(self.state == STATE_PLAYING)

    def _apply_state(self, new_state: int) -> None:
        """Switch the active screen to match ``new_state``.

        Enables/disables rendering on the playing screen and, when
        transitioning to idle, cancels the metadata polling task (it has
        nothing useful to display while idle).
        """
        self.playing_screen.render_enabled = (new_state == STATE_PLAYING)

        if new_state == STATE_IDLE:
            # Transitioning to idle: stop the (now pointless) metadata
            # polling task.
            if self.play_task:
                self.play_task.cancel()
                self.play_task = None

        self.state = new_state

    async def run(self) -> None:
        """Main event loop: poll MPD state, switch screens, refresh the display.

        Runs until cancelled (e.g. by Ctrl-C in ``main()``). On exit,
        always cancels any in-flight metadata task, stops the GPIO
        button's polling thread and switches the relay off, so the process
        can shut down cleanly.
        """
        try:
            while True:
                now = time.monotonic()

                if now - self.last_poll > POLL_INTERVAL:
                    new_state, error = read_status()
                    if self.recovery.update(new_state, error, now):
                        resume_playback()
                    if new_state != self.state:
                        # Transitioning to idle: cancel and await the
                        # metadata task's cleanup before switching screens.
                        if new_state == STATE_IDLE and self.play_task:
                            self.play_task.cancel()
                            try:
                                await asyncio.wait_for(self.play_task, timeout=0.5)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                            self.play_task = None

                        self._apply_state(new_state)

                    # Transitioning to (or remaining in) playing: (re)start
                    # the metadata polling task if it isn't already running.
                    if self.state == STATE_PLAYING and self.play_task is None:
                        self.play_task = asyncio.create_task(
                            self.playing_screen._main()
                        )

                    if self.relay:
                        self.relay.update(self.state == STATE_PLAYING)

                    self.last_poll = now

                # Refresh the currently active screen.
                if self.state == STATE_IDLE:
                    await self.idle_screen.update()
                # While STATE_PLAYING, the playing_screen's own _main() task
                # (started above) handles its own periodic redraws.

                await asyncio.sleep(0.2)

        finally:
            if self.play_task:
                self.play_task.cancel()

            if self.button:
                self.button.stop()

            if self.relay:
                self.relay.stop()

            self.volume.stop()


def main() -> None:
    """Entry point: build and run the state machine.

    Construction is wrapped in try/except so that a startup failure (e.g.
    missing font file, I2C error) is printed with a full traceback into
    state_machine.log instead of the process just disappearing without an
    explanation.
    """
    try:
        sm = MoodeStateMachine()
    except Exception:
        import traceback
        traceback.print_exc()
        raise

    try:
        asyncio.run(sm.run())
    except KeyboardInterrupt:
        pass
