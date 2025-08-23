import logging
import asyncio
import re
from urllib.parse import quote_plus
from playwright.async_api import async_playwright
from config import KEY_PRICE_REF, THROTTLE_SEC


def parse_price_to_ref(price_text: str) -> float | None:
    """
    Convert price text like "11 ref", "2 keys 12 ref", "1.5 keys"
    into refined metal (ref) units.
    Returns None if cannot parse or keys present but KEY_PRICE_REF is None.
    """
    if not price_text:
        return None

    text = price_text.lower()
    key_match = re.search(r"(\d+(?:\.\d+)?)\s*keys?", text)
    ref_match = re.search(r"(\d+(?:\.\d+)?)\s*ref", text)

    total_ref = 0.0
    if key_match:
        if KEY_PRICE_REF is None:
            return None
        total_ref += float(key_match.group(1)) * float(KEY_PRICE_REF)

    if ref_match:
        total_ref += float(ref_match.group(1))

    if total_ref > 0.0:
        return total_ref

    # Fallback: try a lone number treated as ref (e.g., "11 each")
    lone_number = re.search(r"(\d+(?:\.\d+)?)", text)
    if lone_number:
        return float(lone_number.group(1))

    return None


class BackpackClassifieds:
    def __init__(self):
        pass

    async def get_price(self, item_name: str, intent: str = "sell") -> float | None:
        """
        Парсит цену предмета с classifieds через Playwright.
        intent = "sell" или "buy"
        """
        url = (
            f"https://backpack.tf/classifieds?item={quote_plus(item_name)}&tradable=1&craftable=1&intent={intent}"
        )

        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=30000)

                # ждём первый ценник в списке и читаем его
                price_locator = page.locator("div.tag.bottom-right span").first
                await price_locator.wait_for(timeout=20000)
                price_text = await price_locator.inner_text()

                price_ref = parse_price_to_ref(price_text)

                # throttle между запросами
                if THROTTLE_SEC and THROTTLE_SEC > 0:
                    await asyncio.sleep(THROTTLE_SEC)

                return price_ref

        except Exception as e:
            logging.warning(f"[BackpackTF] Ошибка при парсинге {item_name} ({intent}): {e}")
            return None
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def get_sell_price(self, item_name: str) -> float | None:
        return await self.get_price(item_name, intent="sell")

    async def get_buy_price(self, item_name: str) -> float | None:
        return await self.get_price(item_name, intent="buy")
