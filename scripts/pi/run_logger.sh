#!/bin/bash
# Starts the playback diagnostics logger (see good_moode/playback_logger.py).
#
# Files in /home/fajtai/scripts:
#   playback_diag.log     the logger's own log (rotated)
#   playback_logger.err   this launcher's start marker and any startup error /
#                         Python traceback; if this file exists but
#                         playback_diag.log does not, read it first.

DIR="/home/fajtai/scripts"
VENV="$DIR/.venv"
APP="$DIR/good_moode/playback_logger.py"
ERR="$DIR/playback_logger.err"

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "$(date '+%Y-%m-%d %H:%M:%S') run_logger.sh started as $(id -un)" >> "$ERR"

if [ ! -d "$VENV" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: venv not found: $VENV" >> "$ERR"
    exit 1
fi
if [ ! -f "$APP" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: app not found: $APP" >> "$ERR"
    exit 1
fi

source "$VENV/bin/activate"
cd "$DIR" && exec python3 -u -m good_moode.playback_logger 2>> "$ERR"
