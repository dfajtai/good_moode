#!/home/fajtai/scripts/.venv/bin/python
import requests
import asyncio
import time
import re
from typing import Optional, Callable

from PIL import ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas


FONT_PATH = "./fonts/NotoSans-Medium.ttf"
STREAM_URL = "http://stream.radiomost.hu:8200/live.mp3"
MOODE_URL  = "http://localhost/command/?cmd=get_volume"


# ------------------ segédfüggvények ------------------

def extract_title(metadata: str) -> Optional[str]:
    m = re.search(r"StreamTitle='([^';]+)", metadata)
    if not m:
        return None
    return m.group(1).strip()


def get_volume_percent() -> int:
    try:
        r = requests.get(MOODE_URL, timeout=0.5)
        if r.ok:
            data = r.json()
            return max(0, min(100, int(data.get("volume", 50))))
    except Exception:
        pass
    return 50


# ------------------ metadata handler ------------------

class NowPlayingExtractHandler:
    def __init__(
        self,
        source_url: str,
        interval: float = 1.0,
        coro: Optional[Callable[[Optional[str]], asyncio.Future]] = None,
        update_coro: Optional[Callable[[], asyncio.Future]] = None
    ):
        self.source_url = source_url
        self.interval = interval
        self.coro = coro
        self.update_coro = update_coro

        self._last_metadata = ""
        self.last_title = ""

        self._stop_event = asyncio.Event()
        self._session = None

    def _read_metadata(self) -> str:
        try:
            r = self._session.get(self.source_url, stream=True, timeout=10)
            r.raise_for_status()

            metaint = int(r.headers.get("icy-metaint", "0"))
            if not metaint:
                return ""

            _ = r.raw.read(metaint)
            lb = r.raw.read(1)
            if not lb:
                return ""

            meta_len = ord(lb) * 16
            if meta_len:
                meta = r.raw.read(meta_len)
                return meta.decode("iso-8859-2", "ignore")
        except Exception:
            pass
        return ""

    async def _loop(self):
        next_t = time.monotonic() + self.interval

        while not self._stop_event.is_set():
            try:
                meta = self._read_metadata()
                if meta:
                    if meta != self._last_metadata:
                        self._last_metadata = meta
                        self.last_title = extract_title(meta) or ""
                        if self.coro:
                            await self.coro(self.last_title)
                    else:
                        if self.coro:
                            await self.coro(None)

                if self.update_coro:
                    await self.update_coro()

            finally:
                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_t += self.interval

    async def _main(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Icy-MetaData": "1",
            "User-Agent": "RadioMetadataExtractor/1.0",
        })
        try:
            await self._loop()
        finally:
            self._session.close()

    def start(self):
        if self._stop_event.is_set():
            self._stop_event.clear()
        asyncio.run(self._main())


# ------------------ OLED + UI ------------------

class OLEDNowPlayingWithVolumeHandler(NowPlayingExtractHandler):

    def __init__(self, source_url: str, interval: float = 0.2):
        super().__init__(
            source_url=source_url,
            interval=interval,
            coro=self._oled_title_coro,
            update_coro=self._oled_update_coro,
        )

        self.serial = i2c(port=1, address=0x3C)
        self.device = sh1106(self.serial, width=128, height=64)

        # nagyobb betűk
        self.font_big = ImageFont.truetype(FONT_PATH, 15)
        self.font_small = ImageFont.truetype(FONT_PATH, 12)

        self._current_title = "Radio Most Kaposvár"
        self._current_volume = get_volume_percent()

        self._scroll_offset_artist = 0
        self._scroll_offset_song = 0
        self._scroll_speed = 1
        self._scroll_gap = 20

        self._draw()

    def split_artist_title(self, title: str):
        if "_-_" in title:
            a, b = (p.strip() for p in title.split("_-_", 1))
        elif " - " in title:
            a, b = (p.strip() for p in title.split(" - ", 1))
        else:
            return title.strip(), ""
        return a, b

    def _scroll_text(self, draw, text, y, offset):
        bbox = self.font_big.getbbox(text)
        text_w = bbox[2] - bbox[0]

        if text_w <= 128:
            draw.text((0, y), text, fill="white", font=self.font_big)
            return

        total = text_w + self._scroll_gap
        x = -(offset % total)
        draw.text((x, y), text, fill="white", font=self.font_big)
        draw.text((x + total, y), text, fill="white", font=self.font_big)

    def _draw(self):
        artist, song = self.split_artist_title(self._current_title)
        now = time.strftime("%Y-%m-%d %H:%M")
        vol = self._current_volume

        with canvas(self.device) as draw:

            # 1. sor – előadó (scroll)
            if artist:
                self._scroll_text(draw, artist, 0, self._scroll_offset_artist)

            # 2. sor – szám címe (scroll)
            if song:
                self._scroll_text(draw, song, 18, self._scroll_offset_song)

            # 3. sor – VOL + kisebb sáv
            draw.text((0, 36), "VOL", fill="white", font=self.font_small)

            bar_w = int(70 * vol / 100)
            draw.rectangle((30, 40, 30 + bar_w, 43), fill="white")

            # 4. sor – dátum + idő
            draw.text((0, 50), now, fill="white", font=self.font_small)


    async def _oled_title_coro(self, title: Optional[str]) -> bool:
        if title and title != self._current_title:
            self._current_title = title
            self._scroll_offset_artist = 0
            self._scroll_offset_song = 0
            self._draw()
        return True

    async def _oled_update_coro(self) -> bool:
        self._scroll_offset_artist += self._scroll_speed
        self._scroll_offset_song += self._scroll_speed
        self._current_volume = get_volume_percent()
        self._draw()
        return True


# ------------------ futtatás ------------------

if __name__ == "__main__":
    h = OLEDNowPlayingWithVolumeHandler(STREAM_URL, interval=0.2)
    try:
        h.start()
    except KeyboardInterrupt:
        pass
