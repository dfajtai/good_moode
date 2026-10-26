#!/bin/bash

LOG="/var/log/state_machine.log"
VENV="/home/fajtai/scripts/.venv"
DIR="/home/fajtai/scripts"
APP="$DIR/good_moode/__main__.py"   # the package is started with: python3 -m good_moode

echo "==============================" >> "$LOG"
echo "$(date) script started" >> "$LOG"

# biztonság kedvéért PATH
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# várunk boot után
sleep 2
echo "$(date) after sleep" >> "$LOG"

# ellenőrzések
if [ ! -d "$VENV" ]; then
    echo "$(date) ERROR: venv not found: $VENV" >> "$LOG"
    exit 1
fi

if [ ! -f "$APP" ]; then
    echo "$(date) ERROR: app not found: $APP" >> "$LOG"
    exit 1
fi

# venv aktiválás
echo "$(date) activating venv" >> "$LOG"
source "$VENV/bin/activate" >> "$LOG" 2>&1

# python verzió
which python3 >> "$LOG" 2>&1
python3 --version >> "$LOG" 2>&1

echo "$(date) starting python app" >> "$LOG"

# python futtatás (stdout+stderr logba)
# -u: unbuffered stdout/stderr, hogy a print()-ek azonnal a logba kerüljenek,
# ne csak a buffer megteléskor/process leállásakor
cd "$DIR" && python3 -u -m good_moode >> "$LOG" 2>&1

echo "$(date) python exited with code $?" >> "$LOG"
