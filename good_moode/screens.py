"""The two OLED screens: IdleScreen (clock) and PlayingScreen (now playing)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from luma.core.render import canvas
from PIL import ImageFont
from PIL.ImageDraw import ImageDraw
from PIL.ImageFont import FreeTypeFont

from .config import FONT_BIG, FONT_SMALL, SHIFT_MAX, STATION_NAME, VOLUME_INTERVAL
from .mpd import get_volume_percent
from .radio import NowPlayingExtractHandler, is_placeholder_title

if TYPE_CHECKING:  # only needed for the annotations; the display is passed in
    from luma.oled.device import sh1106


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
