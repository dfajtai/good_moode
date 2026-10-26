#!/bin/bash
# Starts the good_moode program (python -m good_moode). Started by moOde's ready-script.sh after boot.

DIR="/home/fajtai/scripts"
VENV="$DIR/.venv"
APP="$DIR/good_moode/__main__.py"   # the package is started with: python3 -m good_moode

# set PATH explicitly, to be safe when started by moOde
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Logging mode = the flag file logging.on exists next to this script (deploy.py --logging creates it,
# a deploy without it removes it). Without it NOTHING logging-related runs: no log lines, no extra
# commands, the program's log() calls do nothing, and nothing is written to the SD card.
if [ -f "$DIR/logging.on" ]; then
    LOGGING=1
    LOG="/var/log/state_machine.log"   # for a debug session without SD writes use a file in RAM: /dev/shm/...
    export GOOD_MOODE_LOGGING=1        # switches on the program's own log() calls
else
    LOGGING=0
fi

# note TEXT...: write a time-stamped line to the log (does nothing outside logging mode)
note() {
    [ "$LOGGING" = 1 ] && echo "$(date) $*" >> "$LOG"
    return 0
}

if [ "$LOGGING" = 1 ]; then echo "==============================" >> "$LOG"; fi
note "script started"

# wait a moment after boot
sleep 2

# checks
if [ ! -d "$VENV" ]; then
    note "ERROR: venv not found: $VENV"
    exit 1
fi

if [ ! -f "$APP" ]; then
    note "ERROR: app not found: $APP"
    exit 1
fi

# activate the venv
source "$VENV/bin/activate"

cd "$DIR" || exit 1

if [ "$LOGGING" = 1 ]; then
    # log the Python version, then run Python with stdout + stderr in the log
    # -u: unbuffered, so that the print()s reach the log at once and not only when the buffer fills up
    which python3 >> "$LOG" 2>&1
    python3 --version >> "$LOG" 2>&1
    note "starting python app"
    python3 -u -m good_moode >> "$LOG" 2>&1
    note "python exited with code $?"
else
    # no logging: the output (including error messages) is discarded
    exec python3 -m good_moode >/dev/null 2>&1
fi
