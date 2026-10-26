#!/usr/bin/env python3
"""Playback diagnostics logger for the moOde player (Raspberry Pi).

Meant to find out why playback sometimes stops, e.g. at the first track change
after boot. It only observes (it never touches MPD or the GPIO) and writes a
log that can be downloaded later:

    ~/scripts/playback_diag.log      (rotated: 2 MB x 5 files)

What is logged:

- STATE    every MPD state change (playing / paused / stopped), with the trigger
           (an ``mpc idle`` event or the periodic poll). Leaving "playing" is a
           WARN and is followed by a CONTEXT block: the tail of the MPD log, the
           moOde log, the kernel log, the Icecast source info and the Pi's health.
- EVENT    every MPD change in the player / playlist / options / mixer
           subsystems, with the raw ``mpc status`` lines (state, position,
           repeat / single / consume, error text).
- SOURCE   the radio's Icecast mount: ``stream_start`` and title. A changed
           ``stream_start`` means the station's encoder reconnected, which drops
           every listener, MPD included (WARN "SOURCE RECONNECT").
- HEALTH   temperature, throttling / under-voltage flags, load, free memory and
           Wi-Fi signal, every minute and whenever the throttling flags change.

Usage:

    python -m good_moode.playback_logger       # run in the foreground (from ~/scripts)
    setsid nohup python -m good_moode.playback_logger >/dev/null 2>&1 &   # detached from the terminal
    python -m good_moode.playback_logger --summary FILE   # state changes and what preceded them

Only needs ``requests``; external commands used when present: mpc, vcgencmd,
iw, journalctl.
"""

import argparse
import logging
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import requests

from .config import STREAM_URL

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR.parent / "playback_diag.log"  # next to the package, i.e. ~/scripts

ICECAST_STATUS_URL = "http://stream.radiomost.hu:8200/status-json.xsl"
MOUNT = STREAM_URL.rsplit("/", 1)[1]  # the mount that is playing, e.g. "mobil.aac"
MPD_LOG_FILES = ("/var/log/mpd/mpd.log", "/var/log/mpd.log")
MOODE_LOG = "/var/log/moode.log"

POLL_INTERVAL = 3.0  # seconds between state polls (catches changes ``mpc idle`` misses)
SOURCE_INTERVAL = 20.0  # seconds between Icecast status checks
HEALTH_INTERVAL = 60.0  # seconds between HEALTH lines

THROTTLE_BITS = {
    0x1: "undervoltage_now", 0x2: "freq_capped_now", 0x4: "throttled_now", 0x8: "temp_limit_now",
    0x10000: "undervoltage_occurred", 0x20000: "freq_capped_occurred",
    0x40000: "throttled_occurred", 0x80000: "temp_limit_occurred",
}

log = logging.getLogger("playback")


# ================= HELPERS =================

def run(cmd: list[str], timeout: float = 3.0) -> tuple[int, str]:
    """Run a command; never raises. Returns ``(returncode, stdout+stderr)``."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return -1, f"<{type(exc).__name__}: {exc}>"


def tail(path: str, lines: int) -> list[str]:
    """Last ``lines`` lines of a text file (empty list if unreadable)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip() for ln in deque(f, maxlen=lines)]
    except OSError:
        return []


def mpd_snapshot() -> tuple[str, str]:
    """Current MPD state and the raw ``mpc status`` lines joined with " | "."""
    rc, out = run(["mpc", "status"])
    raw = " | ".join(ln.strip() for ln in out.splitlines() if ln.strip())
    if rc != 0:
        return "unavailable", raw
    if "[playing]" in out:
        return "playing", raw
    if "[paused]" in out:
        return "paused", raw
    return "stopped", raw


def decode_throttled(value: int) -> str:
    flags = [name for bit, name in THROTTLE_BITS.items() if value & bit]
    return ",".join(flags) if flags else "ok"


def health() -> tuple[str, int]:
    """One-line health summary and the raw throttling bitmask."""
    parts = []
    throttled = 0

    rc, out = run(["vcgencmd", "measure_temp"])
    if rc == 0:
        parts.append(f"temp={out.replace('temp=', '')}")
    rc, out = run(["vcgencmd", "get_throttled"])
    if rc == 0 and "=" in out:
        try:
            throttled = int(out.split("=")[1], 16)
        except ValueError:
            pass
    parts.append(f"throttled={throttled:#x}({decode_throttled(throttled)})")

    try:
        parts.append("load=" + ",".join(Path("/proc/loadavg").read_text().split()[:3]))
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                parts.append(f"mem_avail={int(ln.split()[1]) // 1024}MB")
        parts.append(f"uptime={int(float(Path('/proc/uptime').read_text().split()[0]))}s")
    except OSError:
        pass

    rc, out = run(["iw", "dev", "wlan0", "link"])
    if rc == 0:
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("signal:"):
                parts.append("wifi=" + ln.split(":", 1)[1].strip())
            elif ln.startswith("tx bitrate:"):
                parts.append("tx=" + ln.split(":", 1)[1].strip().split(" MBit")[0] + "Mbit/s")
    return " ".join(parts), throttled


def icecast_source() -> Optional[dict]:
    """The radio's mount info from Icecast, or None if it can't be fetched."""
    try:
        r = requests.get(ICECAST_STATUS_URL, timeout=4)
        r.raise_for_status()
        sources = r.json()["icestats"].get("source", [])
        if isinstance(sources, dict):
            sources = [sources]
        for s in sources:
            if s.get("listenurl", "").endswith("/" + MOUNT):
                return {
                    "stream_start": s.get("stream_start_iso8601", "?"),
                    "listeners": s.get("listeners", "?"),
                    "title": s.get("title", ""),
                }
    except Exception as exc:
        log.info("SOURCE ERROR %s", exc)
    return None


# ================= TRACKER =================

class Tracker:
    """Remembers the last MPD state and logs every change with context."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: Optional[str] = None
        self.stream_start: Optional[str] = None
        self.title: Optional[str] = None
        self.throttled = 0

    def update(self, trigger: str) -> None:
        """Take a snapshot; log it if the state changed. Thread-safe."""
        with self._lock:
            state, raw = mpd_snapshot()
            if state == self.state:
                return
            previous, self.state = self.state, state
            if previous is None:
                log.info("STATE initial %s | %s", state, raw)
            elif previous == "playing":
                log.warning("STATE playing -> %s (trigger: %s) | %s", state, trigger, raw)
                self._context()
            else:
                log.info("STATE %s -> %s (trigger: %s) | %s", previous, state, trigger, raw)

    def _context(self) -> None:
        """Dump everything that could explain why playback just left "playing"."""
        log.warning("CONTEXT begin")
        summary, throttled = health()
        log.warning("CONTEXT health: %s", summary)
        src = icecast_source()
        if src:
            log.warning("CONTEXT source: stream_start=%s listeners=%s title=%r",
                        src["stream_start"], src["listeners"], src["title"])
        mpd_lines = []
        for path in MPD_LOG_FILES:
            mpd_lines = tail(path, 15)
            if mpd_lines:
                break
        if not mpd_lines:
            _, out = run(["journalctl", "-u", "mpd", "-n", "15", "--no-pager"], timeout=5)
            mpd_lines = out.splitlines()
        for ln in mpd_lines:
            log.warning("CONTEXT mpd: %s", ln)
        for ln in tail(MOODE_LOG, 10):
            log.warning("CONTEXT moode: %s", ln)
        _, kernel = run(["journalctl", "-k", "-n", "10", "--no-pager"], timeout=5)
        for ln in kernel.splitlines():
            log.warning("CONTEXT kernel: %s", ln)
        log.warning("CONTEXT end")

    def check_source(self) -> None:
        src = icecast_source()
        if not src:
            return
        if self.stream_start is None:
            log.info("SOURCE %s stream_start=%s listeners=%s title=%r",
                     MOUNT, src["stream_start"], src["listeners"], src["title"])
        elif src["stream_start"] != self.stream_start:
            log.warning("SOURCE RECONNECT stream_start %s -> %s (the encoder restarted; every "
                        "listener, MPD included, was disconnected) listeners=%s",
                        self.stream_start, src["stream_start"], src["listeners"])
        elif src["title"] != self.title:
            log.info("SOURCE title=%r", src["title"])
        self.stream_start, self.title = src["stream_start"], src["title"]

    def log_health(self, force: bool = False) -> None:
        summary, throttled = health()
        if force or throttled != self.throttled:
            level = logging.WARNING if throttled & 0xF else logging.INFO
            log.log(level, "HEALTH %s", summary)
        self.throttled = throttled


# ================= WORKERS =================

def idle_worker(tracker: Tracker) -> None:
    """Block on ``mpc idle`` and log every player/playlist/options/mixer change."""
    failed = False
    while True:
        rc, out = run(["mpc", "idle", "player", "playlist", "options", "mixer"], timeout=3600)
        if rc != 0:
            if not failed:
                log.warning("MPD idle failed (%s); retrying", out)
                failed = True
            time.sleep(2)
            continue
        failed = False
        subsystems = ",".join(out.split())
        _, raw = mpd_snapshot()
        log.info("EVENT %-9s %s", subsystems, raw)
        tracker.update(f"idle:{subsystems}")


def poll_worker(tracker: Tracker) -> None:
    """Periodic poll: state changes that produced no idle event, source and health."""
    next_source = next_health = 0.0
    while True:
        now = time.monotonic()
        tracker.update("poll")
        if now >= next_source:
            tracker.check_source()
            next_source = now + SOURCE_INTERVAL
        if now >= next_health:
            tracker.log_health(force=True)
            next_health = now + HEALTH_INTERVAL
        else:
            tracker.log_health()
        time.sleep(POLL_INTERVAL)


# ================= SUMMARY =================

def summary(path: Path) -> None:
    """Print state changes and source reconnects, with the lines around them."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    marks = [i for i, ln in enumerate(lines)
             if " STATE " in ln or "SOURCE RECONNECT" in ln or "undervoltage_now" in ln]
    if not marks:
        print("No state changes in the log.")
        return
    shown = set()
    for i in marks:
        if i in shown:
            continue
        print("-" * 78)
        for j in range(max(0, i - 3), min(len(lines), i + 30)):
            if j > i and " STATE " in lines[j]:
                break  # the next change starts its own block
            if j in shown:
                continue
            print(lines[j])
            shown.add(j)
    print("-" * 78)
    print(f"{sum(1 for ln in lines if 'STATE playing ->' in ln)} 'playing -> ...' changes, "
          f"{sum(1 for ln in lines if 'SOURCE RECONNECT' in ln)} source reconnects.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--summary", type=Path, metavar="FILE", help="print the state changes of a log file and exit")
    ap.add_argument("--log", type=Path, default=LOG_FILE, help="log file (default: next to this script)")
    args = ap.parse_args()

    if args.summary:
        summary(args.summary)
        return 0

    handler = RotatingFileHandler(args.log, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    if sys.stdout.isatty():
        log.addHandler(logging.StreamHandler())

    # Survive the closing of the terminal it was started from (SIGHUP would
    # otherwise kill it silently), and log a polite stop on SIGTERM.
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def on_term(signum, _frame):
        log.info("STOP playback_logger (signal %s)", signal.Signals(signum).name)
        logging.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)

    _, version = run(["mpc", "version"])
    log.info("START playback_logger log=%s | %s", args.log, version)
    tracker = Tracker()
    tracker.update("start")
    for target in (idle_worker, poll_worker):
        threading.Thread(target=target, args=(tracker,), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("STOP playback_logger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
