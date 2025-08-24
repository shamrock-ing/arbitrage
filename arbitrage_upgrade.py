import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logger = logging.getLogger("tf2-arbitrage")


def parse_price(text: str):
    """
    Разбирает строку цены:
    - "40.11 ref"
    - "2.33 keys"
    - "1 key, 6.11 ref"
    Возвращает (value, currency) или (None, None).
    """
    if not text:
        return None, None

    text = text.replace("~", "").lower().strip()
    text = text.replace(",", "")
    parts = text.split()
    if not parts:
        return None, None

    try:
        # пример: "40 ref" или "2 keys"
        if len(parts) == 2 and parts[1] in ["ref", "keys", "key"]:
            if parts[1] == "ref":
                return float(parts[0]), "ref"
            else:
                return float(parts[0]), "keys"

        # пример: "1 key 20 ref"
        if "key" in parts or "keys" in parts:
            if "key" in parts:
                key_index = parts.index("key")
            else:
                key_index = parts.index("keys")

            keys_val = float(parts[key_index - 1])
            ref_val = 0.0
            if "ref" in parts:
                ref_index = parts.index("ref")
                ref_val = float(parts[ref_index - 1])
            value = keys_val + (ref_val / 50.0)
            return value, "keys"
    except Exception:
        return None, None

    return None, None


class UpgradeArbitrage:
    def __init__(self):
        self.cookies_file = Path("cookies.json")
        self.config_file = Path("config.json")

        self.sell_items = []
        self.buy_items = []
        self.price_mode = "avg23"
        self.cached_sell = {}

        if self.config_file.exists():
            try:
                config = json.loads(self.config_file.read_text())
                self.sell_items = config.get("sell_items", [])
                self.buy_items = config.get("buy_items", [])
                self.price_mode = config.get("price_mode", "avg23")
            except Exception as e:
                logger.error(f"[Arbitrage] Ошибка при загрузке config.json: {e}")

    async def fetch_prices(self, page, items, intent):
        results = {}
        for item in items:
            try:
                logger.info(f"[Arbitrage] Загружаю {item} ({intent}) через stats...")

                quality = "Strange" if item.lower().startswith("strange ") else "Unique"
                item_name = item.replace("Strange ", "").strip()

                url = f"https://backpack.tf/stats/{quality}/{item_name.replace(' ', '%20')}/Tradable/Craftable"
                await page.goto(url, timeout=60000)

                selector = f'div.item[data-listing_intent="{intent}"]'
                await page.wait_for_selector(selector, timeout=30000)

                prices = await page.locator(selector).evaluate_all(
                    "elements => elements.map(e => e.getAttribute('data-listing_price'))"
                )

                logger.info(f"[DEBUG] Нашёл {len(prices)} объявлений для {item} ({intent}): {prices}")

                # ========== ЛОГИКА BUY ==========
                if intent == "buy":
                    sell_price = self.cached_sell.get(item)
                    filtered = []
                    currency = None

                    for pt in prices:
                        val, curr = parse_price(pt)
                        if val is not None:
                            if sell_price:
                                # buy должен быть ниже sell хотя бы на 5%
                                if val <= sell_price * 0.95:
                                    filtered.append(val)
                                    currency = curr
                            else:
                                filtered.append(val)
                                currency = curr

                    if filtered:
                        best_val = max(filtered)  # лучший реальный buy
                        rounded_value = round(best_val, 2)
                        price_text = f"{rounded_value:.2f} {currency}"
                        source = "BUYOrdersFiltered"
                        logger.info(f"[Arbitrage] Цена {item} (buy): {price_text} ({source})")
                        results[item] = {
                            "value": rounded_value,
                            "currency": currency,
                            "source": source
                        }
                    else:
                        logger.warning(f"[Arbitrage] Не нашёл адекватных buy ордеров для {item}")
                        results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}

                # ========== ЛОГИКА SELL ==========
                else:
                    if self.price_mode == "first":
                        price_texts = prices[:1]
                    elif self.price_mode == "avg23" and len(prices) >= 3:
                        price_texts = prices[1:3]
                    else:
                        price_texts = prices[:1]

                    values = []
                    currency = None
                    for pt in price_texts:
                        val, curr = parse_price(pt)
                        if val is not None:
                            values.append(val)
                            if not currency:
                                currency = curr

                    if values:
                        avg_value = sum(values) / len(values)
                        rounded_value = round(avg_value, 2)
                        self.cached_sell[item] = rounded_value  # сохраняем для buy
                        price_text = f"{rounded_value:.2f} {currency}"
                        source = f"{intent.upper()}Orders"
                        logger.info(f"[Arbitrage] Цена {item} (sell): {price_text} ({source})")
                        results[item] = {
                            "value": rounded_value,
                            "currency": currency,
                            "source": source
                        }
                    else:
                        raise Exception("Не удалось разобрать цены")

            except Exception as e:
                logger.error(f"[Arbitrage] Ошибка при обработке {item} ({intent}): {e}")
                try:
                    price_text = await page.locator("div.tag.bottom-right span").first.inner_text()
                    logger.info(f"[Arbitrage] Нашёл цену (Suggested) {item}: {price_text}")
                    parts = price_text.replace("~", "").split()
                    value = float(parts[0].split("–")[0])
                    currency = parts[1]
                    if currency == "key":
                        currency = "keys"
                    rounded_value = round(value, 2)
                    results[item] = {
                        "value": rounded_value,
                        "currency": currency,
                        "source": "Suggested"
                    }
                except Exception:
                    results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
        return results

    async def run(self):
        results = {"sell": {}, "buy": {}}
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=False)
            context = await browser.new_context()

            if self.cookies_file.exists():
                try:
                    cookies = json.loads(self.cookies_file.read_text())
                    for c in cookies:
                        if "expires" in c and not isinstance(c["expires"], (int, float)):
                            c.pop("expires")
                    await context.add_cookies(cookies)
                    logger.info("[Arbitrage] Куки подгружены")
                except Exception as e:
                    logger.error(f"[Arbitrage] Ошибка при загрузке куки: {e}")

            page = await context.new_page()

            if self.sell_items:
                results["sell"] = await self.fetch_prices(page, self.sell_items, "sell")

            if self.buy_items:
                # получаем SELL для buy_items, но не выводим в results["sell"]
                extra_sells = await self.fetch_prices(page, self.buy_items, "sell")
                self.cached_sell.update({k: v["value"] for k, v in extra_sells.items() if v["value"] > 0})

                # выводим только BUY
                results["buy"] = await self.fetch_prices(page, self.buy_items, "buy")

            await browser.close()
        return results