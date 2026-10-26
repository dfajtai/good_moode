# good_moode

An add-on for a [moOde](https://moodeaudio.org/) audio player on a Raspberry Pi Zero 2 W that plays an internet
radio. It adds a 128×64 OLED (now playing, clock, volume), a play/pause button (the push button of the rotary encoder, whose volume knob moOde handles itself)
and a relay that is on while music plays. Wiring: [docs/wiring.pdf](docs/wiring.pdf).

## Folders

- `good_moode/` the Python package that runs on the Pi (`python -m good_moode`)
- `scripts/pi/` files that live next to it on the Pi; `scripts/local/` tools that run on your PC
- `experiment/` hardware experiments and the OLED preview (`experiment/oled_preview.py`)
- `docs/` wiring diagram and OLED screen images
- `moode_php_commands/` files copied from the moOde system, reference only

## Quick start

1. **On the Pi:** install moOde and enable SSH.
2. **On your PC:** create the Python environment (needs [Poetry](https://python-poetry.org/)) and activate it:

       python scripts/local/setup_dev_env.py
       .venv\Scripts\activate            (Linux/macOS: source .venv/bin/activate)

   Run the tools with this environment's Python (`poetry run python scripts/local/<tool>.py`, or in VS Code
   pick `.venv` with "Python: Select Interpreter"); the system Python does not have the libraries.
   Then copy `.env.example` to `.env` (git-ignored) and fill in the Pi's address and login.
3. Run the first-time setup (safe to repeat):

       python scripts/local/initial_deploy.py

   It enables I2C, copies the files, creates the venv, installs the libraries and
   installs the ready-script, then offers a reboot. Add `--reset-stations` to also delete moOde's default
   radio stations.
4. **In moOde's web UI:** add the station `http://stream.radiomost.hu:8200/live.mp3`.

## The same steps by hand

### 1. Enable SSH and I2C

Enable SSH on the Pi. The OLED needs I2C: `sudo raspi-config` → Interface Options → I2C.

### 2. Remove the default stations

    sudo sqlite3 /var/local/www/db/moode-sqlite3.db

    SELECT * FROM cfg_radio;

    DELETE FROM cfg_radio;

    .quit

### 3. Add the station manually

In moOde's web UI: `http://stream.radiomost.hu:8200/live.mp3`

### 4. Copy the files under home

Copy the `good_moode/` folder and the contents of `scripts/pi/` to `~/scripts`, then:

    cd ~/scripts
    chmod u+x activate.sh
    chmod u+x run_app.sh
    chmod u+x run_logger.sh

### 5. Create the venv

    cd ~/scripts
    python3 -m venv --clear .venv
    source .venv/bin/activate
    pip install -r requirements.txt

### 6. Logging (off by default)

Logging writes to the SD card, so it is off: the program's output goes to `/dev/null` and the diagnostics logger does
not start. The switch is the flag file `~/scripts/logging.on`, which `run_app.sh` and `ready-script.sh` test for:

    touch ~/scripts/logging.on                                  # on
    sudo touch /var/log/state_machine.log && sudo chmod 666 /var/log/state_machine.log
    rm ~/scripts/logging.on                                     # off

(`deploy.py --logging` does this for you; a deploy without `--logging` switches it off again.)

### 7. Overwrite ready-script.sh

    sudo cp ~/scripts/ready-script.sh /var/local/www/commandw/ready-script.sh

After a reboot moOde runs it, and it starts the program (`python -m good_moode`).

### 8. Try it by hand

    cd ~/scripts && source .venv/bin/activate && python -m good_moode

## Day to day, from your PC

    python scripts/local/deploy.py      # update: copy the files, install the ready-script, restart (logging off)
    python scripts/local/deploy.py --logging   # the same with logging on (program log + diagnostics logger)
    python scripts/local/download.py    # download the logs into pi_logs/
    python scripts/local/clear_logs.py  # delete the logs on the Pi (asks first; --all also moOde's log and the journals)
    python scripts/local/reboot.py      # reboot the Pi
    python scripts/local/pi_remote.py   # interactive shell: status, tail, restart, shutdown, run, help

## Development

- OLED preview without hardware (Pillow is in the environment from `setup_dev_env.py`): `python experiment/oled_preview.py --help`
- Wiring plan: [docs/wiring.pdf](docs/wiring.pdf), regenerated with `python scripts/local/make_wiring_pdf.py` (`reportlab` is in that environment too); OLED screens: `docs/oled/overview.png`
- `moode_php_commands/` is copied from moOde (GPL-3.0, see its README) and not edited here.
