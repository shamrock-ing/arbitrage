import logging

KIT_COST_REF = 48  # цена кита в рефах

class UpgradeArbitrage:
    def __init__(self, bptf):
        self.bptf = bptf

    async def search_upgrade(self):
        # Список предметов, которые проверяем
        items = [
            "Strange Rocket Launcher",
            "Strange Scattergun",
            "Strange Sniper Rifle",
            "Strange Wrench",
            "Strange Shotgun"
        ]

        profitable_deals = []

        for name in items:
            try:
                logging.info(f"[BackpackTF] Загружаю sell-листинги для {name}...")
                sell_price = await self.bptf.get_sell_price(name)

                if not sell_price:
                    continue

                # цена после апгрейда (с китом)
                upgraded_price = sell_price + KIT_COST_REF

                # проверяем buy-листинги
                buy_price = await self.bptf.get_buy_price(f"Killstreak {name}")

                if buy_price and buy_price > upgraded_price:
                    profit = buy_price - upgraded_price
                    profitable_deals.append({
                        "item": name,
                        "buy_price": buy_price,
                        "upgraded_price": upgraded_price,
                        "profit": profit
                    })

            except Exception as e:
                logging.warning(f"[Arbitrage] Ошибка при проверке '{name}': {e}")

        return profitable_deals
