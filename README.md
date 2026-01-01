# HOW TO USE

## Enable SSH

## Remove default stations

sudo sqlite3 /var/local/www/db/moode-sqlite3.db

SELECT * FROM cfg_radio;

DELETE FROM cfg_radio;

.quit

## Add station manually

http://stream.radiomost.hu:8200/live.mp3

## Copy contents of scripts folder under home ...
cd ~/scripts
chmod u+x activate.sh
chmod u+x run_app.sh
chmod u+x moode_state_machine.py

## Set up log - if wished
sudo touch /var/log/state_machine.log
sudo chmod 666 /var/log/state_machine.log

## Create venv

python -m venv -n .venv --clear
pip install -r requirements.txt

## Overwirte ready-script.sh
sudo cp ready-script.sh /var/local/www/commandw/ready-script.sh