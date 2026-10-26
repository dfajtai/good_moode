"""The radio stream's ICY metadata: reading it, and judging whether a title is real."""

import asyncio
import re
import time
from typing import Callable, Optional

import requests

from .config import METADATA_INTERVAL, PLACEHOLDER_TITLE_PREFIX
from .log import log


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
