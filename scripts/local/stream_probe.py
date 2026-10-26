#!/usr/bin/env python3
"""Data collector + analyzer for the Radio Most "now playing" metadata.

Since ~2026-07 the station sends a fixed placeholder ("Telefon-SMS-Viber_-_
<phone number>") instead of the real track title. ``StreamProbe`` polls every
known source of now-playing info, keeps a JSONL history and tells whether the
situation has improved. Run it from time to time (or from cron):

    python stream_probe.py --check                 # sample once, analyze history
    python stream_probe.py --watch 300 --interval 5  # sample for 5 minutes

Sources sampled on every run:

- ``icy:<mount>``      ICY ``StreamTitle`` of the live stream itself
- ``icecast:<mount>``  title in Icecast's ``status-json.xsl``
- ``web:current``      ``current`` in the website's ``song.json``
- ``web:previous``     ``previous`` in ``song.json`` (frozen while ``current``
                       is stuck, so a change here means the automation moves)
- ``api:current/previous``  same data from the site's ``api/get_song.php``

It also looks for info hidden elsewhere: extra ICY fields (e.g. ``StreamUrl``)
and in-band ID3 tags in the audio data.

Only needs ``requests``; deliberately does not import good_moode
(which pulls in GPIO / OLED libraries that only exist on the Pi).
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ================= CONFIG =================

STREAM_BASE = "http://stream.radiomost.hu:8200"
MOUNTS = ("live.mp3", "mobil.aac")
SONG_JSON_URL = "https://kaposvarmost.hu/radio/cache/song.json"
SONG_API_URL = "https://kaposvarmost.hu/radio/api/get_song.php"  # used by the site's radio box

PLACEHOLDER_PREFIX = "telefon-sms-viber"  # keep in sync with good_moode/config.py
HISTORY_FILE = Path(__file__).resolve().parent / "stream_probe_history.jsonl"

HTTP_TIMEOUT = 8  # seconds per request
SAVE_EVERY = 3600  # seconds; an unchanged snapshot is stored at most this often
USER_AGENT = "RadioMetadataProbe/1.0"

NOW_PLAYING_SOURCES_PREFIXES = ("icy:", "icecast:", "web:current", "api:current")


# ================= HELPERS =================

def is_placeholder(title: Optional[str]) -> bool:
    """True for an empty title or the station's fixed placeholder."""
    return not title or title.strip().casefold().startswith(PLACEHOLDER_PREFIX)


def _is_now_playing(source: str) -> bool:
    return source.startswith(NOW_PLAYING_SOURCES_PREFIXES)


# ================= DATA =================

@dataclass
class Snapshot:
    """One sampling of all sources.

    Attributes:
        ts: ISO timestamp (local time) of the sampling.
        titles: Source name -> title (``None`` if the source had no title).
        extras: Source name -> anything unexpected found there (extra ICY
            fields, ID3 tag). Empty when there is nothing hidden.
        errors: Source name -> error text for sources that failed.
    """

    ts: str
    titles: dict[str, Optional[str]] = field(default_factory=dict)
    extras: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def same_content(self, other: "Snapshot") -> bool:
        return self.titles == other.titles and self.extras == other.extras


@dataclass
class Report:
    """Result of ``StreamProbe.analyze``."""

    verdict: str  # "HIBAS" | "MOZOG" | "JAVULT"
    samples: int
    first_ts: str
    last_ts: str
    now_playing: dict[str, Optional[str]]
    real_now_playing: dict[str, str]
    previous_values: list[str]
    distinct_titles: dict[str, int]
    hidden: dict[str, str]
    errors: dict[str, str]

    def format(self) -> str:
        v = {
            "HIBAS": "VÁLTOZATLAN / HIBÁS - csak a fix felirat látszik",
            "MOZOG": "RÉSZLEGES - a 'previous' mozog, de a 'current' még felirat",
            "JAVULT": "JAVULT - valódi cím érkezik",
        }[self.verdict]
        lines = [
            f"Eredmény: {v}",
            f"Minták: {self.samples} db ({self.first_ts} .. {self.last_ts})",
            "",
            "Most játszik (utolsó minta):",
        ]
        for src, title in self.now_playing.items():
            mark = "OK " if src in self.real_now_playing else "-- "
            lines.append(f"  {mark}{src:<22} {title!r}")
        lines.append(f"  (previous értékek eddig: {self.previous_values})")
        lines.append("")
        lines.append(
            "Különböző címek forrásonként: "
            + ", ".join(f"{s}={n}" for s, n in self.distinct_titles.items())
        )
        if self.hidden:
            lines.append("")
            lines.append("REJTETT / EXTRA ADAT:")
            lines += [f"  {s}: {x}" for s, x in self.hidden.items()]
        else:
            lines.append("Rejtett adat: nincs (nincs extra ICY mező, nincs ID3 tag)")
        if self.errors:
            lines.append("")
            lines.append("Hibás források az utolsó mintában:")
            lines += [f"  {s}: {e}" for s, e in self.errors.items()]
        return "\n".join(lines)


# ================= PROBE =================

class StreamProbe:
    """Collects now-playing metadata from every source and analyzes the history."""

    def __init__(self, history_file: Path = HISTORY_FILE) -> None:
        self.history_file = history_file
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    # ---- collecting ----

    def _read_icy(self, mount: str) -> tuple[Optional[str], str]:
        """Read one ICY block from a mount.

        Returns ``(title, extras)`` where extras describes ICY fields other
        than StreamTitle and any in-band ID3 tag.
        """
        r = self._session.get(
            f"{STREAM_BASE}/{mount}",
            headers={"Icy-MetaData": "1"},
            stream=True,
            timeout=HTTP_TIMEOUT,
        )
        try:
            r.raise_for_status()
            metaint = int(r.headers.get("icy-metaint", "0"))
            if not metaint:
                return None, "nincs icy-metaint (a stream nem küld ICY adatot)"

            audio = r.raw.read(metaint)
            extras = []
            if audio[:3] == b"ID3" or b"TIT2" in audio:
                extras.append("ID3 tag az audio adatban")

            length = r.raw.read(1)
            block = ""
            if length and ord(length):
                block = r.raw.read(ord(length) * 16).decode("iso-8859-2", "ignore")

            fields = dict(re.findall(r"(\w+)='([^']*)';", block))
            title = (fields.pop("StreamTitle", "") or "").strip() or None
            extras += [f"{k}={v!r}" for k, v in fields.items() if v.strip()]
            return title, "; ".join(extras)
        finally:
            r.close()

    def _read_icecast(self) -> dict[str, Optional[str]]:
        """Titles per mount from Icecast's ``status-json.xsl``."""
        r = self._session.get(f"{STREAM_BASE}/status-json.xsl", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        sources = r.json()["icestats"].get("source", [])
        if isinstance(sources, dict):  # Icecast returns an object for one mount
            sources = [sources]
        out: dict[str, Optional[str]] = {}
        for s in sources:
            mount = s.get("listenurl", "").rsplit("/", 1)[-1]
            out[mount] = (s.get("title") or "").strip() or None
        return out

    def _read_song_json(self, url: str = SONG_JSON_URL) -> dict:
        r = self._session.get(url, params={"_": int(time.time())}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def collect(self) -> Snapshot:
        """Sample every source once. Never raises; failures go to ``errors``."""
        snap = Snapshot(ts=datetime.now().isoformat(timespec="seconds"))

        for mount in MOUNTS:
            src = f"icy:{mount}"
            try:
                title, extra = self._read_icy(mount)
                snap.titles[src] = title
                if extra:
                    snap.extras[src] = extra
            except Exception as exc:
                snap.errors[src] = str(exc)

        try:
            icecast = self._read_icecast()
            for mount in MOUNTS:
                snap.titles[f"icecast:{mount}"] = icecast.get(mount)
        except Exception as exc:
            snap.errors["icecast"] = str(exc)

        try:
            song = self._read_song_json()
            snap.titles["web:current"] = song.get("current") or None
            snap.titles["web:previous"] = song.get("previous") or None
            unknown = set(song) - {"current", "previous"}
            if unknown:
                snap.extras["web:song.json"] = f"új mezők: {sorted(unknown)}"
        except Exception as exc:
            snap.errors["web:song.json"] = str(exc)

        try:
            api = self._read_song_json(SONG_API_URL)
            snap.titles["api:current"] = api.get("current") or None
            snap.titles["api:previous"] = api.get("previous") or None
        except Exception as exc:
            snap.errors["api:get_song.php"] = str(exc)

        return snap

    # ---- history ----

    def load_history(self) -> list[Snapshot]:
        if not self.history_file.exists():
            return []
        out = []
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(Snapshot(**json.loads(line)))
                except (ValueError, TypeError):
                    pass  # skip a corrupt line instead of losing the history
        return out

    def save(self, snap: Snapshot) -> bool:
        """Append ``snap`` to the history unless it adds nothing new.

        An unchanged snapshot is stored only if the previous one is older
        than ``SAVE_EVERY`` seconds, so a long watch doesn't bloat the file.

        Returns:
            True if the snapshot was written.
        """
        history = self.load_history()
        if history and history[-1].same_content(snap):
            age = (
                datetime.fromisoformat(snap.ts)
                - datetime.fromisoformat(history[-1].ts)
            ).total_seconds()
            if age < SAVE_EVERY:
                return False
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap.__dict__, ensure_ascii=False) + "\n")
        return True

    # ---- analyzing ----

    def analyze(self, history: Optional[list[Snapshot]] = None) -> Report:
        """Judge the situation from the (stored) history.

        Verdict:
            JAVULT: some now-playing source carries a real title.
            MOZOG:  no real title yet, but ``web:previous`` has changed, i.e.
                    the station's automation is moving again.
            HIBAS:  only the placeholder, nothing moves.
        """
        history = history if history is not None else self.load_history()
        if not history:
            raise ValueError("nincs minta - futtasd előbb a collect()/--check-et")

        last = history[-1]
        now_playing = {s: t for s, t in last.titles.items() if _is_now_playing(s)}
        real = {s: t for s, t in now_playing.items() if not is_placeholder(t)}

        previous_values: list[str] = []
        for snap in history:
            for key in ("web:previous", "api:previous"):
                p = snap.titles.get(key)
                if p and p not in previous_values:
                    previous_values.append(p)

        seen: dict[str, set[str]] = {}
        for snap in history:
            for src, t in snap.titles.items():
                if t:
                    seen.setdefault(src, set()).add(t)
        distinct_counts = {s: len(v) for s, v in seen.items()}

        hidden: dict[str, str] = {}
        for snap in history:
            hidden.update(snap.extras)

        if real:
            verdict = "JAVULT"
        elif len(previous_values) > 1:
            verdict = "MOZOG"
        else:
            verdict = "HIBAS"

        return Report(
            verdict=verdict,
            samples=len(history),
            first_ts=history[0].ts,
            last_ts=last.ts,
            now_playing=now_playing,
            real_now_playing=real,
            previous_values=previous_values,
            distinct_titles=distinct_counts,
            hidden=hidden,
            errors=last.errors,
        )

    # ---- convenience ----

    def check(self) -> Report:
        """Sample once, store it, and analyze the whole history."""
        self.save(self.collect())
        return self.analyze()

    def watch(self, duration: float, interval: float = 5.0) -> Report:
        """Sample every ``interval`` seconds for ``duration`` seconds."""
        end = time.monotonic() + duration
        while True:
            snap = self.collect()
            if self.save(snap):
                print(f"[{snap.ts}] változás: {snap.titles}", flush=True)
            if time.monotonic() + interval >= end:
                break
            time.sleep(interval)
        return self.analyze()


# ================= MAIN =================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="egy minta + elemzés (alapértelmezett)")
    ap.add_argument("--watch", type=float, metavar="SEC", help="ennyi másodpercig gyűjt")
    ap.add_argument("--interval", type=float, default=5.0, help="mintavétel gyakorisága (mp)")
    ap.add_argument("--history", type=Path, default=HISTORY_FILE, help="JSONL előzményfájl")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    probe = StreamProbe(args.history)
    report = probe.watch(args.watch, args.interval) if args.watch else probe.check()
    print(report.format())
    return {"JAVULT": 0, "MOZOG": 2, "HIBAS": 1}[report.verdict]


if __name__ == "__main__":
    sys.exit(main())
