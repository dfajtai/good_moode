"""Helpers that talk to MPD (through ``mpc``) and to moOde's HTTP command API."""

import re
import subprocess
import time

import requests

from .config import MOODE_VOL_URL, STATE_IDLE, STATE_PLAYING, STREAM_URL
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
