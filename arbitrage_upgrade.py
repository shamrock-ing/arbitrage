import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
from urllib.parse import quote
from config import KEY_PRICE_REF

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


async def _load_all_classifieds_orders(page):
	"""
	Подгрузка ордеров на странице classifieds: автоскролл пока число карточек растёт.
	"""
	await asyncio.sleep(0.5)
	last_total = -1
	for _ in range(12):
		await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
		await asyncio.sleep(0.6)
		total = await page.locator('[data-listing_intent="buy"], [data-listing_intent="sell"]').count()
		if total <= last_total:
			break
		last_total = total


def _to_keys_if_possible(value: float, currency: str, key_price_ref: float | None):
	"""
	Возвращает (keys_value, ok).
	ok=False, если конвертация невозможна (ref без key_price_ref).
	"""
	if currency == "keys":
		return value, True
	if currency == "ref" and key_price_ref:
		try:
			return value / float(key_price_ref), True
		except Exception:
			return None, False
	return None, False


class UpgradeArbitrage:
	def __init__(self):
		self.cookies_file = Path("cookies.json")
		self.config_file = Path("config.json")

		self.sell_items = []
		self.buy_items = []
		self.price_mode = "avg23"
		self.cached_sell = {}
		self.runtime_key_price_ref = None  # определяем динамически, если не задано в конфиге

		if self.config_file.exists():
			try:
				config = json.loads(self.config_file.read_text())
				self.sell_items = config.get("sell_items", [])
				self.buy_items = config.get("buy_items", [])
				self.price_mode = config.get("price_mode", "avg23")
			except Exception as e:
				logger.error(f"[Arbitrage] Ошибка при загрузке config.json: {e}")

	async def _detect_key_price_ref(self, page) -> float | None:
		"""
		Пытается определить цену ключа в ref, если KEY_PRICE_REF не задан:
		идёт на stats ключа и берёт минимальный sell в ref.
		"""
		try:
			key_stats = "https://backpack.tf/stats/Unique/Mann%20Co.%20Supply%20Crate%20Key/Tradable/Craftable"
			await page.goto(key_stats, timeout=90000, wait_until="domcontentloaded")
			await page.locator('div.item[data-listing_intent="sell"]').first.wait_for(state="attached", timeout=90000)
			await asyncio.sleep(0.5)
			sell_prices = await page.locator('div.item[data-listing_intent="sell"]').evaluate_all(
				"elements => elements.map(e => e.getAttribute('data-listing_price'))"
			)
			candidates = []
			for pt in sell_prices:
				val, curr = parse_price(pt)
				# для ключа ожидаем цены в ref
				if val is not None and curr == "ref":
					candidates.append(val)
			if candidates:
				est = min(candidates)
				logger.info(f"[Arbitrage] Обнаружена цена ключа: ~{est:.2f} ref")
				return est
		except Exception as e:
			logger.warning(f"[Arbitrage] Не удалось определить цену ключа через stats: {e}")
		return None

	async def fetch_prices(self, page, items, intent):
		results = {}
		for item in items:
			try:
				# небольшая пауза между запросами
				await asyncio.sleep(0.6)

				if intent == "buy":
					logger.info(f"[Arbitrage] Загружаю {item} (buy) через classifieds (scraping only)...")

					is_strange = item.lower().startswith("strange ")
					quality = 11 if is_strange else 6
					item_name = item.replace("Strange ", "").strip()
					item_enc = quote(item_name, safe="")
					base_url = (
						f"https://backpack.tf/classifieds?item={item_enc}"
						f"&quality={quality}&tradable=1&craftable=1&australium=-1&killstreak_tier=0"
					)

					# Определяем цену ключа в ref (если не задана в конфиге) один раз за сессию
					effective_key_ref = KEY_PRICE_REF or self.runtime_key_price_ref
					if not effective_key_ref:
						self.runtime_key_price_ref = await self._detect_key_price_ref(page)
						effective_key_ref = self.runtime_key_price_ref

					# PASS 1: глобальный min SELL (в ключах; конвертируем ref при необходимости)
					global_min_sell = None
					prev_sell_count = 0
					max_pages = 5
					for page_num in range(1, max_pages + 1):
						url = base_url if page_num == 1 else f"{base_url}&page={page_num}"
						logger.info(f"[Arbitrage][BUY/P1] URL → {url}")
						await page.goto(url, timeout=90000, wait_until="domcontentloaded")
						await asyncio.sleep(0.5)
						await page.locator('[data-listing_intent="sell"], [data-listing_intent="buy"]').first.wait_for(state="attached", timeout=90000)
						await _load_all_classifieds_orders(page)

						page_sell_prices = await page.locator('[data-listing_intent="sell"]').evaluate_all(
							"elements => elements.map(e => e.getAttribute('data-listing_price'))"
						)

						page_min = None
						for pt in page_sell_prices:
							val, curr = parse_price(pt)
							if val is None:
								continue
							keys_val, ok = _to_keys_if_possible(val, curr, effective_key_ref)
							if not ok:
								continue
							if page_min is None or keys_val < page_min:
								page_min = keys_val
						if page_min is not None:
							if global_min_sell is None or page_min < global_min_sell:
								global_min_sell = page_min

						logger.info(f"[Arbitrage][BUY/P1] page={page_num}, page_min={page_min}, global_min={global_min_sell}")

						if len(page_sell_prices) <= prev_sell_count:
							break
						prev_sell_count = len(page_sell_prices)

						await asyncio.sleep(0.6)

					if global_min_sell is None:
						logger.warning(f"[Arbitrage] Нет пригодных SELL объявлений для {item} (keys/конверсия)")
						results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
						continue

					# PASS 2: ранний стоп — ищем buy < global_min_sell (в ключах; конвертируем ref при необходимости)
					best_buy = None
					for page_num in range(1, max_pages + 1):
						url = base_url if page_num == 1 else f"{base_url}&page={page_num}"
						logger.info(f"[Arbitrage][BUY/P2] URL → {url}")
						await page.goto(url, timeout=90000, wait_until="domcontentloaded")
						await asyncio.sleep(0.5)
						# На некоторых страницах могут отсутствовать buy, поэтому проверяем наличие
						has_buy = await page.locator('[data-listing_intent="buy"]').count()
						if has_buy == 0:
							logger.info(f"[Arbitrage][BUY/P2] page={page_num} buy=0")
							await asyncio.sleep(0.4)
							continue

						await page.locator('[data-listing_intent="buy"]').first.wait_for(state="attached", timeout=90000)
						await _load_all_classifieds_orders(page)

						page_buy_prices = await page.locator('[data-listing_intent="buy"]').evaluate_all(
							"elements => elements.map(e => e.getAttribute('data-listing_price'))"
						)

						candidates = []
						for pt in page_buy_prices:
							val, curr = parse_price(pt)
							if val is None:
								continue
							keys_val, ok = _to_keys_if_possible(val, curr, effective_key_ref)
							if ok and keys_val < global_min_sell:
								candidates.append(keys_val)

						if candidates:
							best_buy = max(candidates)
							logger.info(f"[Arbitrage][BUY/P2] Early stop on page {page_num}: buy={best_buy:.2f} keys < global min sell={global_min_sell:.2f}")
							break

						await asyncio.sleep(0.6)

					if best_buy is not None:
						results[item] = {"value": round(best_buy, 2), "currency": "keys", "source": "ClassifiedsVerified"}
					else:
						logger.warning(f"[Arbitrage] Не нашёл buy ниже глобального min sell для {item}")
						results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}

				else:
					logger.info(f"[Arbitrage] Загружаю {item} (sell) через stats...")

					quality = "Strange" if item.lower().startswith("strange ") else "Unique"
					item_name = item.replace("Strange ", "").strip()
					item_enc = quote(item_name, safe="")

					url = f"https://backpack.tf/stats/{quality}/{item_enc}/Tradable/Craftable"
					logger.info(f"[Arbitrage][SELL] URL → {url}")
					await page.goto(url, timeout=90000, wait_until="domcontentloaded")
					logger.info(f"[Arbitrage][SELL] At → {page.url}")

					selector = 'div.item[data-listing_intent="sell"]'
					await page.locator(selector).first.wait_for(state="attached", timeout=90000)

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
			browser = await p.chromium.launch(headless=False, args=["--no-sandbox"])
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
			await context.add_init_script(
				"Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
			)

			# Куки (если есть)
			if self.cookies_file.exists():
				try:
					raw = json.loads(self.cookies_file.read_text())
					norm = []
					for c in raw:
						c = dict(c)
						if "expires" in c and not isinstance(c.get("expires"), (int, float)):
							c.pop("expires")
						d = c.get("domain")
						if d and not d.startswith("."):
							c["domain"] = f".{d}"
						c.setdefault("sameSite", "Lax")
						norm.append(c)
					await context.add_cookies(norm)
					logger.info("[Arbitrage] Куки подгружены")
				except Exception as e:
					logger.error(f"[Arbitrage] Ошибка при загрузке куки: {e}")

			page = await context.new_page()
			await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

			# Прогрев через stats (и логин при необходимости)
			if self.sell_items:
				pref_item = self.sell_items[0]
			elif self.buy_items:
				pref_item = self.buy_items[0]
			else:
				pref_item = "Mann Co. Supply Crate Key"

			is_strange = pref_item.lower().startswith("strange ")
			quality_str = "Strange" if is_strange else "Unique"
			item_name = pref_item.replace("Strange ", "").strip()
			stats_warmup = f"https://backpack.tf/stats/{quality_str}/{quote(item_name, safe='')}/Tradable/Craftable"

			await page.goto(stats_warmup, timeout=90000, wait_until="domcontentloaded")
			if "steamcommunity.com/openid/login" in page.url or "/login" in page.url:
				logger.info("[Arbitrage][LOGIN] Выполни вход через Steam в открытом окне (после входа бот сам продолжит).")
				try:
					await page.wait_for_url("**backpack.tf/stats/**", timeout=180000)
				except Exception:
					logger.error("[Arbitrage][LOGIN] Не дождался возврата на stats после логина.")
				await page.goto(stats_warmup, timeout=90000, wait_until="domcontentloaded")

			# Сохраняем актуальные куки (best effort)
			try:
				sess_cookies = await context.cookies()
				for c in sess_cookies:
					if "expires" in c and not isinstance(c.get("expires"), (int, float)):
						c["expires"] = -1
				self.cookies_file.write_text(json.dumps(sess_cookies, indent=2))
			except Exception:
				pass

			# Основной цикл
			if self.sell_items:
				results["sell"] = await self.fetch_prices(page, self.sell_items, "sell")

			await asyncio.sleep(0.8)

			if self.buy_items:
				results["buy"] = await self.fetch_prices(page, self.buy_items, "buy")

			await browser.close()
		return results