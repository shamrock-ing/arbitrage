import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
from urllib.parse import quote

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
				if intent == "buy":
					logger.info(f"[Arbitrage] Загружаю {item} (buy) через classifieds (scraping only)...")

					# Готовим параметры classifieds: убираем Strange и выставляем quality
					is_strange = item.lower().startswith("strange ")
					quality = 11 if is_strange else 6
					item_name = item.replace("Strange ", "").strip()
					item_enc = quote(item_name, safe="")
					url_class = (
						f"https://backpack.tf/classifieds?item={item_enc}"
						f"&quality={quality}&tradable=1&craftable=1&australium=-1&killstreak_tier=0"
					)
					logger.info(f"[Arbitrage][BUY] URL → {url_class}")
					await page.goto(url_class, timeout=60000, wait_until="networkidle")
					logger.info(f"[Arbitrage][BUY] At → {page.url}")

					await page.locator('[data-listing_intent="sell"], [data-listing_intent="buy"]').first.wait_for(state="attached", timeout=60000)

					sell_prices_raw = await page.locator('[data-listing_intent="sell"]').evaluate_all(
						"elements => elements.map(e => e.getAttribute('data-listing_price'))"
					)
					buy_prices_raw = await page.locator('[data-listing_intent="buy"]').evaluate_all(
						"elements => elements.map(e => e.getAttribute('data-listing_price'))"
					)

					sell_values_keys = []
					for pt in sell_prices_raw:
						val, curr = parse_price(pt)
						if val is not None and curr == "keys":
							sell_values_keys.append(val)

					if not sell_values_keys:
						logger.warning(f"[Arbitrage] Нет валидных SELL объявлений в ключах для {item}")
						results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
						continue

					min_sell_keys = min(sell_values_keys)

					filtered_buy_keys = []
					for pt in buy_prices_raw:
						val, curr = parse_price(pt)
						if val is not None and curr == "keys" and val < min_sell_keys:
							filtered_buy_keys.append(val)

					if filtered_buy_keys:
						best_val = max(filtered_buy_keys)
						rounded_value = round(best_val, 2)
						results[item] = {"value": rounded_value, "currency": "keys", "source": "ClassifiedsVerified"}
						logger.info(f"[Arbitrage] (HTML) {item}: buy={rounded_value:.2f} keys, min sell={min_sell_keys:.2f} keys")
					else:
						logger.warning(f"[Arbitrage] Не нашёл buy ниже минимального sell для {item}")
						results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}

				else:
					logger.info(f"[Arbitrage] Загружаю {item} (sell) через stats...")

					quality = "Strange" if item.lower().startswith("strange ") else "Unique"
					item_name = item.replace("Strange ", "").strip()
					item_enc = quote(item_name, safe="")

					url = f"https://backpack.tf/stats/{quality}/{item_enc}/Tradable/Craftable"
					logger.info(f"[Arbitrage][SELL] URL → {url}")
					await page.goto(url, timeout=60000, wait_until="networkidle")
					logger.info(f"[Arbitrage][SELL] At → {page.url}")

					selector = 'div.item[data-listing_intent="sell"]'
					await page.locator(selector).first.wait_for(state="attached", timeout=60000)

					prices = await page.locator(selector).evaluate_all(
						"elements => elements.map(e => e.getAttribute('data-listing_price'))"
					)

					logger.info(f"[DEBUG] Нашёл {len(prices)} объявлений для {item} (sell): {prices}")

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
						self.cached_sell[item] = rounded_value
						price_text = f"{rounded_value:.2f} {currency}"
						source = "SELLOrders"
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
					# Пытаемся взять Suggested тег, если доступно
					span = page.locator("div.tag.bottom-right span").first
					text = await span.inner_text()
					val, curr = parse_price(text)
					if val is None:
						raise Exception("Suggested parse failed")
					rounded_value = round(val, 2)
					results[item] = {
						"value": rounded_value,
						"currency": curr or "unknown",
						"source": "Suggested"
					}
				except Exception:
					results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
		return results

	async def run(self):
		results = {"sell": {}, "buy": {}}
		async with async_playwright() as p:
			browser = await p.chromium.launch(headless=True, args=["--no-sandbox"]) 
			context = await browser.new_context(
				user_agent=(
					"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
					"AppleWebKit/537.36 (KHTML, like Gecko) "
					"Chrome/120.0.0.0 Safari/537.36"
				),
				locale="en-US",
				java_script_enabled=True,
				viewport={"width": 1366, "height": 768},
			)
			# скрываем webdriver
			await context.add_init_script(
				"Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
			)

			# Загружаем куки и нормализуем домены/sameSite
			if self.cookies_file.exists():
				try:
					cookies = json.loads(self.cookies_file.read_text())
					norm_cookies = []
					for c in cookies:
						cookie = dict(c)
						if "expires" in cookie and not isinstance(cookie.get("expires"), (int, float)):
							cookie.pop("expires")
						domain = cookie.get("domain")
						if domain and not domain.startswith("."):
							cookie["domain"] = f".{domain}"
						cookie.setdefault("sameSite", "Lax")
						norm_cookies.append(cookie)
					await context.add_cookies(norm_cookies)
					logger.info("[Arbitrage] Куки подгружены")
				except Exception as e:
					logger.error(f"[Arbitrage] Ошибка при загрузке куки: {e}")

			page = await context.new_page()
			await page.set_extra_http_headers({
				"Accept-Language": "en-US,en;q=0.9",
			})

			# Предварительный логин, чтобы classifieds не редиректили на Steam OpenID
			try:
				await page.goto("https://backpack.tf/login", timeout=60000, wait_until="networkidle")
				logger.info(f"[Arbitrage][LOGIN] At → {page.url}")
			except Exception:
				pass

			if self.sell_items:
				results["sell"] = await self.fetch_prices(page, self.sell_items, "sell")

			if self.buy_items:
				results["buy"] = await self.fetch_prices(page, self.buy_items, "buy")

			await browser.close()
		return results