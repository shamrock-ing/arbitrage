import aiohttp
from bs4 import BeautifulSoup

class BackpackClassifieds:
    BASE_URL = "https://backpack.tf/classifieds?item="

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                  "image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    async def fetch(self, session, url):
        try:
            async with session.get(url, headers=self.HEADERS, timeout=20) as resp:
                if resp.status != 200:
                    print(f"[ERROR] HTTP {resp.status} для {url}")
                    return None
                return await resp.text()
        except Exception as e:
            print(f"[ERROR] Не удалось загрузить {url}: {e}")
            return None

    async def get_prices(self, item_name: str):
        url = f"{self.BASE_URL}{item_name.replace(' ', '%20')}"
        async with aiohttp.ClientSession() as session:
            html = await self.fetch(session, url)
            if not html:
                return None, None

            soup = BeautifulSoup(html, "html.parser")

            # Ищем блоки цен
            tags = soup.find_all("div", class_="tag bottom-right")
            prices = []
            for tag in tags:
                span = tag.find("span")
                if not span:
                    continue
                text = span.get_text(strip=True).lower()
                if "keys" in text:
                    try:
                        prices.append(float(text.replace("keys", "").strip()))
                    except:
                        pass

            if not prices:
                return None, None

            # buy — минимальная цена (ниже), sell — максимальная (выше)
            buy = min(prices)
            sell = max(prices)

            return sell, buy