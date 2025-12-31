# 1. .venv aktiválás
cd ~/scripts
source .venv/bin/activate
# (venv) prompt látszik

# 2. Blinka telepítés (kritikus GPIO bridge)
pip install adafruit-blinka

# 3. Frissítés + teljes stack
pip install --upgrade \
    adafruit-circuitpython-ssd1306 \
    adafruit-blinka \
    pillow \
    aiohttp

# 4. Teszt IMPORT (venv-ben)
python -c "
import board, busio, adafruit_ssd1306
print('✅ Blinka + SSD1306 OK!')
"