import requests
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List


def _parse_price_to_keys(text: str, key_price_ref: Optional[float]) -> Optional[float]:
    """
    Приводит строку цены к ключам:
    - "40.11 ref"
    - "2.33 keys"
    - "1 key, 6.11 ref"
    Если key_price_ref не задан и цена в ref — возвращает None.
    """
    if not text:
        return None

    raw = text.replace("~", "").lower().strip().replace(",", "")
    parts = raw.split()
    if not parts:
        return None

    try:
        # "40 ref" / "2 keys"
        if len(parts) == 2 and parts[1] in ["ref", "keys", "key"]:
            value = float(parts[0])
            if parts[1] == "ref":
                if not key_price_ref:
                    return None
                return value / float(key_price_ref)
            return value

        # "1 key 20 ref"
        if ("key" in parts or "keys" in parts) and "ref" in parts:
            if "key" in parts:
                key_index = parts.index("key")
            else:
                key_index = parts.index("keys")
            keys_val = float(parts[key_index - 1])

            ref_index = parts.index("ref")
            ref_val = float(parts[ref_index - 1])

            if not key_price_ref:
                return keys_val

            return keys_val + (ref_val / float(key_price_ref))
    except Exception:
        return None

    return None


class BackpackClassifiedsHTML:
    BASE_URL = "https://backpack.tf/classifieds?item="

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    def _build_url(self, item_name: str) -> str:
        is_strange = item_name.lower().startswith("strange ")
        quality = 11 if is_strange else 6
        base_name = item_name.replace("Strange ", "").strip()
        item_enc = base_name.replace(" ", "%20")
        return (
            f"{self.BASE_URL}{item_enc}"
            f"&quality={quality}&tradable=1&craftable=1&australium=-1&killstreak_tier=0"
        )

    def _fetch(self, url: str) -> Optional[str]:
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None

    def get_min_sell_and_verified_buy(
        self,
        item_name: str,
        key_price_ref: Optional[float] = None
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Возвращает:
        - min_sell_keys: минимальная цена продажи (в ключах)
        - verified_buy_keys: максимальный buy, строго меньше min_sell_keys (в ключах)
        """
        url = self._build_url(item_name)
        html = self._fetch(url)
        if not html:
            return None, None

        soup = BeautifulSoup(html, "html.parser")

        sell_nodes = soup.select('[data-listing_intent="sell"]')
        buy_nodes = soup.select('[data-listing_intent="buy"]')

        sell_values_keys: List[float] = []
        for node in sell_nodes:
            price_text = node.get("data-listing_price", "")
            val_keys = _parse_price_to_keys(price_text, key_price_ref)
            if val_keys is not None:
                sell_values_keys.append(val_keys)

        if not sell_values_keys:
            return None, None

        min_sell_keys = min(sell_values_keys)

        buy_values_keys: List[float] = []
        for node in buy_nodes:
            price_text = node.get("data-listing_price", "")
            val_keys = _parse_price_to_keys(price_text, key_price_ref)
            if val_keys is not None and val_keys < min_sell_keys:
                buy_values_keys.append(val_keys)

        verified_buy_keys = max(buy_values_keys) if buy_values_keys else None
        return min_sell_keys, verified_buy_keys