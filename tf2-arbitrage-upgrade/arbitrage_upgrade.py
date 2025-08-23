import logging
import asyncio
from config import KIT_COST_REF, MIN_PROFIT_SCRAP, MIN_ROI, WEAPONS

class UpgradeArbitrage:
    def __init__(self, bptf):
        self.bptf = bptf

    async def _check_item(self, name: str):
        logging.info(f"[BackpackTF] Загружаю sell-листинги для {name}...")
        sell_price = await self.bptf.get_sell_price(name)
        if not sell_price:
            return None

        upgraded_price = sell_price + KIT_COST_REF
        buy_price = await self.bptf.get_buy_price(f"Killstreak {name}")
        if not buy_price:
            return None

        profit = buy_price - upgraded_price
        min_profit_ref = (MIN_PROFIT_SCRAP or 0) / 9.0
        roi = (profit / upgraded_price) if upgraded_price else 0.0
        if profit >= min_profit_ref and roi >= (MIN_ROI or 0):
            return {
                "item": name,
                "buy_price": buy_price,
                "upgraded_price": upgraded_price,
                "profit": profit
            }
        return None

    async def search_upgrade(self):
        # Список предметов, которые проверяем
        items = WEAPONS or [
            "Strange Rocket Launcher",
            "Strange Scattergun",
            "Strange Sniper Rifle",
            "Strange Wrench",
            "Strange Shotgun"
        ]

        semaphore = asyncio.Semaphore(5)

        async def _wrapped(name: str):
            async with semaphore:
                try:
                    return await self._check_item(name)
                except Exception as e:
                    logging.warning(f"[Arbitrage] Ошибка при проверке '{name}': {e}")
                    return None

        tasks = [_wrapped(name) for name in items]
        results = await asyncio.gather(*tasks)
        profitable_deals = [r for r in results if r]
        return profitable_deals
