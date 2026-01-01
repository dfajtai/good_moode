#!/usr/bin/env python3
import time
import re
import requests
import asyncio
import subprocess
import threading
from pathlib import Path

from typing import Optional, Callable

from PIL import ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import sh1106
from luma.core.render import canvas

import gpiod
from gpiod.line import Direction, Bias, Value

# ================= CONFIG =================

I2C_ADDR = 0x3C

BASE_DIR = Path(__file__).resolve().parent
FONT_BIG = str(BASE_DIR / "fonts" / "NotoSans-Bold.ttf")
FONT_SMALL = str(BASE_DIR / "fonts" / "NotoSans-Medium.ttf")

STREAM_URL = "http://stream.radiomost.hu:8200/live.mp3"

MOODE_VOL_URL = "http://localhost/command/?cmd=get_volume"

STATE_IDLE = 0
STATE_PLAYING = 1

BUTTON_PIN = 22
GPIO_CHIP = "/dev/gpiochip0"

POLL_INTERVAL = 0.2


# ================= HELPERS =================

def extract_title(metadata: str) -> Optional[str]:
    m = re.search(r"StreamTitle='([^';]+)", metadata)
    if not m:
        return None
    return m.group(1).strip()


def get_volume_percent() -> int:
    try:
        out = subprocess.check_output(["mpc"], text=True, timeout=1)
        vol_match = re.search(r"volume: (\d+)%", out)
        if vol_match:
            return max(0, min(100, int(vol_match.group(1))))
    except Exception:
        pass
    
    # Fallback Moode API-ra
    try:
        r = requests.get(MOODE_VOL_URL, timeout=0.5)
        if r.ok:
            return max(0, min(100, int(r.json().get("volume", 50))))
    except Exception:
        pass
    return 50


def toggle_play_pause():
    # print("[DEBUG] MPC TOGGLE CALLED!")
    try:

        result = subprocess.run(
            ["mpc", "toggle"], 
            capture_output=True, 
            text=True, 
            timeout=2
        )
        # print(f"MPC STDOUT: {result.stdout.strip()}")
        # print(f"MPC STDERR: {result.stderr.strip()}")
        # print(f"MPC RETURN CODE: {result.returncode}")
        
        if result.returncode != 0:
            print("[ERROR] MPC toggle failed!")
            
    except subprocess.TimeoutExpired:
        print("[ERROR] MPC timeout!")
    except FileNotFoundError:
        print("[ERROR] mpc command not found!")
    except Exception as e:
        print(f"[ERROR] MPC exception: {e}")


def get_state():
    try:
        out = subprocess.check_output(["mpc", "status"], text=True, timeout=0.5)
        if "[playing]" in out:
            return STATE_PLAYING
    except Exception:
        pass
    return STATE_IDLE

# ================= METADATA HANDLER =================

class NowPlayingExtractHandler:
    """
    Periodically reads ICY metadata and calls callbacks.
    """

    def __init__(
        self,
        source_url: str,
        interval: float = 2.0,
        coro: Optional[Callable[[Optional[str]], asyncio.Future]] = None,
        update_coro: Optional[Callable[[], asyncio.Future]] = None,
    ):
        self.source_url = source_url
        self.interval = interval
        self.coro = coro
        self.update_coro = update_coro

        self._last_metadata = ""
        self.last_title = ""

        self._stop_event = asyncio.Event()
        self._session = None

        self._task: asyncio.Task | None = None

    def _read_metadata(self) -> str: 
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

            _ = r.raw.read(metaint)  # Skip to metadata
            lb = r.raw.read(1)
            if not lb:
                return ""

            meta_len = ord(lb) * 16
            if meta_len:
                meta = r.raw.read(meta_len)
                return meta.decode("iso-8859-2", "ignore")
        except Exception as e:
            print(f"[META ERROR] {e}")
        return ""

    async def _read_metadata_async(self) -> str:
        """Async wrapper that runs blocking I/O in a thread."""
        loop = asyncio.get_event_loop()
        try:
            # Az asyncio.to_thread (Python 3.9+) futtatja a szinkron függvényt egy szálban
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

    async def _loop(self):
        try:
            next_t = time.monotonic() + self.interval

            while not self._stop_event.is_set():
                meta = await self._read_metadata_async()  # <-- ASYNC HÍVÁS!
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

                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_t += self.interval

        except asyncio.CancelledError:
            pass

    async def _main(self):
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

    async def stop(self):
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
    """
    Simple polling-based GPIO button using libgpiod 2.x
    Works without root if permissions allow.
    """

    def __init__(self, line: int, on_press, debounce: float = 0.1):
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

        self._last_state = self.request.get_value(self.line)
        # print(f"[GPIO] initial state = {self._last_state}")

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                value = self.request.get_value(self.line)
                state = value.value if hasattr(value, 'value') else value
                
                if state != self._last_state:
                    if state == 0 and self._last_state == 1:
                        now = time.monotonic()
                        if now - self._last_press >= 0.05:
                            self._last_press = now
                            if self.on_press:
                                self.on_press()
                
                self._last_state = state
                
            except Exception:
                pass
            
            time.sleep(0.01)

    def stop(self):
        self._stop.set()
        try:
            self.request.release()
        except Exception:
            pass


# ================= IDLE SCREEN =================

class IdleScreen:
    def __init__(self, display, contrast = 50):
        self.display = display
        self.contrast = contrast

        self.font_big = ImageFont.truetype(FONT_BIG, 32)
        self.font_small = ImageFont.truetype(FONT_SMALL, 12)

        self.blink = True
        self.last_blink = 0

        self.shift_x = 0
        self.shift_y = 0
        self.last_shift = 0

        self._draw()

    def _set_contrast(self, val):
        try:
            self.display.contrast(val)
        except Exception:
            pass

    def _draw(self):
        now = time.time()
        self._set_contrast(self.contrast)

        if now - self.last_blink >= 1:
            self.blink = not self.blink
            self.last_blink = now

        if now - self.last_shift >= 60:
            self.shift_x = (self.shift_x + 1) % 4
            self.shift_y = (self.shift_y + 1) % 4
            self.last_shift = now

        t = time.localtime()
        colon = ":" if self.blink else " "
        timestr = f"{t.tm_hour:02d}{colon}{t.tm_min:02d}"
        datestr = time.strftime("%Y-%m-%d")

        with canvas(self.display) as draw:
            # Óra középre (128x64 kijelzőn)
            w = draw.textbbox((0, 0), timestr, font=self.font_big)[2]
            x = (128 - w) // 2
            y = 12  # Körülbelül a felső 1/3
            
            draw.text((x, y), timestr, fill="white", font=self.font_big)

            # Dátum középre, alatta kis térközzel
            dw = draw.textbbox((0, 0), datestr, font=self.font_small)[2]
            draw.text(
                ((128 - dw) // 2, y + 38),
                datestr,
                fill="white",
                font=self.font_small,
            )

    async def update(self):
        self._draw()
        return True


# ================= PLAYING SCREEN =================

class PlayingScreen(NowPlayingExtractHandler):
    def __init__(self, display, source_url: str, interval: float = 0.2, contrast = 180):
        super().__init__(
            source_url=source_url,
            interval=interval,
            coro=self._oled_title_coro,
            update_coro=self._oled_update_coro,
        )
        
        self.display = display
        self.contrast = contrast

        self._render_enabled = True

        self.font_big = ImageFont.truetype(FONT_BIG, 15)
        self.font_small = ImageFont.truetype(FONT_SMALL, 12)

        self._current_title = "Radio Most Kaposvár"
        self._current_volume = get_volume_percent()

        self._scroll_offset_artist = 0
        self._scroll_offset_song = 0
        self._scroll_speed = 2
        self._scroll_gap = 20
        
        # Burn-in elleni shift
        self.shift_x = 0
        self.shift_y = 0
        self.last_shift = 0

        self._draw()

    def _set_contrast(self, val):
        try:
            self.display.contrast(val)
        except Exception:
            pass

    @property
    def render_enabled(self):
        return self._render_enabled

    @render_enabled.setter
    def render_enabled(self, val):
        self._render_enabled = val


    def split_artist_title(self, title: str):
        if "_-_" in title:
            return (p.strip() for p in title.split("_-_", 1))
        if " - " in title:
            return (p.strip() for p in title.split(" - ", 1))
        return title.strip(), ""

    def _scroll_text(self, draw, text, y, offset):
        bbox = self.font_big.getbbox(text)
        text_w = bbox[2] - bbox[0]

        # Shift hozzáadása a burn-in elleni védelem miatt
        y_shifted = y + self.shift_y

        if text_w <= 128:
            draw.text((self.shift_x, y_shifted), text, fill="white", font=self.font_big)
            return

        total = text_w + self._scroll_gap
        x = -(offset % total) + self.shift_x
        draw.text((x, y_shifted), text, fill="white", font=self.font_big)
        draw.text((x + total, y_shifted), text, fill="white", font=self.font_big)

    def _draw(self):
        if not self._render_enabled:
            return
        
        now = time.time()
        
        # Burn-in elleni shift frissítése
        if now - self.last_shift >= 60:
            self.shift_x = (self.shift_x + 1) % 4
            self.shift_y = (self.shift_y + 1) % 4
            self.last_shift = now
        
        artist, song = self.split_artist_title(self._current_title)
        # print(f"[DRAW] Artist: '{artist}' Song: '{song}'")  # DEBUG
        
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
            draw.rectangle((30 + self.shift_x, 40 + self.shift_y, 30 + bar_w + self.shift_x, 43 + self.shift_y), fill="white")

            draw.text((self.shift_x, 50 + self.shift_y), now_str, fill="white", font=self.font_small)

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


# ================= STATE MACHINE =================

class MoodeStateMachine:
    def __init__(self):
        self.serial = i2c(port=1, address=I2C_ADDR)
        self.display = sh1106(self.serial, width=128, height=64)

        self.state = None
        self.last_poll = 0

        self.idle_screen = IdleScreen(self.display)

        self.playing_screen  = PlayingScreen(self.display, STREAM_URL)
        self.play_task: asyncio.Task | None = None

        self.CONTRAST_IDLE = 40
        self.CONTRAST_PLAYING = 180

        self.button = GPIOButton(BUTTON_PIN, toggle_play_pause)

        self.state = get_state()
        self._apply_state(self.state)


    def _apply_state(self, new_state):
        self.playing_screen.render_enabled = (new_state == STATE_PLAYING)

        if new_state == STATE_IDLE:
            # STATE_IDLE-re váltunk, szükség a play_task rendszeres leállítására
            if self.play_task:
                self.play_task.cancel()
                self.play_task = None
        
        self.state = new_state

    async def run(self):
        try:
            while True:
                now = time.monotonic()

                if now - self.last_poll > POLL_INTERVAL:
                    new_state = get_state()
                    if new_state != self.state:
                        # STATE_IDLE-re váltunk, gondoskodunk az async cleanup-ról
                        if new_state == STATE_IDLE and self.play_task:
                            self.play_task.cancel()
                            try:
                                await asyncio.wait_for(self.play_task, timeout=0.5)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                            self.play_task = None
                        
                        self._apply_state(new_state)

                    # Indítsd el a playing screen-t, ha STATE_PLAYING-ben vagyunk
                    if self.state == STATE_PLAYING and self.play_task is None:
                        self.play_task = asyncio.create_task(
                            self.playing_screen._main()
                        )

                    self.last_poll = now

                # Frissítsd az aktuális screent
                if self.state == STATE_IDLE:
                    await self.idle_screen.update()
                # STATE_PLAYING-ben a playing_screen._main() task gondoskodik a frissítésről

                await asyncio.sleep(0.2)

        finally:
            if self.play_task:
                self.play_task.cancel()

            if self.button:
                self.button.stop()



# ================= MAIN =================

if __name__ == "__main__":
    sm = MoodeStateMachine()
    try:
        asyncio.run(sm.run())
    except KeyboardInterrupt:
        pass
