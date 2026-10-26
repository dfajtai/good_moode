# good_moode

**A simplification of [moOde](https://moodeaudio.org/): an internet radio that runs completely on its own, with a
single button.** moOde is a full audio player with a big web interface; this project turns it into an appliance on a
Raspberry Pi Zero 2 W. You plug it in, and it:

- **starts by itself** after power-up and plays one chosen station (no phone, no web page, no menu);
- is **operated with one button**: the push button of the rotary encoder toggles play / pause, and the encoder's knob
  is the volume (moOde handles the knob itself);
- **shows what is going on** on a small 128×64 OLED: the current title when the radio sends one, a large clock, and the
  volume as a thin bar;
- **switches an amplifier or speaker** on and off with a relay while music plays;
- **looks after itself**: if the stream stops because the network dropped, it starts playing again once the
  connection is back.

moOde still does the audio (MPD, the DAC, the web UI for setup); `good_moode` is only the small Python program around it
plus the tools to install it. Wiring: [docs/wiring.pdf](docs/wiring.pdf).

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
4. **In moOde's web UI** (two manual steps, the scripts cannot do them):
   - add the station `http://stream.radiomost.hu:8200/mobil.aac`;
   - set up the rotary encoder (it is configured in moOde, not here): in *Configure → Peripherals → Rotary encoder*
     switch it **On** and enter the parameters `50 2 10 17 27`, then save (reboot if moOde asks).

## The same steps by hand

### 1. Enable SSH and I2C

Enable SSH on the Pi. The OLED needs I2C: `sudo raspi-config` → Interface Options → I2C.

### 2. Remove the default stations

    sudo sqlite3 /var/local/www/db/moode-sqlite3.db

    SELECT * FROM cfg_radio;

    DELETE FROM cfg_radio;

    .quit

### 3. Add the station manually

In moOde's web UI: `http://stream.radiomost.hu:8200/mobil.aac`

### 4. Set up the rotary encoder in moOde

The volume knob is handled by moOde itself, so the encoder has to be configured in moOde's web UI (menu names can differ a little
between moOde versions): *Configure → Peripherals → Rotary encoder*

- switch the rotary encoder **On**;
- parameters: `50 2 10 17 27` (three tuning values, then the two GPIO pins: S1 = GPIO17, S2 = GPIO27);
- save, and reboot if moOde asks for it.

The encoder's push button (KEY, GPIO22) is the play/pause button of `good_moode`: do not assign GPIO22 to the encoder's switch in
moOde, otherwise one press would toggle twice. The OLED only shows the volume that moOde sets.

### 5. Copy the files under home

Copy the `good_moode/` folder and the contents of `scripts/pi/` to `~/scripts`, then:

    cd ~/scripts
    chmod u+x activate.sh
    chmod u+x run_app.sh
    chmod u+x run_logger.sh

### 6. Create the venv

    cd ~/scripts
    python3 -m venv --clear .venv
    source .venv/bin/activate
    pip install -r requirements.txt

### 7. Logging (off by default)

Logging writes to the SD card, so it is off: the program's output goes to `/dev/null` and the diagnostics logger does
not start. The switch is the flag file `~/scripts/logging.on`, which `run_app.sh` and `ready-script.sh` test for:

    touch ~/scripts/logging.on                                  # on
    sudo touch /var/log/state_machine.log && sudo chmod 666 /var/log/state_machine.log
    rm ~/scripts/logging.on                                     # off

(`deploy.py --logging` does this for you; a deploy without `--logging` switches it off again.)

### 8. Overwrite ready-script.sh

    sudo cp ~/scripts/ready-script.sh /var/local/www/commandw/ready-script.sh

After a reboot moOde runs it, and it starts the program (`python -m good_moode`).

### 9. Try it by hand

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
