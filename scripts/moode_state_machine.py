#!/usr/bin/env python3
"""OLED status display + physical play/pause button for moOde audio player.

Runs as a standalone process alongside moOde/MPD on a Raspberry Pi. It polls
MPD's playback state and drives a small SH1106 OLED display through two
screens:

- IdleScreen:    a simple clock, shown while nothing is playing.
- PlayingScreen: artist/title (scrolling), volume bar and clock, shown while
                  MPD is playing. Track info comes from the radio stream's
                  ICY metadata, not from MPD directly.

A single GPIO push-button (BCM pin ``BUTTON_PIN``) toggles play/pause via
``mpc toggle``, polled from a background thread (see ``GPIOButton``).

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

# Playback states used throughout this module. Plain ints (not an Enum) to
# keep comparisons against MPD's own status output cheap and simple.
STATE_IDLE = 0
STATE_PLAYING = 1

BUTTON_PIN = 22  # BCM numbering, i.e. physical pin 15 on the 40-pin header
GPIO_CHIP = "/dev/gpiochip0"

POLL_INTERVAL = 0.2  # seconds between MPD status polls in the main loop


# ================= HELPERS =================

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
            print("[ERROR] MPC toggle failed!")

    except subprocess.TimeoutExpired:
        print("[ERROR] MPC timeout!")
    except FileNotFoundError:
        print("[ERROR] mpc command not found!")
    except Exception as e:
        print(f"[ERROR] MPC exception: {e}")


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
                    print("[STARTUP] Playback already active, nothing to do", flush=True)
                else:
                    print("[STARTUP] Queue present but not playing, starting playback", flush=True)
                    subprocess.run(["mpc", "play"], timeout=2)
                return

            # Queue is empty: load the radio stream, same as a manual
            # "Play" from the web UI would do.
            print(
                f"[STARTUP] Queue is empty (attempt {attempt}/{retries}), "
                f"loading stream and starting playback",
                flush=True,
            )
            subprocess.run(["mpc", "clear"], timeout=2)
            subprocess.run(["mpc", "add", STREAM_URL], timeout=2)
            subprocess.run(["mpc", "play"], timeout=2)
            return

        except Exception as exc:
            print(f"[STARTUP ERROR] {exc}", flush=True)

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
    ) -> None:
        """
        Args:
            source_url: URL of the audio stream to read ICY metadata from.
            interval: Seconds between metadata polls.
            coro: Async callback invoked with the new title (or ``None`` if
                unchanged) on every poll.
            update_coro: Async callback invoked on every poll regardless of
                whether the title changed.
        """
        self.source_url = source_url
        self.interval = interval
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
            print(f"[META ERROR] {e}")
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
            print("[META TIMEOUT] Metadata read took too long")
            return ""
        except Exception as e:
            print(f"[META ERROR] {e}")
            return ""

    async def _loop(self) -> None:
        """Main polling loop: reads metadata every ``self.interval`` seconds.

        Uses a fixed schedule (``next_t`` accumulates by ``interval`` each
        iteration) rather than a plain ``sleep(interval)`` after each read,
        so the polling cadence doesn't drift by the time the read itself
        takes.
        """
        try:
            next_t = time.monotonic() + self.interval

            while not self._stop_event.is_set():
                meta = await self._read_metadata_async()
                if meta:
                    if meta != self._last_metadata:
                        # Title (or other ICY field) changed since last poll.
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
        print(f"[GPIO] initial state = {self._last_state}", flush=True)

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
                    print(f"[GPIO] state change: {self._last_state} -> {state}", flush=True)

                    # Falling edge (1 -> 0) = button press, since the line
                    # idles high (pull-up) and is pulled low on press.
                    if state == 0 and self._last_state == 1:
                        now = time.monotonic()
                        if now - self._last_press >= self.debounce:
                            self._last_press = now
                            print("[GPIO] PRESS detected, calling on_press()", flush=True)
                            if self.on_press:
                                self.on_press()

                self._last_state = state

            except Exception as exc:
                print(f"[GPIO ERROR] {exc}", flush=True)

            time.sleep(POLL_SLEEP)

    def stop(self) -> None:
        """Stop the polling thread and release the GPIO line request."""
        self._stop.set()
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

        self.font_big: FreeTypeFont = ImageFont.truetype(FONT_BIG, 32)
        self.font_small: FreeTypeFont = ImageFont.truetype(FONT_SMALL, 12)

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
            # Clock, horizontally centered (display is 128px wide)
            w = draw.textbbox((0, 0), timestr, font=self.font_big)[2]
            x = (128 - w) // 2
            y = 12  # roughly the top third of the 64px-tall display

            draw.text((x, y), timestr, fill="white", font=self.font_big)

            # Date, centered below the clock
            dw = draw.textbbox((0, 0), datestr, font=self.font_small)[2]
            draw.text(
                ((128 - dw) // 2, y + 38),
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
            interval: Seconds between metadata polls (also the effective
                animation tick rate for the scrolling text).
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
        self.font_small: FreeTypeFont = ImageFont.truetype(FONT_SMALL, 12)

        self._current_title = "Radio Most Kaposvár"
        self._current_volume = get_volume_percent()

        self._scroll_offset_artist = 0
        self._scroll_offset_song = 0
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

    def _draw(self) -> None:
        """Render the now-playing screen: artist/title, volume bar, clock.

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

        artist, song = self.split_artist_title(self._current_title)

        now_str = time.strftime("%Y-%m-%d %H:%M")
        vol = self._current_volume

        self._set_contrast(self.contrast)

        with canvas(self.display) as draw:
            if artist:
                self._scroll_text(draw, artist, 0, self._scroll_offset_artist)

            if song:
                self._scroll_text(draw, song, 18, self._scroll_offset_song)

            draw.text((self.shift_x, 36 + self.shift_y), "VOL", fill="white", font=self.font_small)

            bar_w = int(90 * vol / 100)
            draw.rectangle(
                (30 + self.shift_x, 40 + self.shift_y, 30 + bar_w + self.shift_x, 43 + self.shift_y),
                fill="white",
            )

            draw.text((self.shift_x, 50 + self.shift_y), now_str, fill="white", font=self.font_small)

    async def _oled_title_coro(self, title: Optional[str]) -> bool:
        """Handle a title update from the metadata poller.

        Called with the new title when it changed, or ``None`` if the
        title is unchanged since the last poll (see
        ``NowPlayingExtractHandler._loop``). Only redraws (and resets the
        scroll animation) when the title actually changed.
        """
        if title and title != self._current_title:
            self._current_title = title
            self._scroll_offset_artist = 0
            self._scroll_offset_song = 0
            self._draw()
        return True

    async def _oled_update_coro(self) -> bool:
        """Periodic tick: advance the scroll animation and refresh volume/clock.

        Called on every metadata poll cycle regardless of whether the
        title changed, so the scrolling text and volume bar stay live even
        while the current track's title is unchanged.
        """
        self._scroll_offset_artist += self._scroll_speed
        self._scroll_offset_song += self._scroll_speed
        self._current_volume = get_volume_percent()
        self._draw()
        return True


# ================= STATE MACHINE =================

class MoodeStateMachine:
    """Top-level orchestrator: owns the display, button and both screens.

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

        # Establish the initial screen/state based on what MPD is actually
        # doing right now (rather than defaulting to idle and waiting for
        # the first poll in run()).
        self.state = get_state()
        self._apply_state(self.state)

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
        always cancels any in-flight metadata task and stops the GPIO
        button's polling thread, so the process can shut down cleanly.
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