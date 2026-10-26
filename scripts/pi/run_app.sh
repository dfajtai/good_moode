#!/bin/bash

DIR="/home/fajtai/scripts"
VENV="$DIR/.venv"

# Logging is OFF unless the flag file logging.on exists next to this script (deploy.py --logging
# creates it, deploy.py without it removes it). Off means: nothing is written to the SD card.
# For a one-off debug session without SD writes, use a file in RAM: LOG="/dev/shm/state_machine.log"
if [ -f "$DIR/logging.on" ]; then
    LOG="/var/log/state_machine.log"
else
    LOG="/dev/null"
fi
APP="$DIR/good_moode/__main__.py"   # the package is started with: python3 -m good_moode

echo "==============================" >> "$LOG"
echo "$(date) script started" >> "$LOG"

# set PATH explicitly, to be safe when started by moOde
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# wait a moment after boot
sleep 2
echo "$(date) after sleep" >> "$LOG"

# checks
if [ ! -d "$VENV" ]; then
    echo "$(date) ERROR: venv not found: $VENV" >> "$LOG"
    exit 1
fi

if [ ! -f "$APP" ]; then
    echo "$(date) ERROR: app not found: $APP" >> "$LOG"
    exit 1
fi

# activate the venv
echo "$(date) activating venv" >> "$LOG"
source "$VENV/bin/activate" >> "$LOG" 2>&1

# log the Python version
which python3 >> "$LOG" 2>&1
python3 --version >> "$LOG" 2>&1

echo "$(date) starting python app" >> "$LOG"

# run Python (stdout + stderr go to the log)
# -u: unbuffered stdout/stderr, so that the print()s reach the log at once and not only when
# the buffer fills up or the process ends
cd "$DIR" && python3 -u -m good_moode >> "$LOG" 2>&1

echo "$(date) python exited with code $?" >> "$LOG"
