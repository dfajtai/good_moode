#!/usr/bin/env python3
"""OLED status display, play/pause button and relay for moOde audio player.

Runs as a standalone process alongside moOde/MPD on a Raspberry Pi. It polls
MPD's playback state and drives a small SH1106 OLED display through two
screens:

- IdleScreen:    a simple clock, shown while nothing is playing.
- PlayingScreen: artist/title (scrolling), volume bar and clock, shown while
                  MPD is playing. Track info comes from the radio stream's
                  ICY metadata, not from MPD directly.

A single GPIO push-button (BCM pin ``BUTTON_PIN``) toggles play/pause via
``mpc toggle``, polled from a background thread (see ``GPIOButton``).

A relay on BCM pin ``RELAY_PIN`` (e.g. switching the amplifier's power) is
driven high while MPD is playing and low otherwise (see ``Relay``).

Entry point: ``main()`` at the bottom of this file.
"""

import time
import re
import requests
import asyncio
import subprocess
import threading
from pathlib import Path

from typing import Optional, Callable, Any

from PIL import ImageFont
from PIL.ImageFont import FreeTypeFont
from PIL.ImageDraw import ImageDraw
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas

import gpiod
from gpiod.line import Direction, Bias, Value

# ================= CONFIG =================

I2C_ADDR = 0x3C  # I2C address of the SH1106 OLED display

BASE_DIR = Path(__file__).resolve().parent
FONT_BIG = str(BASE_DIR / "fonts" / "NotoSans-Bold.ttf")
FONT_SMALL = str(BASE_DIR / "fonts" / "NotoSans-Medium.ttf")

STREAM_URL = "http://stream.radiomost.hu:8200/live.mp3"

MOODE_VOL_URL = "http://localhost/command/?cmd=get_volume"

# The station's automation has been sending this fixed placeholder (followed
# by a phone number) as StreamTitle instead of the real track since ~2026-07,
# so it carries no information; see is_placeholder_title().
PLACEHOLDER_TITLE_PREFIX = "telefon-sms-viber"
STATION_NAME = "Kaposvár Most"

# Playback states used throughout this module. Plain ints (not an Enum) to
# keep comparisons against MPD's own status output cheap and simple.
STATE_IDLE = 0
STATE_PLAYING = 1

BUTTON_PIN = 22  # BCM numbering, i.e. physical pin 15 on the 40-pin header
GPIO_CHIP = "/dev/gpiochip0"

RELAY_PIN = 26  # BCM numbering, i.e. physical pin 37; high = playing, low = otherwise
RELAY_OFF_DELAY = 2.0  # seconds of continuous idle before the relay drops (see Relay)

SHIFT_MAX = 3  # largest burn-in shift in pixels (shift cycles 0..SHIFT_MAX)

POLL_INTERVAL = 0.2  # seconds between MPD status polls in the main loop

# Each ICY metadata read opens a new connection to the radio stream and pulls
# ~16 KB through the Pi's (weak) Wi-Fi, competing with MPD's own stream. Read it
# rarely; the scroll animation and clock still tick every PlayingScreen interval.
METADATA_INTERVAL = 5.0  # seconds between ICY metadata reads
VOLUME_INTERVAL = 1.0  # seconds between volume reads (each one runs ``mpc``)


# ================= HELPERS =================

def log(*args, **kwargs) -> None:
    """print() with a timestamp, for the service log (state_machine.log)."""
    kwargs.setdefault("flush", True)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, **kwargs)


def extract_title(metadata: str) -> Optional[str]:
    """Extract the ``StreamTitle`` value from a raw ICY metadata block.

    Args:
        metadata: Raw metadata text as read from the stream (see
            ``NowPlayingExtractHandler._read_metadata``).

    Returns:
        The stream title, stripped of surrounding whitespace, or ``None``
        if the metadata block didn't contain a ``StreamTitle`` field.
    """
    m = re.search(r"StreamTitle='([^';]+)", metadata)
    if not m:
        return None
    return m.group(1).strip()


def is_placeholder_title(title: Optional[str]) -> bool:
    """Return True if ``title`` carries no real track info.

    That is the case for an empty title and for the station's fixed
    "Telefon-SMS-Viber_-_<phone number>" placeholder. Only that one
    placeholder is filtered; every other title is treated as real.
    """
    if not title:
        return True
    return title.strip().casefold().startswith(PLACEHOLDER_TITLE_PREFIX)


def get_volume_percent() -> int:
    """Read the current MPD volume as a percentage (0-100).

    Tries ``mpc`` first (fast, local); falls back to moOde's HTTP command
    API if that fails for any reason. Returns 50 as a last-resort default
    so the volume bar always has something sane to draw, rather than
    raising and taking down the display loop.
    """
    try:
        out = subprocess.check_output(["mpc"], text=True, timeout=1)
        vol_match = re.search(r"volume: (\d+)%", out)
        if vol_match:
            return max(0, min(100, int(vol_match.group(1))))
    except Exception:
        pass

    # Fallback to moOde's own HTTP command API
    try:
        r = requests.get(MOODE_VOL_URL, timeout=0.5)
        if r.ok:
            return max(0, min(100, int(r.json().get("volume", 50))))
    except Exception:
        pass
    return 50


def toggle_play_pause() -> None:
    """Toggle MPD play/pause via ``mpc toggle``.

    Used as the GPIO button's press callback (see ``GPIOButton``). Any
    failure is logged to stdout (captured by run_app.sh into
    state_machine.log) rather than raised, since this runs from inside the
    GPIO polling thread and must never crash it.
    """
    try:
        result = subprocess.run(
            ["mpc", "toggle"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            log("[ERROR] MPC toggle failed!")
        else:
            log("[BUTTON] mpc toggle ok")

    except subprocess.TimeoutExpired:
        log("[ERROR] MPC timeout!")
    except FileNotFoundError:
        log("[ERROR] mpc command not found!")
    except Exception as e:
        log(f"[ERROR] MPC exception: {e}")


def get_state() -> int:
    """Return the current playback state: STATE_PLAYING or STATE_IDLE.

    Derived from ``mpc status``; any failure (MPD unreachable, timeout,
    etc.) is treated as idle, which is the safer default for display
    purposes.
    """
    try:
        out = subprocess.check_output(["mpc", "status"], text=True, timeout=0.5)
        if "[playing]" in out:
            return STATE_PLAYING
    except Exception:
        pass
    return STATE_IDLE


def ensure_stream_playing(retries: int = 5, delay: float = 1.0) -> None:
    """Make sure MPD has something loaded and playing at startup.

    Mirrors what happens when the user manually starts playback from the
    web UI: if the queue is empty, load the radio stream and play it; if
    something is queued but paused/stopped, just press play. This works
    around moOde's auto-play/songid restore sometimes leaving the queue
    empty after boot, which made the physical button's ``mpc toggle`` a
    silent no-op (nothing to toggle, no error either).

    Args:
        retries: How many times to retry if MPD isn't ready yet.
        delay: Seconds to wait between retries.
    """
    for attempt in range(1, retries + 1):
        try:
            playlist = subprocess.check_output(["mpc", "playlist"], text=True, timeout=2)

            if playlist.strip():
                # Queue is not empty: make sure it's actually playing.
                status = subprocess.check_output(["mpc", "status"], text=True, timeout=2)
                if "[playing]" in status:
                    log("[STARTUP] Playback already active, nothing to do", flush=True)
                else:
                    log("[STARTUP] Queue present but not playing, starting playback", flush=True)
                    subprocess.run(["mpc", "play"], timeout=2)
                return

            # Queue is empty: load the radio stream, same as a manual
            # "Play" from the web UI would do.
            log(
                f"[STARTUP] Queue is empty (attempt {attempt}/{retries}), "
                f"loading stream and starting playback",
                flush=True,
            )
            subprocess.run(["mpc", "clear"], timeout=2)
            subprocess.run(["mpc", "add", STREAM_URL], timeout=2)
            subprocess.run(["mpc", "play"], timeout=2)
            return

        except Exception as exc:
            log(f"[STARTUP ERROR] {exc}", flush=True)

        time.sleep(delay)


# ================= METADATA HANDLER =================

class NowPlayingExtractHandler:
    """Periodically reads ICY metadata from an audio stream and reports it.

    Runs an internal asyncio loop (``_main`` / ``_loop``) that, on every
    tick:

    1. Reads the stream's current ICY metadata block (blocking network I/O,
       offloaded to a thread via ``run_in_executor``).
    2. If the title changed, calls ``coro(new_title)``; if unchanged, calls
       ``coro(None)`` so the caller can still react to "still the same
       track" (e.g. to keep scrolling text animated).
    3. Always calls ``update_coro()`` afterwards, regardless of whether the
       title changed, for periodic UI refreshes (e.g. volume bar, clock).

    Subclassed by ``PlayingScreen``, which supplies ``coro``/``update_coro``
    to actually draw to the OLED display.
    """

    def __init__(
        self,
        source_url: str,
        interval: float = 2.0,
        coro: Optional[Callable[[Optional[str]], asyncio.Future]] = None,
        update_coro: Optional[Callable[[], asyncio.Future]] = None,
        metadata_interval: float = METADATA_INTERVAL,
    ) -> None:
        """
        Args:
            source_url: URL of the audio stream to read ICY metadata from.
            interval: Seconds between loop ticks (``update_coro`` is called
                on every tick).
            coro: Async callback invoked with the new title (or ``None`` if
                unchanged) whenever the metadata is read.
            update_coro: Async callback invoked on every tick regardless of
                whether the title changed.
            metadata_interval: Seconds between actual metadata reads; each
                read opens a new connection to the stream, so this is much
                longer than ``interval``.
        """
        self.source_url = source_url
        self.interval = interval
        self.metadata_interval = metadata_interval
        self._next_metadata = 0.0
        self.coro = coro
        self.update_coro = update_coro

        self._last_metadata = ""
        self.last_title = ""

        self._stop_event = asyncio.Event()
        self._session: Optional[requests.Session] = None

        self._task: asyncio.Task | None = None

    def _read_metadata(self) -> str:
        """Blocking read of one raw ICY metadata block from the stream.

        Opens (or reuses) an HTTP session with ``Icy-MetaData: 1``, skips
        ahead to the metadata block per the ``icy-metaint`` byte interval,
        and decodes it. Returns an empty string on any failure or if the
        stream doesn't advertise ICY metadata at all.

        This performs blocking network I/O and is meant to be run in a
        worker thread via ``_read_metadata_async``, never directly on the
        event loop.
        """
        r = None
        try:
            if not self._session:
                self._session = requests.Session()
                self._session.headers.update({
                    "Icy-MetaData": "1",
                    "User-Agent": "RadioMetadataExtractor/1.0",
                })

            r = self._session.get(self.source_url, stream=True, timeout=2)
            r.raise_for_status()

            metaint = int(r.headers.get("icy-metaint", "0"))
            if not metaint:
                return ""

            _ = r.raw.read(metaint)  # skip the audio bytes preceding the metadata block
            lb = r.raw.read(1)
            if not lb:
                return ""

            meta_len = ord(lb) * 16  # ICY metadata length is encoded in 16-byte units
            if meta_len:
                meta = r.raw.read(meta_len)
                return meta.decode("iso-8859-2", "ignore")
        except Exception as e:
            log(f"[META ERROR] {e}")
        finally:
            if r is not None:
                r.close()  # don't leave the stream connection open
        return ""

    async def _read_metadata_async(self) -> str:
        """Async wrapper that runs the blocking metadata read in a thread.

        Bounded by a 3-second timeout so a slow/stalled stream can never
        block the asyncio event loop indefinitely.
        """
        loop = asyncio.get_event_loop()
        try:
            # asyncio.to_thread (Python 3.9+) would also work here;
            # run_in_executor is used directly for explicit control.
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._read_metadata),
                timeout=3.0
            )
        except asyncio.TimeoutError:
            log("[META TIMEOUT] Metadata read took too long")
            return ""
        except Exception as e:
            log(f"[META ERROR] {e}")
            return ""

    async def _loop(self) -> None:
        """Main loop: ticks every ``self.interval`` seconds, reads metadata when due.

        ``update_coro`` runs on every tick (animation, volume, clock), but
        the metadata itself is only read every ``self.metadata_interval``
        seconds, starting with the first tick.

        Uses a fixed schedule (``next_t`` accumulates by ``interval`` each
        iteration) rather than a plain ``sleep(interval)`` after each tick,
        so the cadence doesn't drift by the time the tick itself takes.
        """
        try:
            self._next_metadata = 0.0
            next_t = time.monotonic() + self.interval

            while not self._stop_event.is_set():
                if time.monotonic() >= self._next_metadata:
                    self._next_metadata = time.monotonic() + self.metadata_interval
                    meta = await self._read_metadata_async()
                    if meta:
                        if meta != self._last_metadata:
                            # Title (or other ICY field) changed since last read.
                            self._last_metadata = meta
                            self.last_title = extract_title(meta) or ""
                            if self.coro:
                                await self.coro(self.last_title)
                        else:
                            # Same metadata as before; still notify the caller
                            # with None so it can do periodic bookkeeping.
                            if self.coro:
                                await self.coro(None)

                if self.update_coro:
                    await self.update_coro()

                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_t += self.interval

        except asyncio.CancelledError:
            pass

    async def _main(self) -> None:
        """Set up the HTTP session and run the polling loop until cancelled."""
        self._session = requests.Session()
        self._session.headers.update({
            "Icy-MetaData": "1",
            "User-Agent": "RadioMetadataExtractor/1.0",
        })

        try:
            self._task = asyncio.create_task(self._loop())
            await self._task
        finally:
            self._session.close()

    async def stop(self) -> None:
        """Signal the loop to stop and wait (briefly) for it to finish."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except asyncio.TimeoutError:
                pass
        if self._session:
            self._session.close()


# ================= GPIO BUTTON (libgpiod 2.x) =================

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


# ================= RELAY (libgpiod 2.x) =================

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


# ================= IDLE SCREEN =================

class IdleScreen:
    """Clock screen shown on the OLED while playback is idle/stopped.

    Draws a large HH:MM clock (with a blinking colon) and the current
    date, both slightly shifted by a few pixels every minute to reduce
    OLED burn-in.
    """

    def __init__(self, display: sh1106, contrast: int = 50) -> None:
        """
        Args:
            display: The luma.oled device to draw on.
            contrast: OLED contrast level (0-255) used while this screen
                is active.
        """
        self.display = display
        self.contrast = contrast

        # The clock is as large as fits the 128px width (with the burn-in
        # shift margin): "00:00" at 48px is 122px wide. The date is kept
        # clearly smaller so the time dominates.
        self.font_big: FreeTypeFont = ImageFont.truetype(FONT_BIG, 48)
        self.font_small: FreeTypeFont = ImageFont.truetype(FONT_SMALL, 16)

        self.blink = True
        self.last_blink = 0.0

        # Burn-in mitigation: shift the drawn content by a few pixels
        # periodically (see _draw below).
        self.shift_x = 0
        self.shift_y = 0
        self.last_shift = 0.0

        self._draw()

    def _set_contrast(self, val: int) -> None:
        """Apply the OLED contrast level; ignore errors from the display driver."""
        try:
            self.display.contrast(val)
        except Exception:
            pass

    def _draw(self) -> None:
        """Render the clock screen for the current time."""
        now = time.time()
        self._set_contrast(self.contrast)

        if now - self.last_blink >= 1:
            self.blink = not self.blink
            self.last_blink = now

        if now - self.last_shift >= 60:
            # Cycle the burn-in-avoidance offset through a small 4x4 pattern.
            self.shift_x = (self.shift_x + 1) % 4
            self.shift_y = (self.shift_y + 1) % 4
            self.last_shift = now

        t = time.localtime()
        colon = ":" if self.blink else " "
        timestr = f"{t.tm_hour:02d}{colon}{t.tm_min:02d}"
        datestr = time.strftime("%Y-%m-%d")

        with canvas(self.display) as draw:
            # Position from the glyph bounding boxes. The time is measured
            # with the colon (not the blank used while blinking), so the
            # digits don't jump horizontally on every blink.
            tb = draw.textbbox((0, 0), f"{t.tm_hour:02d}:{t.tm_min:02d}", font=self.font_big)
            db = draw.textbbox((0, 0), datestr, font=self.font_small)
            gap = 8  # pixels between clock and date

            # Keep a margin for the burn-in shift (up to SHIFT_MAX px right
            # and down): center the block in the area that stays free.
            area_w = 128 - SHIFT_MAX
            area_h = 64 - SHIFT_MAX
            block_h = (tb[3] - tb[1]) + gap + (db[3] - db[1])
            top = (area_h - block_h) // 2

            draw.text(
                ((area_w - (tb[2] - tb[0])) // 2 - tb[0] + self.shift_x, top - tb[1] + self.shift_y),
                timestr,
                fill="white",
                font=self.font_big,
            )

            date_top = top + (tb[3] - tb[1]) + gap
            draw.text(
                ((area_w - (db[2] - db[0])) // 2 - db[0] + self.shift_x, date_top - db[1] + self.shift_y),
                datestr,
                fill="white",
                font=self.font_small,
            )

    async def update(self) -> bool:
        """Redraw the screen. Called periodically from the main loop."""
        self._draw()
        return True


# ================= PLAYING SCREEN =================

class PlayingScreen(NowPlayingExtractHandler):
    """Now-playing screen: scrolling artist/title, volume bar and clock.

    Extends ``NowPlayingExtractHandler`` to receive title updates directly
    from the stream's own ICY metadata (rather than from MPD), since for an
    internet radio stream that's the only source of per-track info.
    """

    def __init__(
        self,
        display: sh1106,
        source_url: str,
        interval: float = 0.2,
        contrast: int = 180,
    ) -> None:
        """
        Args:
            display: The luma.oled device to draw on.
            source_url: Stream URL to read ICY metadata from.
            interval: Seconds between ticks, i.e. the animation rate of the
                scrolling text (metadata is read much less often, see
                ``METADATA_INTERVAL``).
            contrast: OLED contrast level (0-255) used while this screen
                is active.
        """
        super().__init__(
            source_url=source_url,
            interval=interval,
            coro=self._oled_title_coro,
            update_coro=self._oled_update_coro,
        )

        self.display = display
        self.contrast = contrast

        self._render_enabled = True

        self.font_big: FreeTypeFont = ImageFont.truetype(FONT_BIG, 15)
        self.font_clock: FreeTypeFont = ImageFont.truetype(FONT_BIG, 30)
        self.font_date: FreeTypeFont = ImageFont.truetype(FONT_SMALL, 11)

        # No real track info until a non-placeholder title arrives.
        self._current_title = ""
        self._no_data = True
        self._current_volume = get_volume_percent()
        self._last_volume_read = time.monotonic()

        self._scroll_offset = 0
        self._scroll_speed = 2
        self._scroll_gap = 20

        # Burn-in mitigation: shift the drawn content by a few pixels
        # periodically (see _draw below).
        self.shift_x = 0
        self.shift_y = 0
        self.last_shift = 0.0

        self._draw()

    def _set_contrast(self, val: int) -> None:
        """Apply the OLED contrast level; ignore errors from the display driver."""
        try:
            self.display.contrast(val)
        except Exception:
            pass

    @property
    def render_enabled(self) -> bool:
        """Whether this screen should actually draw to the display.

        Set to False by ``MoodeStateMachine`` while STATE_IDLE is active,
        so the background metadata polling can keep running without
        wasting time on OLED draws that wouldn't be shown anyway.
        """
        return self._render_enabled

    @render_enabled.setter
    def render_enabled(self, val: bool) -> None:
        self._render_enabled = val

    def split_artist_title(self, title: str) -> tuple[str, str]:
        """Split a combined "Artist - Title" ICY title into its two parts.

        Recognizes both an underscore-padded separator (``_-_``, sometimes
        used by radio automation software) and a plain " - " separator. If
        neither is found, the whole string is treated as the "artist" line
        with an empty title.

        Returns:
            A ``(artist, title)`` tuple, both stripped of surrounding
            whitespace.
        """
        if "_-_" in title:
            artist, song = title.split("_-_", 1)
            return artist.strip(), song.strip()
        if " - " in title:
            artist, song = title.split(" - ", 1)
            return artist.strip(), song.strip()
        return title.strip(), ""

    def _scroll_text(self, draw: ImageDraw, text: str, y: int, offset: int) -> None:
        """Draw one line of text, scrolling it horizontally if it's too wide.

        Text that fits within the 128px display width is drawn statically.
        Wider text scrolls continuously leftward, wrapping around with a
        gap (``self._scroll_gap``) so it reads as a seamless marquee.

        Args:
            draw: The active PIL ``ImageDraw`` context for this frame.
            text: The line of text to render.
            y: Vertical position (before burn-in shift is applied).
            offset: Current scroll offset in pixels, advanced by the
                caller (``_oled_update_coro``) once per animation tick.
        """
        bbox = self.font_big.getbbox(text)
        text_w = bbox[2] - bbox[0]

        # Apply the burn-in-avoidance offset.
        y_shifted = y + self.shift_y

        if text_w <= 128:
            draw.text((self.shift_x, y_shifted), text, fill="white", font=self.font_big)
            return

        # Text is wider than the display: scroll it, drawing a second copy
        # one full cycle ahead so the wrap-around looks continuous.
        total = text_w + self._scroll_gap
        x = -(offset % total) + self.shift_x
        draw.text((x, y_shifted), text, fill="white", font=self.font_big)
        draw.text((x + total, y_shifted), text, fill="white", font=self.font_big)

    def _top_line(self) -> str:
        """Text for the scrolling top line: "Title - Artist", or the station name.

        The station name is used while there is no real track info. The
        title comes first, then the artist, as the ICY text is "Artist -
        Title"; without a recognizable separator the text is shown as is.
        """
        if self._no_data:
            return STATION_NAME
        artist, song = self.split_artist_title(self._current_title)
        return f"{song} - {artist}" if song else artist

    def _draw_clock(self, draw: ImageDraw, top: int) -> None:
        """Draw the large time with the smaller date centered under it.

        Positions are derived from the glyph bounding boxes, so ``top`` is
        the first pixel row of the time's ink, regardless of font ascent.
        """
        gap = 3  # pixels between time and date
        timestr = time.strftime("%H:%M")
        datestr = time.strftime("%Y-%m-%d")

        tb = draw.textbbox((0, 0), timestr, font=self.font_clock)
        draw.text(
            ((128 - (tb[2] - tb[0])) // 2 - tb[0] + self.shift_x, top - tb[1] + self.shift_y),
            timestr,
            fill="white",
            font=self.font_clock,
        )

        db = draw.textbbox((0, 0), datestr, font=self.font_date)
        date_top = top + (tb[3] - tb[1]) + gap
        draw.text(
            ((128 - (db[2] - db[0])) // 2 - db[0] + self.shift_x, date_top - db[1] + self.shift_y),
            datestr,
            fill="white",
            font=self.font_date,
        )

    def _draw_volume_strip(self, draw: ImageDraw) -> None:
        """Draw the volume as a thin 3px bar along the bottom edge.

        Not affected by the burn-in shift: it stays on the last three rows
        of the display, and its length changes with the volume anyway.
        """
        bar_w = int(128 * self._current_volume / 100)
        if bar_w > 0:
            draw.rectangle((0, 61, bar_w - 1, 63), fill="white")

    def _draw(self) -> None:
        """Render the now-playing screen.

        Layout (top to bottom): scrolling "Title - Artist" line (the station
        name while there is no track info), large clock with the date under
        it, and a thin volume bar at the bottom.

        No-op if ``render_enabled`` is False (screen currently hidden
        because playback is idle).
        """
        if not self._render_enabled:
            return

        now = time.time()

        if now - self.last_shift >= 60:
            # Cycle the burn-in-avoidance offset through a small 4x4 pattern.
            self.shift_x = (self.shift_x + 1) % 4
            self.shift_y = (self.shift_y + 1) % 4
            self.last_shift = now

        self._set_contrast(self.contrast)

        with canvas(self.display) as draw:
            self._scroll_text(draw, self._top_line(), -3, self._scroll_offset)
            self._draw_clock(draw, 24)
            self._draw_volume_strip(draw)

    async def _oled_title_coro(self, title: Optional[str]) -> bool:
        """Handle a title update from the metadata poller.

        Called with the new title when it changed, or ``None`` if the
        title is unchanged since the last poll (see
        ``NowPlayingExtractHandler._loop``). Only redraws (and resets the
        scroll animation) when the title actually changed. Empty and
        placeholder titles (see ``is_placeholder_title``) switch the screen
        to the "no data" layout.
        """
        if title is None:
            return True

        if is_placeholder_title(title):
            if not self._no_data:
                self._no_data = True
                self._scroll_offset = 0
                self._draw()
        elif self._no_data or title != self._current_title:
            self._no_data = False
            self._current_title = title
            self._scroll_offset = 0
            self._draw()
        return True

    async def _oled_update_coro(self) -> bool:
        """Periodic tick: advance the scroll animation and refresh volume/clock.

        Called on every tick regardless of whether the title changed, so
        the scrolling text and the volume bar stay live even while the
        current track's title is unchanged. The volume is re-read at most
        every ``VOLUME_INTERVAL`` seconds (each read runs ``mpc``).
        """
        self._scroll_offset += self._scroll_speed
        now = time.monotonic()
        if now - self._last_volume_read >= VOLUME_INTERVAL:
            self._current_volume = get_volume_percent()
            self._last_volume_read = now
        self._draw()
        return True


# ================= STATE MACHINE =================

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
        self.last_poll = 0.0

        self.idle_screen = IdleScreen(self.display)
        self.playing_screen = PlayingScreen(self.display, STREAM_URL)
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
                    new_state = get_state()
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


# ================= MAIN =================

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


if __name__ == "__main__":
    main()