import asyncio
import logging
import json
from arbitrage_upgrade import UpgradeArbitrage

logging.basicConfig(
    level=logging.INFO,
    format="tf2-arbitrage | %(asctime)s - [%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tf2-arbitrage")


async def main():
    arb = UpgradeArbitrage()
    results = await arb.run()

    print("\n=== Результаты арбитража ===")

    if results["sell"]:
        print("\n--- SELL ---")
        for item, data in results["sell"].items():
            print(f"{item}: {data['value']:.2f} {data['currency']} ({data['source']})")

    if results["buy"]:
        print("\n--- BUY ---")
        for item, data in results["buy"].items():
            print(f"{item}: {data['value']:.2f} {data['currency']} ({data['source']})")


if __name__ == "__main__":
    asyncio.run(main())