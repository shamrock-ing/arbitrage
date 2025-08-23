import requests
import logging
from config import BPTF_API_KEY, BPTF_PRICE_URL

class BackpackAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.params = {
            "key": BPTF_API_KEY,
            "compress": 1,
            "appid": 440
        }

    def fetch_prices(self):
        logging.info("[BackpackTF] Загружаю прайс-лист...")
        r = self.session.get(BPTF_PRICE_URL, timeout=20)
        if r.status_code != 200:
            raise Exception(f"Backpack.tf API error {r.status_code}")
        data = r.json()
        return data.get("response", {}).get("items", {})
