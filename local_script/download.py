"""Scenario: download the Pi's logs.

    python local_script/download.py

Saves playback_diag.log*, playback_logger.err, state_machine.log, moode.log and
the MPD / kernel journals into pi_logs/<date_time>/ in the repo root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from pi_remote import connect_from_env  # noqa: E402

pi = connect_from_env()
try:
    pi.download_logs()
finally:
    pi.close()
