# HOW TO USE

moOde audio player add-on for a Raspberry Pi Zero 2 W: OLED (clock / now playing), play/pause button and a
relay that is on while music plays. Wiring: [docs/wiring.pdf](docs/wiring.pdf).

## Enable SSH

Also enable I2C (the OLED needs it): `sudo raspi-config` → Interface Options → I2C.

## Remove default stations

sudo sqlite3 /var/local/www/db/moode-sqlite3.db

SELECT * FROM cfg_radio;

DELETE FROM cfg_radio;

.quit

## Add station manually

http://stream.radiomost.hu:8200/live.mp3

## Copy the files under home ...
Copy the `good_moode/` folder and the contents of `scripts/pi/` to `~/scripts`
(or run `python scripts/local/deploy.py` from your PC, see below).

    cd ~/scripts
    chmod u+x activate.sh
    chmod u+x run_app.sh
    chmod u+x run_logger.sh

## Set up log - if wished

    sudo touch /var/log/state_machine.log
    sudo chmod 666 /var/log/state_machine.log

## Create venv

    cd ~/scripts
    python3 -m venv --clear .venv
    source .venv/bin/activate
    pip install -r requirements.txt

## Overwrite ready-script.sh

    sudo cp ~/scripts/ready-script.sh /var/local/www/commandw/ready-script.sh

After a reboot moOde runs it, and it starts the program (`python -m good_moode`) and the diagnostics logger.
To try it by hand: `cd ~/scripts && source .venv/bin/activate && python -m good_moode`

## From your PC

Install `paramiko` and create a `.env` file in the repo root (git-ignored):

    PI_HOST=192.168.0.80
    PI_USER=fajtai
    PI_PASSWORD=...

    python scripts/local/deploy.py      # copy the files, install the ready-script, restart
    python scripts/local/download.py    # download the logs into pi_logs/
    python scripts/local/reboot.py      # reboot the Pi

## Folders

- `good_moode/` the Python package that runs on the Pi (`python -m good_moode`)
- `scripts/pi/` files that live next to it on the Pi; `scripts/local/` tools that run on your PC
- `experiment/` hardware experiments and the OLED preview (`experiment/oled_preview.py`)
- `docs/` wiring diagram and OLED screen images
- `moode_php_commands/` files copied from the moOde system, reference only
