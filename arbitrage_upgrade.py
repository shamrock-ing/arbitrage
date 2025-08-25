import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
from urllib.parse import quote, quote_plus
from config import KEY_PRICE_REF

logger = logging.getLogger("tf2-arbitrage")
UPGRADE_JSON_ALL = Path("upgrade_results_all.json")
UPGRADE_JSON_PROFITABLE = Path("upgrade_results_profitable.json")


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


ALIAS_BASE_NAMES = {
	"axe": "Fire Axe",
}


def parse_item_attributes(item_name: str):
	"""
	Разбирает название предмета и определяет его атрибуты:
	- quality: 6 (Unique), 11 (Strange)
	- killstreak_tier: 0 (обычный), 1 (Basic Killstreak), 2 (Specialized Killstreak), 3 (Professional Killstreak)
	- australium: True/False
	- base_name: базовое название без префиксов
	"""
	item_lower = item_name.lower()
	
	# Определяем качество
	is_strange = item_lower.startswith("strange ")
	quality = 11 if is_strange else 6
	
	# Определяем killstreak tier
	killstreak_tier = 0
	if "professional killstreak" in item_lower:
		killstreak_tier = 3
	elif "specialized killstreak" in item_lower:
		killstreak_tier = 2
	elif "killstreak" in item_lower:
		killstreak_tier = 1  # Basic Killstreak
	
	# Определяем australium
	australium = "australium" in item_lower
	
	# Убираем все префиксы для получения базового названия
	base_name = item_name
	if is_strange:
		base_name = base_name.replace("Strange ", "").strip()
	if "professional killstreak" in item_lower:
		base_name = base_name.replace("Professional Killstreak ", "").strip()
	elif "specialized killstreak" in item_lower:
		base_name = base_name.replace("Specialized Killstreak ", "").strip()
	elif "killstreak" in item_lower:
		base_name = base_name.replace("Killstreak ", "").strip()
	if australium:
		base_name = base_name.replace("Australium ", "").strip()

	# Нормализуем неоднозначные имена (алиасы)
	alias = ALIAS_BASE_NAMES.get(base_name.lower())
	if alias:
		base_name = alias
	
	return {
		"quality": quality,
		"killstreak_tier": killstreak_tier,
		"australium": australium,
		"base_name": base_name
	}


async def _load_all_classifieds_orders(page, max_scrolls=8):
	"""
	Оптимизированная подгрузка ордеров на странице classifieds.
	Уменьшено количество скроллов и задержки.
	"""
	await asyncio.sleep(0.3)  # Уменьшено с 0.5
	last_total = -1
	
	for i in range(max_scrolls):
		await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
		await asyncio.sleep(0.4)  # Уменьшено с 0.6
		
		total = await page.locator('[data-listing_intent="buy"], [data-listing_intent="sell"]').count()
		if total <= last_total:
			break
		last_total = total
		
		# Ранний выход если уже достаточно данных
		if total >= 20:
			break


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


def _to_ref(value: float | None, currency: str | None, key_price_ref: float | None) -> float:
	"""
	Конвертирует значение в ref. Возвращает 0.0 если невозможно.
	"""
	if value is None or currency is None:
		return 0.0
	if currency == "ref":
		return float(value)
	if currency in ("key", "keys") and key_price_ref:
		return float(value) * float(key_price_ref)
	return 0.0


KIT_COSTS_REF = {
	"specialized": 48.5,   # диапазон 47-50 ref, берём среднее
	"professional": 124.0, # 2 keys 20 ref при key≈52 → 124 ref
}


def _kit_item_name(base_item: str, kit_type: str) -> str:
	# Убираем возможный префикс Strange из base_item, чтобы не дублировать
	base_clean = base_item.strip()
	if base_clean.lower().startswith("strange "):
		base_clean = base_clean[8:].strip()
	if kit_type == "specialized":
		kit_name = "Specialized Killstreak"
	elif kit_type == "professional":
		kit_name = "Professional Killstreak"
	else:
		raise ValueError(f"Unknown kit_type: {kit_type}")
	return f"Strange {kit_name} {base_clean}"


async def _analyze_upgrades_for_items(self, page, base_items, key_price_ref: float | None, kit_types=("specialized", "professional")):
	"""
	Для каждого base_item проверяет условие sell_A + kit_cost < buy_B.
	Возвращает список словарей с результатами.
	"""
	results = []
	if not base_items:
		return results
	for base_item in base_items:
		# Получаем sell_A
		sell_data = await self.fetch_prices(page, [base_item], "sell")
		sell_entry = sell_data.get(base_item, {})
		sell_value = sell_entry.get("value")
		sell_currency = sell_entry.get("currency")
		sell_ref = _to_ref(sell_value, sell_currency, key_price_ref or KEY_PRICE_REF)
		if sell_ref <= 0:
			continue
		for kit in kit_types:
			try:
				kit_cost = KIT_COSTS_REF.get(kit)
				if not kit_cost:
					continue
				upgraded_name = _kit_item_name(base_item, kit)
				logger.info(f"[UpgradeCheck] Проверяю {upgraded_name} (buy)")
				buy_data = await self.fetch_prices(page, [upgraded_name], "buy")
				buy_entry = buy_data.get(upgraded_name, {})
				buy_value = buy_entry.get("value")
				buy_currency = buy_entry.get("currency")
				buy_ref = _to_ref(buy_value, buy_currency, key_price_ref or KEY_PRICE_REF)
				total_cost = sell_ref + kit_cost
				break_even_ref = total_cost
				break_even_keys = (break_even_ref / float(key_price_ref)) if key_price_ref else None
				profit_ref = buy_ref - total_cost
				profit_percent = (profit_ref / total_cost * 100.0) if total_cost > 0 else 0.0
				is_profitable = profit_ref > 0
				reason = None
				if buy_ref <= 0:
					reason = "no_verified_buy_below_min_sell"
				result = {
					"base_item": base_item,
					"kit_type": kit,
					"sell": {"value": sell_value, "currency": sell_currency, "ref": round(sell_ref, 2)},
					"kit_cost_ref": kit_cost,
					"upgraded_item": upgraded_name,
					"buy": {"value": buy_value, "currency": buy_currency, "ref": round(buy_ref, 2)},
					"total_cost_ref": round(total_cost, 2),
					"break_even": {"ref": round(break_even_ref, 2), "keys": round(break_even_keys, 2) if break_even_keys is not None else None},
					"profit": {"ref": round(profit_ref, 2), "percent": round(profit_percent, 2), "is_profitable": is_profitable},
					"reason": reason,
				}
				if is_profitable:
					logger.info(
						f"[UpgradeCheck] {base_item} + {kit}: PROFIT {profit_ref:+.2f} ref (sell={sell_ref:.2f} + kit={kit_cost:.2f} < buy={buy_ref:.2f})"
					)
				else:
					logger.info(
						f"[UpgradeCheck] {base_item} + {kit}: NO {profit_ref:+.2f} ref (sell={sell_ref:.2f} + kit={kit_cost:.2f} vs buy={buy_ref:.2f})"
					)
				results.append(result)
			except Exception as e:
				logger.error(f"[UpgradeCheck] Ошибка при анализе {base_item} + {kit}: {e}")
				results.append({
					"base_item": base_item,
					"kit_type": kit,
					"sell": {"value": sell_value, "currency": sell_currency, "ref": round(sell_ref, 2)},
					"kit_cost_ref": KIT_COSTS_REF.get(kit),
					"upgraded_item": _kit_item_name(base_item, kit),
					"buy": {"value": None, "currency": None, "ref": 0.0},
					"total_cost_ref": round(sell_ref + (KIT_COSTS_REF.get(kit) or 0.0), 2),
					"break_even": {"ref": round(sell_ref + (KIT_COSTS_REF.get(kit) or 0.0), 2), "keys": None},
					"profit": {"ref": - (KIT_COSTS_REF.get(kit) or 0.0), "percent": -100.0, "is_profitable": False},
					"reason": f"exception: {e}",
				})
	return results


def _is_closed_error(e: Exception) -> bool:
	msg = str(e).lower()
	return (
		"has been closed" in msg or
		"target page" in msg and "closed" in msg or
		"context" in msg and "closed" in msg or
		"browser" in msg and "closed" in msg
	)


class UpgradeArbitrage:
	def __init__(self):
		self.cookies_file = Path("cookies.json")
		self.config_file = Path("config.json")

		self.sell_items = []
		self.buy_items = []
		self.price_mode = "avg23"
		self.upgrade_items = []
		self.upgrade_kits = []  # allowed: "specialized", "professional"
		self.focus_upgrade = False
		self.cached_sell = {}
		self.cached_attributes = {}  # Кэш для парсинга атрибутов
		self.runtime_key_price_ref = None  # определяем динамически, если не задано в конфиге
		
		# Оптимизированные настройки
		self.delays = {
			"page_load": 0.4,      # Уменьшено с 0.5
			"between_requests": 0.4, # Уменьшено с 0.6
			"scroll": 0.4,          # Уменьшено с 0.6
			"retry": 0.2            # Новое - для retry
		}
		
		# Retry настройки
		self.max_retries = 2
		self.retry_delay = 1.0
		
		if self.config_file.exists():
			try:
				config = json.loads(self.config_file.read_text())
				self.sell_items = config.get("sell_items", [])
				self.buy_items = config.get("buy_items", [])
				self.price_mode = config.get("price_mode", "avg23")
				self.upgrade_items = config.get("upgrade_items", [])
				self.upgrade_kits = config.get("upgrade_kits", [])
				self.focus_upgrade = bool(config.get("focus_upgrade", False))
			except Exception as e:
				logger.error(f"[Arbitrage] Ошибка при загрузке config.json: {e}")

	def _get_cached_attributes(self, item_name: str):
		"""
		Получает атрибуты предмета из кэша или парсит заново
		"""
		if item_name not in self.cached_attributes:
			self.cached_attributes[item_name] = parse_item_attributes(item_name)
		return self.cached_attributes[item_name]
	
	async def _detect_key_price_ref(self, page) -> float | None:
		"""
		Пытается определить цену ключа в ref, если KEY_PRICE_REF не задан:
		идёт на stats ключа и берёт минимальный sell в ref.
		"""
		try:
			key_stats = "https://backpack.tf/stats/Unique/Mann%20Co.%20Supply%20Crate%20Key/Tradable/Craftable"
			await page.goto(key_stats, timeout=90000, wait_until="domcontentloaded")
			await page.locator('div.item[data-listing_intent="sell"]').first.wait_for(state="attached", timeout=90000)
			await asyncio.sleep(self.delays["page_load"])
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
			# Оптимизированная пауза между запросами
			await asyncio.sleep(self.delays["between_requests"])
			
			# Retry логика для обработки ошибок
			for retry in range(self.max_retries + 1):
				try:
					if intent == "buy":
						logger.info(f"[Arbitrage] Загружаю {item} (buy) через classifieds (scraping only)...")

						# Парсим атрибуты предмета (с кэшированием)
						item_attrs = self._get_cached_attributes(item)
						logger.info(f"[Arbitrage][BUY] Атрибуты {item}: quality={item_attrs['quality']}, killstreak_tier={item_attrs['killstreak_tier']}, australium={item_attrs['australium']}, base_name='{item_attrs['base_name']}'")
						item_enc = quote_plus(item_attrs["base_name"])  # classifieds prefer '+' for spaces
						
						# Определяем параметр australium
						australium_param = "1" if item_attrs["australium"] else "-1"
						
						base_url = (
							f"https://backpack.tf/classifieds?item={item_enc}"
							f"&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={australium_param}&killstreak_tier={item_attrs['killstreak_tier']}"
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
							await asyncio.sleep(self.delays["page_load"])
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

							await asyncio.sleep(self.delays["between_requests"])

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
							await asyncio.sleep(self.delays["page_load"])
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
							
							await asyncio.sleep(self.delays["between_requests"])

						if best_buy is not None:
							results[item] = {"value": round(best_buy, 2), "currency": "keys", "source": "ClassifiedsVerified"}
						else:
							logger.warning(f"[Arbitrage] Не нашёл buy ниже глобального min sell для {item}")
							results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}

					else:
						logger.info(f"[Arbitrage] Загружаю {item} (sell)...")

						# Парсим атрибуты предмета (с кэшированием)
						item_attrs = self._get_cached_attributes(item)
						logger.info(f"[Arbitrage][SELL] Атрибуты {item}: quality={item_attrs['quality']}, killstreak_tier={item_attrs['killstreak_tier']}, australium={item_attrs['australium']}, base_name='{item_attrs['base_name']}'")
						
						# Определяем, нужно ли использовать classifieds вместо stats
						use_classifieds = item_attrs["killstreak_tier"] > 0 or item_attrs["australium"]
						
						if use_classifieds:
							logger.info(f"[Arbitrage] Используем classifieds для {item} (сложные атрибуты)")
							
							# Используем classifieds для sell (как для buy)
							item_enc = quote_plus(item_attrs["base_name"])  # classifieds prefer '+' for spaces
							australium_param = "1" if item_attrs["australium"] else "-1"
							
							base_url = (
								f"https://backpack.tf/classifieds?item={item_enc}"
								f"&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={australium_param}&killstreak_tier={item_attrs['killstreak_tier']}"
							)
							
							# Получаем sell цены через classifieds
							url = base_url
							logger.info(f"[Arbitrage][SELL] Classifieds URL → {url}")
							await page.goto(url, timeout=90000, wait_until="domcontentloaded")
							await asyncio.sleep(self.delays["page_load"])
							await page.locator('[data-listing_intent="sell"]').first.wait_for(state="attached", timeout=90000)
							await _load_all_classifieds_orders(page)
							
							sell_prices = await page.locator('[data-listing_intent="sell"]').evaluate_all(
								"elements => elements.map(e => e.getAttribute('data-listing_price'))"
							)
							
							logger.info(f"[DEBUG] Нашёл {len(sell_prices)} sell объявлений в classifieds для {item}: {sell_prices}")
							
							if self.price_mode == "first":
								price_texts = sell_prices[:1]
							elif self.price_mode == "avg23" and len(sell_prices) >= 3:
								price_texts = sell_prices[1:3]
							else:
								price_texts = sell_prices[:1]
							
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
								source = "ClassifiedsSell"
								logger.info(f"[Arbitrage] Цена {item} (sell): {price_text} ({source})")
								results[item] = {
									"value": rounded_value,
									"currency": currency,
									"source": source
								}
							else:
								raise Exception("Не удалось разобрать цены из classifieds")
						
						else:
							logger.info(f"[Arbitrage] Используем stats для {item} (простые атрибуты)")
							
							# Используем stats для простых предметов
							if item_attrs["quality"] == 11:
								quality_str = "Strange"
							else:
								quality_str = "Unique"
							
							item_enc = quote(item_attrs["base_name"], safe="")
							url = f"https://backpack.tf/stats/{quality_str}/{item_enc}/Tradable/Craftable"
							
							logger.info(f"[Arbitrage][SELL] Stats URL → {url}")
							await page.goto(url, timeout=90000, wait_until="domcontentloaded")
							logger.info(f"[Arbitrage][SELL] At → {page.url}")

							# Если предмета нет на stats, пробуем через classifieds
							not_exist = await page.locator("text=This item does not seem to exist").count()
							if not_exist and not_exist > 0:
								logger.warning(f"[Arbitrage][SELL] Stats сообщает: 'This item does not seem to exist.' — переключаюсь на classifieds для {item}")
								item_enc_f = quote_plus(item_attrs["base_name"])  # classifieds prefer '+' for spaces
								australium_param_f = "1" if item_attrs["australium"] else "-1"
								class_url = (
									f"https://backpack.tf/classifieds?item={item_enc_f}"
									f"&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={australium_param_f}&killstreak_tier={item_attrs['killstreak_tier']}"
								)
								logger.info(f"[Arbitrage][SELL] Fallback Classifieds URL → {class_url}")
								await page.goto(class_url, timeout=90000, wait_until="domcontentloaded")
								await asyncio.sleep(self.delays["page_load"])
								cnt = await page.locator('[data-listing_intent="sell"]').count()
								if cnt > 0:
									await _load_all_classifieds_orders(page)
									sell_prices_fb = await page.locator('[data-listing_intent="sell"]').evaluate_all(
										"elements => elements.map(e => e.getAttribute('data-listing_price'))"
									)
									logger.info(f"[DEBUG] (FB) Нашёл {len(sell_prices_fb)} sell объявлений для {item}: {sell_prices_fb}")
									price_texts = sell_prices_fb[:1] if sell_prices_fb else []
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
										results[item] = {"value": rounded_value, "currency": currency, "source": "ClassifiedsSellFB"}
										logger.info(f"[Arbitrage] (FB) Цена {item} (sell): {rounded_value} {currency} (ClassifiedsSellFB)")
										# Переходим к следующему предмету
										break
								else:
									logger.warning(f"[Arbitrage][SELL] (FB) Нет sell объявлений для {item}")
									results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
									break
 
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

					# Если успешно обработали, выходим из retry цикла
					break
					
				except Exception as e:
					# Если контекст/страница закрыты — создаём новую страницу и пробуем ещё раз
					if _is_closed_error(e):
						logger.warning(f"[Arbitrage] Страница/контекст закрыты. Пересоздаю страницу и повторяю: {item}")
						try:
							# Пересоздаём новую страницу из текущего контекста
							ctx = page.context
							page = await ctx.new_page()
							await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
							continue
						except Exception as e2:
							logger.error(f"[Arbitrage] Не удалось пересоздать страницу: {e2}")
					# Обычный retry
					if retry < self.max_retries:
						logger.warning(f"[Arbitrage] Попытка {retry + 1} для {item} не удалась: {e}")
						await asyncio.sleep(self.delays["retry"])
						continue
					# Финальный фолбек: пробуем classifieds при провале stats или suggested
					logger.error(f"[Arbitrage] Все попытки для {item} не удались: {e}")
					try:
						item_attrs = self._get_cached_attributes(item)
						item_enc = quote_plus(item_attrs["base_name"])  # classifieds prefer '+' for spaces
						alt_url = (
							f"https://backpack.tf/classifieds?item={item_enc}"
							f"&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={'1' if item_attrs['australium'] else '-1'}&killstreak_tier={item_attrs['killstreak_tier']}"
						)
						await page.goto(alt_url, timeout=90000, wait_until="domcontentloaded")
					except Exception:
						results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
				except Exception:
					results[item] = {"value": 0.0, "currency": "unknown", "source": "None"}
		return results
	
	async def run(self):
		start_time = asyncio.get_event_loop().time()
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
			
			# Оптимизация производительности страницы
			await page.add_init_script("""
				// Отключаем ненужные функции для ускорения
				Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
				Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
				Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
			""")

			# Прогрев через stats (и логин при необходимости)
			if self.sell_items:
				pref_item = self.sell_items[0]
			elif self.buy_items:
				pref_item = self.buy_items[0]
			else:
				pref_item = "Mann Co. Supply Crate Key"

			# Парсим атрибуты предмета для прогрева (с кэшированием)
			pref_attrs = self._get_cached_attributes(pref_item)
			quality_str = "Strange" if pref_attrs["quality"] == 11 else "Unique"
			item_name = pref_attrs["base_name"]
			
			# Строим URL прогрева с учётом всех атрибутов
			base_warmup = f"https://backpack.tf/stats/{quality_str}/{quote(item_name, safe='')}/Tradable/Craftable"
			
			# Для australium предметов добавляем /Australium
			if pref_attrs["australium"]:
				base_warmup += "/Australium"
			
			# Для killstreak предметов добавляем killstreak_tier параметр
			killstreak_param = ""
			if pref_attrs["killstreak_tier"] > 0:
				killstreak_param = f"&killstreak_tier={pref_attrs['killstreak_tier']}"
			
			stats_warmup = base_warmup + killstreak_param

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
			if not self.focus_upgrade:
				if self.sell_items:
					results["sell"] = await self.fetch_prices(page, self.sell_items, "sell")

				await asyncio.sleep(self.delays["between_requests"])

				if self.buy_items:
					results["buy"] = await self.fetch_prices(page, self.buy_items, "buy")

			# Анализ апгрейдов: sell_A + kit < buy_B
			try:
				# Обеспечим наличие цены ключа
				if not self.runtime_key_price_ref:
					try:
						self.runtime_key_price_ref = await self._detect_key_price_ref(page)
					except Exception:
						self.runtime_key_price_ref = None
				key_ref = self.runtime_key_price_ref or 52.0
				# Определяем набор предметов и типов китов для апгрейда
				upgrade_items = self.upgrade_items if self.upgrade_items else (list(set(self.sell_items)) or list(set(self.buy_items)))
				kit_types = tuple([k for k in self.upgrade_kits if k in ("specialized", "professional")]) or ("specialized", "professional")
				logger.info(f"[UpgradeCheck] Базовые предметы для апгрейда: {upgrade_items}")
				logger.info(f"[UpgradeCheck] Типы китов: {list(kit_types)}")
				upgrade_results = await _analyze_upgrades_for_items(self, page, upgrade_items, key_ref, kit_types)
				# Сводка
				profitable = [r for r in upgrade_results if r["profit"]["is_profitable"]]
				profitable.sort(key=lambda r: r["profit"]["ref"], reverse=True)
				if profitable:
					logger.info("[UpgradeCheck] ==== ТОП ВЫГОДНЫХ АПГРЕЙДОВ ====")
					for r in profitable:
						logger.info(
							f"[UpgradeCheck] {r['base_item']} + {r['kit_type']}: +{r['profit']['ref']:.2f} ref (ROI {r['profit']['percent']:.1f}%)"
						)
				else:
					logger.info("[UpgradeCheck] Выгодных апгрейдов не найдено")
				results["upgrade_opportunities"] = upgrade_results
				# Полная таблица (все сделки)
				_print_upgrade_table(upgrade_results)
				# Сохраняем JSON
				_save_upgrade_results_json(upgrade_results)
			except Exception as e:
				logger.error(f"[UpgradeCheck] Ошибка в анализе апгрейдов: {e}")
			
			await browser.close()
			
			# Статистика производительности
			total_time = asyncio.get_event_loop().time() - start_time
			total_items = len(self.sell_items) + len(self.buy_items)
			avg_time_per_item = total_time / total_items if total_items > 0 else 0
		
			logger.info(f"[Arbitrage] Статистика: общее время={total_time:.2f}с, предметов={total_items}, среднее время на предмет={avg_time_per_item:.2f}с")
		return results


# Тестовая функция для проверки парсинга атрибутов
def test_parse_item_attributes():
	"""
	Тестирует функцию parse_item_attributes для различных названий предметов
	"""
	test_items = [
		"Rocket Launcher",
		"Strange Rocket Launcher", 
		"Killstreak Rocket Launcher",
		"Strange Killstreak Rocket Launcher",
		"Specialized Killstreak Rocket Launcher",
		"Strange Specialized Killstreak Rocket Launcher",
		"Professional Killstreak Rocket Launcher",
		"Strange Professional Killstreak Rocket Launcher",
		"Australium Rocket Launcher",
		"Strange Australium Rocket Launcher",
		"Strange Specialized Killstreak Australium Rocket Launcher",
		"Strange Professional Killstreak Australium Rocket Launcher"
	]
	
	print("=== Тест парсинга атрибутов ===")
	for item in test_items:
		attrs = parse_item_attributes(item)
		print(f"{item:50} → quality={attrs['quality']}, killstreak_tier={attrs['killstreak_tier']}, australium={attrs['australium']}, base_name='{attrs['base_name']}'")


def _format_ref(v: float | None) -> str:
	try:
		return f"{float(v):.2f}"
	except Exception:
		return "-"


def _print_upgrade_table(upgrade_results: list[dict]):
	"""
	Выводит таблицу всех апгрейд-сделок, отсортированную по прибыли (ref) по убыванию.
	Помечает выгодные сделки.
	"""
	if not upgrade_results:
		print("\n=== Upgrade сделки ===\nНет данных")
		return
	rows = sorted(upgrade_results, key=lambda r: r.get("profit", {}).get("ref", 0.0), reverse=True)
	print("\n=== Upgrade сделки (все) ===")
	print("base_item | kit | sell_ref | kit_ref | buy_ref | total_ref | BE(keys) | profit_ref | ROI | status")
	print("-" * 96)
	for r in rows:
		base_item = r.get("base_item")
		kit = r.get("kit_type")
		sell_ref = _format_ref(r.get("sell", {}).get("ref"))
		kit_ref = _format_ref(r.get("kit_cost_ref"))
		buy_ref = _format_ref(r.get("buy", {}).get("ref"))
		total_ref = _format_ref(r.get("total_cost_ref"))
		profit_ref = _format_ref(r.get("profit", {}).get("ref"))
		roi = r.get("profit", {}).get("percent")
		roi_str = f"{roi:.1f}%" if isinstance(roi, (int, float)) else "-"
		status = "✅" if r.get("profit", {}).get("is_profitable") else "—"
		be_keys = r.get("break_even", {}).get("keys")
		be_keys_str = f"{be_keys:.2f}" if isinstance(be_keys, (int, float)) else "-"
		print(f"{base_item} | {kit} | {sell_ref} | {kit_ref} | {buy_ref} | {total_ref} | {be_keys_str} | {profit_ref} | {roi_str} | {status}")


def _save_upgrade_results_json(upgrade_results: list[dict]):
	try:
		UPGRADE_JSON_ALL.write_text(json.dumps(upgrade_results, ensure_ascii=False, indent=2), encoding="utf-8")
	except Exception as e:
		logger.error(f"[UpgradeCheck] Не удалось сохранить все результаты: {e}")
	try:
		profitable = [r for r in upgrade_results if r.get("profit", {}).get("is_profitable")]
		UPGRADE_JSON_PROFITABLE.write_text(json.dumps(profitable, ensure_ascii=False, indent=2), encoding="utf-8")
	except Exception as e:
		logger.error(f"[UpgradeCheck] Не удалось сохранить прибыльные результаты: {e}")


if __name__ == "__main__":
	test_parse_item_attributes()