import logging
from playwright.async_api import async_playwright

class BackpackClassifieds:
    def __init__(self):
        pass

    async def get_price(self, item_name: str, intent: str = "sell") -> float | None:
        """
        Парсит цену предмета с classifieds через Playwright.
        intent = "sell" или "buy"
        """
        url = f"https://backpack.tf/classifieds?item={item_name.replace(' ', '+')}&tradable=1&craftable=1&intent={intent}"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=30000)

                # ждём первый ценник
                await page.wait_for_selector("div.tag.bottom-right span", timeout=20000)
                price_text = await page.inner_text("div.tag.bottom-right span")

                await browser.close()

                # конвертируем строку в число (рефины)
                if "ref" in price_text:
                    return float(price_text.replace("ref", "").strip())
                elif "key" in price_text:
                    # условно считаем 1 key = 50 ref (позже можно сделать гибко)
                    return float(price_text.replace("key", "").strip()) * 50
                else:
                    return None

        except Exception as e:
            logging.warning(f"[BackpackTF] Ошибка при парсинге {item_name} ({intent}): {e}")
            return None

    async def get_sell_price(self, item_name: str) -> float | None:
        return await self.get_price(item_name, intent="sell")

    async def get_buy_price(self, item_name: str) -> float | None:
        return await self.get_price(item_name, intent="buy")
