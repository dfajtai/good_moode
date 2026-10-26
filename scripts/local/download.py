"""Scenario: download the Pi's logs.

    python scripts/local/download.py

Saves playback_diag.log*, playback_logger.err, state_machine.log, moode.log and
the MPD / kernel journals into pi_logs/<date_time>/ in the repo root.
"""

from pi_remote import connect_from_env  # same folder

pi = connect_from_env()
try:
    pi.download_logs()
finally:
    pi.close()
