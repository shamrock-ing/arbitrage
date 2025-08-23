import asyncio
import logging
from backpack_classifieds import BackpackClassifieds
from arbitrage_upgrade import UpgradeArbitrage

logging.basicConfig(
    format="tf2-arbitrage | %(asctime)s - [%(levelname)s]: %(message)s",
    level=logging.INFO
)

async def main():
    logging.info("[BackpackTF] Запуск бота...")

    bptf = BackpackClassifieds()
    engine = UpgradeArbitrage(bptf)

    deals = await engine.search_upgrade()

    if deals:
        logging.info(f"[Arbitrage] Найдено {len(deals)} выгодных сделок:")
        for deal in deals:
            logging.info(deal)
    else:
        logging.info("[Arbitrage] Сделок не найдено.")

if __name__ == "__main__":
    asyncio.run(main())
