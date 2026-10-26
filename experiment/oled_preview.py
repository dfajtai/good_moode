#!/usr/bin/env python3
"""Render the OLED screens of the good_moode package to PNG/GIF.

Debug preview without any hardware: the real ``PlayingScreen`` / ``IdleScreen``
classes are loaded and draw onto an in-memory 128x64 canvas instead of the
SH1106, so the preview always matches what the Pi would draw. Only needs
Pillow; missing hardware libraries (luma, requests) are stubbed.

Examples (run from the repo root):

    python experiment/oled_preview.py --title "Adele - Hello" --volume 35
    python experiment/oled_preview.py                       # no track info
    python experiment/oled_preview.py --title "Very long artist - Very long title" --frames 40 -o scroll.gif
    python experiment/oled_preview.py --title "Adele - Hello" --shift 3 --now "2026-10-25 15:07"
    python experiment/oled_preview.py --screen idle --shift 3 --no-colon
    python experiment/oled_preview.py --export docs/oled --now "2026-10-25 15:07"   # all cases as files
    python experiment/oled_preview.py --sheet -o overview.png   # standard set of cases
"""

import argparse
import asyncio
import importlib
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LABEL_FONT = ROOT / "good_moode" / "fonts" / "NotoSans-Medium.ttf"

PLACEHOLDER = "Telefon-SMS-Viber_-_06703232355"
LONG_TITLE = "Tisztelet_-_Hosszú előadó neve és nagyon hosszú dal címe"

# Hardware-only names the screens import; stubbed when the real
# library is not installed (i.e. on a normal PC).
_STUB_ATTRS = {
    "requests": {"Session": object},
    "luma": {},
    "luma.core": {},
    "luma.core.interface": {},
    "luma.core.interface.serial": {"i2c": object},
    "luma.oled": {},
    "luma.oled.device": {"sh1106": object},
    "luma.core.render": {"canvas": None},
}


def _stub_missing_modules() -> None:
    for name, attrs in _STUB_ATTRS.items():
        try:
            importlib.import_module(name)
        except Exception:
            stub = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(stub, key, value)
            sys.modules[name] = stub


class _FrozenTime:
    """Stand-in for the ``time`` module with a fixed wall-clock time."""

    def __init__(self, struct: time.struct_time) -> None:
        self._struct = struct

    def __getattr__(self, name: str):
        return getattr(time, name)

    def strftime(self, fmt: str, t: Optional[time.struct_time] = None) -> str:
        return time.strftime(fmt, t or self._struct)

    def localtime(self, secs: Optional[float] = None) -> time.struct_time:
        return self._struct if secs is None else time.localtime(secs)

    def time(self) -> float:
        return time.mktime(self._struct)


class _Device:
    """Fake display: the screens only ever call ``contrast()`` on it."""

    def contrast(self, value: int) -> None:
        pass


class Preview:
    """Loads the real screen classes and renders them to PIL images."""

    def __init__(self, now: Optional[str] = None) -> None:
        _stub_missing_modules()
        sys.path.insert(0, str(ROOT))
        from good_moode import screens  # cheap import: no gpiod / state machine needed

        self.m = screens

        self.volume = 70
        self.frame: Optional[Image.Image] = None
        self.m.canvas = self._canvas
        self.m.get_volume_percent = lambda: self.volume
        if now:
            self.m.time = _FrozenTime(time.strptime(now, "%Y-%m-%d %H:%M"))

    @contextmanager
    def _canvas(self, device):
        im = Image.new("1", (128, 64))
        try:
            yield ImageDraw.Draw(im)
        finally:
            self.frame = im

    def playing_screen(self, title: str, volume: int = 70, shift: int = 0):
        """Create a PlayingScreen as if ``title`` had just arrived from the stream."""
        self.volume = volume
        screen = self.m.PlayingScreen(_Device(), "preview")
        asyncio.run(screen._oled_title_coro(title))
        screen.shift_x = screen.shift_y = shift
        screen.last_shift = float("inf")  # keep the shift we set
        return screen

    def snap(self, screen) -> Image.Image:
        screen._draw()
        return self.frame.copy()

    def playing(self, title: str, volume: int = 70, shift: int = 0, advance: int = 0) -> Image.Image:
        """One frame of the playing screen, ``advance`` scroll ticks after the title arrived."""
        screen = self.playing_screen(title, volume, shift)
        screen._scroll_offset = advance * screen._scroll_speed
        return self.snap(screen)

    def scroll_frames(self, title: str, count: int, volume: int = 70, shift: int = 0) -> list[Image.Image]:
        """``count`` consecutive frames, advancing the scroll like the real update tick."""
        screen = self.playing_screen(title, volume, shift)
        frames = []
        for _ in range(count):
            frames.append(self.snap(screen))
            screen._scroll_offset += screen._scroll_speed
        return frames

    def idle(self, shift: int = 0, colon: bool = True) -> Image.Image:
        """The idle (clock) screen; ``colon=False`` is the blink's "off" phase."""
        screen = self.m.IdleScreen(_Device())
        screen.shift_x = screen.shift_y = shift
        screen.blink = colon
        screen.last_blink = screen.last_shift = float("inf")  # keep what we set
        return self.snap(screen)


def to_rgb(im: Image.Image, scale: int = 6, grid: bool = True) -> Image.Image:
    """Enlarge the 1-bit frame to an OLED-looking RGB image (pixel grid optional)."""
    out = Image.new("RGB", (im.width * scale, im.height * scale), (0, 0, 0))
    draw = ImageDraw.Draw(out)
    px = im.load()
    inset = 1 if grid and scale >= 3 else 0
    for y in range(im.height):
        for x in range(im.width):
            if px[x, y]:
                draw.rectangle(
                    (x * scale, y * scale, (x + 1) * scale - 1 - inset, (y + 1) * scale - 1 - inset),
                    fill=(210, 235, 255),
                )
    return out


def contact_sheet(items: list[tuple[str, Image.Image]], cols: int = 2, pad: int = 24) -> Image.Image:
    """Arrange labelled images in a grid."""
    font = ImageFont.truetype(str(LABEL_FONT), 20)
    w, h = items[0][1].size
    rows = -(-len(items) // cols)
    sheet = Image.new("RGB", (cols * w + (cols + 1) * pad, rows * (h + 40) + pad), (30, 30, 34))
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(items):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + 40)
        draw.text((x, y), label, fill=(220, 220, 220), font=font)
        sheet.paste(im, (x, y + 28))
    return sheet


def standard_cases(p: Preview) -> list[tuple[str, str, Image.Image]]:
    """(file name, label, frame) for the standard set of cases."""
    return [
        ("no-data", "No data (placeholder filtered), volume 70%", p.playing(PLACEHOLDER)),
        ("with-data", "With data: Adele - Hello, volume 35%", p.playing("Adele - Hello", volume=35)),
        ("long-title-1", "Long title, scroll tick 0", p.playing(LONG_TITLE)),
        ("long-title-2", "Long title, scroll tick 15", p.playing(LONG_TITLE, advance=15)),
        ("shift-with-data", "Worst burn-in shift (+3,+3), with data", p.playing("Adele - Hello", shift=3)),
        ("shift-no-data", "Worst burn-in shift (+3,+3), no data, volume 100%", p.playing(PLACEHOLDER, volume=100, shift=3)),
        ("idle", "Idle: clock, colon on", p.idle()),
        ("idle-colon-off", "Idle: colon off (blink)", p.idle(colon=False)),
        ("idle-shift", "Idle: worst burn-in shift (+3,+3)", p.idle(shift=3)),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--screen", choices=("playing", "idle"), default="playing")
    ap.add_argument("--title", default="", help="stream title; empty/placeholder = no track info")
    ap.add_argument("--volume", type=int, default=70, help="0-100")
    ap.add_argument("--shift", type=int, default=0, choices=range(4), help="burn-in shift, 0-3 px")
    ap.add_argument("--advance", type=int, default=0, help="scroll ticks after the title arrived")
    ap.add_argument("--frames", type=int, default=1, help=">1: animated GIF of the scrolling")
    ap.add_argument("--now", help='freeze the clock, e.g. "2026-10-25 15:07"')
    ap.add_argument("--scale", type=int, default=6)
    ap.add_argument("--no-grid", action="store_true", help="no dark pixel grid")
    ap.add_argument("--no-colon", action="store_true", help="idle screen: blink's off phase")
    ap.add_argument("--sheet", action="store_true", help="render the standard set of cases")
    ap.add_argument("--export", type=Path, metavar="DIR", help="write every standard case + overview.png to DIR")
    ap.add_argument("-o", "--out", type=Path, default=Path("oled_preview.png"))
    args = ap.parse_args()

    p = Preview(args.now)
    rgb = lambda im: to_rgb(im, args.scale, not args.no_grid)

    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        cases = standard_cases(p)
        for slug, _, im in cases:
            rgb(im).save(args.export / f"{slug}.png")
        contact_sheet([(label, rgb(im)) for _, label, im in cases]).save(args.export / "overview.png")
        args.out = args.export
    elif args.sheet:
        contact_sheet([(label, rgb(im)) for _, label, im in standard_cases(p)]).save(args.out)
    elif args.screen == "idle":
        rgb(p.idle(args.shift, not args.no_colon)).save(args.out)
    elif args.frames > 1:
        frames = [rgb(f) for f in p.scroll_frames(args.title, args.frames, args.volume, args.shift)]
        out = args.out if args.out.suffix == ".gif" else args.out.with_suffix(".gif")
        frames[0].save(out, save_all=True, append_images=frames[1:], duration=200, loop=0)
        args.out = out
    else:
        rgb(p.playing(args.title, args.volume, args.shift, args.advance)).save(args.out)

    print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
