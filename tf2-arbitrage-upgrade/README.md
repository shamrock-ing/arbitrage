# TF2 Arbitrage Upgrade Bot

## Setup

1) Python 3.10+ recommended (tested on 3.13)

2) Create venv and install deps
```bash
cd tf2-arbitrage-upgrade
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```
If `venv` lacks pip on your OS:
```bash
python3 -m venv .venv --without-pip
curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py
. .venv/bin/activate
python get-pip.py
pip install -r requirements.txt
```

3) Install Playwright browsers
```bash
python -m playwright install --with-deps
```
If `--with-deps` fails on your OS, try without it:
```bash
python -m playwright install chromium
```

## Configuration

Set environment variables as needed:
```bash
export BPTF_TOKEN="<your_backpack_tf_token>"   # optional, not required for scraping
```
Optional pricing for keys in ref (to parse listings like "2 keys 10 ref"):
```bash
# Example: 60 ref per key
export KEY_PRICE_REF=60
```
You can also edit `config.py` for:
- `KIT_COST_REF` — kit price in ref
- `MIN_PROFIT_SCRAP` — minimum profit in scrap (9 scrap = 1 ref)
- `MIN_ROI` — minimum ROI (0.05 = 5%)
- `THROTTLE_SEC` — delay between page loads
- `WEAPONS` — override default list of weapons

## Run
```bash
python main.py
```