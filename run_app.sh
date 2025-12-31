#!/bin/bash

VENV="/home/fajtai/scripts/.venv"
APP="/home/fajtai/scripts/state_machine.py"

# kis várakozás boot után
sleep 2

# virtuális környezet aktiválása
source "$VENV/bin/activate"

# python állapotgép indítása
exec python3 "$APP"