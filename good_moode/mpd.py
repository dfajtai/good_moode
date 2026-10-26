"""Helpers that talk to MPD (through ``mpc``) and to moOde's HTTP command API."""

import re
import subprocess
import threading
import time
from typing import Optional

import requests

from .config import MOODE_VOL_URL, STATE_IDLE, STATE_PLAYING, STREAM_URL, VOLUME_WATCH_FALLBACK
from .log import log


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


class VolumeWatcher:
    """Keeps the current volume up to date by following MPD's ``mixer`` events instead of polling.

    The volume is changed by moOde (its own rotary encoder support, the web UI, ...) and MPD reports every
    change as a ``mixer`` event. A background thread waits for it with ``mpc idle mixer`` and reads the new
    value at once, so the display follows the real volume without delay and without a ``mpc`` call per
    drawn frame. ``value`` is always the last volume read; reading it costs nothing.

    ``mpc idle`` is a new connection each time, so a change that lands in the few milliseconds between two
    waits could be missed; the wait therefore also ends after ``fallback`` quiet seconds and the volume is
    re-read anyway (that is the worst case for such a miss). If MPD is unreachable the thread backs off
    for two seconds instead of spinning.
    """

    def __init__(self, fallback: float = VOLUME_WATCH_FALLBACK) -> None:
        self.fallback = fallback
        self.value = get_volume_percent()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="volume-watcher", daemon=True)

    def start(self) -> None:
        """Start following the volume."""
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to end (it is a daemon thread, so the process can exit without waiting)."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                done = subprocess.run(["mpc", "idle", "mixer"], capture_output=True, timeout=self.fallback)
                if done.returncode != 0:  # MPD unreachable or mpc failed: do not spin
                    self._stop.wait(2.0)
                    continue
            except subprocess.TimeoutExpired:
                pass  # quiet for `fallback` seconds: re-read below as a safety net
            except Exception:
                self._stop.wait(2.0)
                continue
            self.value = get_volume_percent()


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


def read_status() -> tuple[int, Optional[str]]:
    """Return ``(state, error)`` from one ``mpc status`` call.

    ``state`` is STATE_PLAYING or STATE_IDLE; any failure to ask MPD
    (unreachable, timeout, ...) counts as idle, which is the safer default
    for display purposes. ``error`` is the text of MPD's ``ERROR:`` line
    (e.g. a stream that timed out) or None. MPD keeps that line while it is
    stopped because of the error, until the next play.
    """
    try:
        out = subprocess.check_output(["mpc", "status"], text=True, timeout=0.5)
    except Exception:
        return STATE_IDLE, None
    state = STATE_PLAYING if "[playing]" in out else STATE_IDLE
    match = re.search(r"^ERROR: (.+)$", out, re.MULTILINE)
    return state, match.group(1).strip() if match else None


def get_state() -> int:
    """Return the current playback state: STATE_PLAYING or STATE_IDLE (see ``read_status``)."""
    return read_status()[0]


def resume_playback() -> bool:
    """Start playback again with ``mpc play``; returns True if the command succeeded.

    ``mpc play`` on a stopped stream makes MPD reconnect to it. Success only means
    that MPD accepted the command: if the network is still down, the next
    ``read_status`` shows the error again and the recovery retries later.
    """
    try:
        return subprocess.run(["mpc", "play"], capture_output=True, timeout=3).returncode == 0
    except Exception as exc:
        log(f"[ERROR] resuming playback failed: {exc}")
        return False


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
