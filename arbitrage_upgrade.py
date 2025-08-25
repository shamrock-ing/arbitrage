import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# Константы
KEY_PRICE_REF = 52.0  # Цена ключа в ref (по умолчанию)
COOKIES_FILE = Path("cookies.json")

# Настройка логирования
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - [%(levelname)s]: %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def parse_price(text: str) -> Tuple[float, str]:
	"""
	Парсит цену из текста и возвращает (значение, валюта)
	Поддерживает ref, keys, и комбинированные цены
	"""
	text = text.strip().lower()
	
	# Паттерны для разных валют
	ref_pattern = r'(\d+(?:\.\d+)?)\s*ref'
	key_pattern = r'(\d+(?:\.\d+)?)\s*key'
	combined_pattern = r'(\d+(?:\.\d+)?)\s*keys?[,\s]+(\d+(?:\.\d+)?)\s*ref'
	
	# Проверяем комбинированную цену (keys + ref)
	combined_match = re.search(combined_pattern, text)
	if combined_match:
		keys = float(combined_match.group(1))
		ref = float(combined_match.group(2))
		return keys, "keys"  # Возвращаем только keys, ref будет добавлен
	
	# Проверяем только keys
	key_match = re.search(key_pattern, text)
	if key_match:
		return float(key_match.group(1)), "keys"
	
	# Проверяем только ref
	ref_match = re.search(ref_pattern, text)
	if ref_match:
		return float(ref_match.group(1)), "ref"
	
	# Если ничего не найдено
	return 0.0, "unknown"


def _price_text_to_ref(text: str, key_price_ref: float) -> Optional[float]:
	"""
	Конвертирует текст цены (включая комбинированные варианты вроде "1 key, 20 ref") в ref.
	Возвращает None если распарсить не удалось.
	"""
	if not text:
		return None
	try:
		raw = text.replace("~", "").lower().strip().replace(",", "")
		# Форматы:
		# - "40.11 ref"
		# - "2 keys" или "1 key"
		# - "1 key 6.11 ref"
		# - "1 key 6.11 ref each" и т.п.
		ref_match = re.search(r"(\d+(?:\.\d+)?)\s*ref", raw)
		key_match = re.search(r"(\d+(?:\.\d+)?)\s*keys?", raw)
		ref_val = float(ref_match.group(1)) if ref_match else 0.0
		key_val = float(key_match.group(1)) if key_match else 0.0
		if not ref_match and not key_match:
			return None
		return ref_val + key_val * float(key_price_ref)
	except Exception:
		return None


def parse_item_attributes(item_name: str) -> Dict[str, Union[int, bool, str]]:
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
	
	return {
		"quality": quality,
		"killstreak_tier": killstreak_tier,
		"australium": australium,
		"base_name": base_name
	}


def _to_keys_if_possible(value: float, currency: str, key_price_ref: float) -> Tuple[float, str]:
	"""
	Конвертирует цену в keys если это выгодно, иначе оставляет в ref
	"""
	if currency == "ref" and value >= key_price_ref:
		keys = value / key_price_ref
		if keys >= 0.5:  # Конвертируем только если получится 0.5+ keys
			return keys, "keys"
	return value, currency


class UpgradeArbitrage:
	"""
	Класс для анализа арбитража и рентабельности апгрейдов в TF2
	"""
	
	def __init__(self):
		self.runtime_key_price_ref = None
		self.cached_attributes = {}
		self.delays = {
			"page_load": 2.0,
			"scroll": 0.5,
			"retry": 1.0
		}
		self.max_retries = 3
		self.retry_delay = 1.0
		
		# Загружаем конфигурацию
		try:
			with open("config.json", "r", encoding="utf-8") as f:
				config = json.load(f)
				self.sell_items = config.get("sell_items", [])
				self.buy_items = config.get("buy_items", [])
				self.price_mode = config.get("price_mode", "avg23")
				self.upgrade_analysis_items = config.get("upgrade_analysis_items", [])
		except Exception as e:
			logger.error(f"Ошибка загрузки конфига: {e}")
			self.sell_items = []
			self.buy_items = []
			self.price_mode = "avg23"
			self.upgrade_analysis_items = []
	
	def _get_cached_attributes(self, item_name: str) -> Dict[str, Union[int, bool, str]]:
		"""Кэширует атрибуты предметов для оптимизации"""
		if item_name not in self.cached_attributes:
			self.cached_attributes[item_name] = parse_item_attributes(item_name)
		return self.cached_attributes[item_name]
	
	async def _detect_key_price_ref(self, page: Page) -> float:
		"""Определяет актуальную цену ключа в ref"""
		try:
			# Ищем цену ключа на странице
			key_price_text = await page.locator("text=~\\d+\\.\\d+ ref").first.text_content()
			if key_price_text:
				match = re.search(r'(\d+\.\d+)', key_price_text)
				if match:
					price = float(match.group(1))
					logger.info(f"[Arbitrage] Обнаружена цена ключа: ~{price:.2f} ref")
					return price
		except Exception as e:
			logger.debug(f"Не удалось определить цену ключа: {e}")
		
		return KEY_PRICE_REF
	
	async def _load_all_classifieds_orders(self, page: Page, max_scrolls: int = 8) -> List[Dict]:
		"""
		Загружает все buy/sell объявления с classifieds страницы
		Оптимизировано для производительности
		"""
		orders = []
		
		try:
			# Ждём загрузки страницы
			await page.wait_for_load_state("networkidle", timeout=10000)
			
			# Автоматический скролл для загрузки всех объявлений
			for scroll in range(max_scrolls):
				# Скроллим вниз
				await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
				await asyncio.sleep(self.delays["scroll"])
				
				# Получаем текущие объявления
				current_orders = await page.locator("[data-listing_intent=\"sell\"], [data-listing_intent=\"buy\"]").all()
				
				# Если количество объявлений не изменилось, прекращаем скролл
				if len(current_orders) == len(orders):
					break
				
				orders = current_orders
				
				# Ограничиваем максимальное количество скроллов
				if scroll >= max_scrolls - 1:
					break
			
			logger.debug(f"Загружено {len(orders)} объявлений после {max_scrolls} скроллов")
			
		except Exception as e:
			logger.warning(f"Ошибка при загрузке объявлений: {e}")
		
		return orders
	
	async def fetch_prices(self, page: Page, items: List[str], intent: str) -> Dict[str, Dict]:
		"""
		Получает цены для списка предметов
		Поддерживает retry логику и оптимизированные задержки
		"""
		results = {}
		
		for item in items:
			logger.info(f"[Arbitrage] Загружаю {item} ({intent})...")
			
			# Получаем атрибуты предмета
			item_attrs = self._get_cached_attributes(item)
			logger.info(f"[Arbitrage][{intent.upper()}] Атрибуты {item}: quality={item_attrs['quality']}, killstreak_tier={item_attrs['killstreak_tier']}, australium={item_attrs['australium']}, base_name='{item_attrs['base_name']}'")
			
			# Определяем, использовать ли classifieds или stats
			use_classifieds = False
			if intent == "sell":
				# Для sell используем classifieds если есть сложные атрибуты
				if item_attrs["killstreak_tier"] > 0 or item_attrs["australium"]:
					use_classifieds = True
					logger.info(f"[Arbitrage] Используем classifieds для {item} (сложные атрибуты)")
				else:
					logger.info(f"[Arbitrage] Используем stats для {item} (простые атрибуты)")
			
			# Retry логика
			for attempt in range(self.max_retries):
				try:
					if intent == "buy":
						# Для buy всегда используем classifieds
						url = f"https://backpack.tf/classifieds?item={item_attrs['base_name'].replace(' ', '%20')}&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={1 if item_attrs['australium'] else -1}&killstreak_tier={item_attrs['killstreak_tier'] if item_attrs['killstreak_tier'] > 0 else -1}"
						
						logger.info(f"[Arbitrage][BUY] Classifieds URL → {url}")
						
						await page.goto(url, timeout=30000, wait_until="domcontentloaded")
						
						# Определяем цену ключа если нужно
						if not self.runtime_key_price_ref:
							self.runtime_key_price_ref = await self._detect_key_price_ref(page)
						
						effective_key_ref = self.runtime_key_price_ref or KEY_PRICE_REF
						
						# PASS 1: Ищем минимальную цену sell
						global_min_sell = float('inf')
						page_num = 1
						
						while page_num <= 2:  # Ограничиваем 2 страницами для производительности
							page_url = f"{url}&page={page_num}" if page_num > 1 else url
							logger.info(f"[Arbitrage][BUY/P1] URL → {page_url}")
							
							if page_num > 1:
								await page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
							
							# Ждём загрузки объявлений
							await page.wait_for_selector("[data-listing_intent=\"sell\"], [data-listing_intent=\"buy\"]", timeout=10000)
							
							# Получаем все объявления на странице
							sell_orders = await page.locator("[data-listing_intent=\"sell\"]").all()
							
							if not sell_orders:
								break
							
							# Парсим цены sell объявлений
							page_min_sell = float('inf')
							for order in sell_orders:
								try:
									price_text = await order.locator(".price").text_content()
									if price_text:
										price_value, price_currency = parse_price(price_text)
										if price_currency == "ref":
											page_min_sell = min(page_min_sell, price_value)
										elif price_currency == "keys":
											price_ref = price_value * effective_key_ref
											page_min_sell = min(page_min_sell, price_ref)
								except Exception as e:
									continue
							
							if page_min_sell != float('inf'):
								global_min_sell = min(global_min_sell, page_min_sell)
								logger.info(f"[Arbitrage][BUY/P1] page={page_num}, page_min={page_min_sell}, global_min={global_min_sell}")
							
							page_num += 1
						
						# PASS 2: Ищем максимальную цену buy
						best_buy = 0
						page_num = 1
						
						while page_num <= 2:
							page_url = f"{url}&page={page_num}" if page_num > 1 else url
							logger.info(f"[Arbitrage][BUY/P2] URL → {page_url}")
							
							if page_num > 1:
								await page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
							
							await page.wait_for_selector("[data-listing_intent=\"sell\"], [data-listing_intent=\"buy\"]", timeout=10000)
							
							buy_orders = await page.locator("[data-listing_intent=\"buy\"]").all()
							
							if not buy_orders:
								break
							
							# Парсим цены buy объявлений
							for order in buy_orders:
								try:
									price_text = await order.locator(".price").text_content()
									if price_text:
										price_value, price_currency = parse_price(price_text)
										if price_currency == "ref":
											best_buy = max(best_buy, price_value)
										elif price_currency == "keys":
											price_ref = price_value * effective_key_ref
											best_buy = max(best_buy, price_ref)
								except Exception as e:
									continue
							
							# Early stop если нашли выгодную цену
							if best_buy > 0 and best_buy < global_min_sell:
								logger.info(f"[Arbitrage][BUY/P2] Early stop on page {page_num}: buy={best_buy:.2f} ref < global min sell={global_min_sell:.2f} ref")
								break
							
							page_num += 1
						
						# Конвертируем в keys если выгодно
						final_price, final_currency = _to_keys_if_possible(best_buy, "ref", effective_key_ref)
						
						results[item] = {
							"value": final_price,
							"currency": final_currency,
							"ref": best_buy,
							"source": "ClassifiedsVerified"
						}
						
						logger.info(f"[Arbitrage] Цена {item} (buy): {final_price} {final_currency} ({results[item]['source']})")
					
					elif intent == "sell":
						if use_classifieds:
							# Используем classifieds для сложных предметов
							url = f"https://backpack.tf/classifieds?item={item_attrs['base_name'].replace(' ', '%20')}&quality={item_attrs['quality']}&tradable=1&craftable=1&australium={1 if item_attrs['australium'] else -1}&killstreak_tier={item_attrs['killstreak_tier'] if item_attrs['killstreak_tier'] > 0 else -1}"
							
							logger.info(f"[Arbitrage][SELL] Classifieds URL → {url}")
							
							await page.goto(url, timeout=30000, wait_until="domcontentloaded")
							await page.wait_for_selector("[data-listing_intent=\"sell\"], [data-listing_intent=\"buy\"]", timeout=10000)
							
							# Получаем sell объявления
							sell_orders = await page.locator("[data-listing_intent=\"sell\"]").all()
							
							if sell_orders:
								# Парсим цены
								prices = []
								for order in sell_orders[:10]:  # Ограничиваем 10 объявлениями
									try:
										price_text = await order.locator(".price").text_content()
										if price_text:
											price_value, price_currency = parse_price(price_text)
											if price_currency == "ref":
												prices.append(f"{price_value} ref")
											elif price_currency == "keys":
												prices.append(f"{price_value} keys")
											elif price_currency == "unknown":
												prices.append(price_text)
									except Exception as e:
										continue
								
								if prices:
									logger.debug(f"Нашёл {len(prices)} sell объявлений в classifieds для {item}: {prices}")
									
									# Берём минимальную цену для sell
									min_price = min(prices, key=lambda x: parse_price(x)[0])
									price_value, price_currency = parse_price(min_price)
									
									# Конвертируем в ref для расчётов
									if price_currency == "keys":
										key_price = self.runtime_key_price_ref or KEY_PRICE_REF
										price_ref = price_value * key_price
									else:
										price_ref = price_value
									
									results[item] = {
										"value": price_value,
										"currency": price_currency,
										"ref": price_ref,
										"source": "ClassifiedsSell"
									}
									
									logger.info(f"[Arbitrage] Цена {item} (sell): {price_value} {price_currency} ({results[item]['source']})")
									break
						
						else:
							# Используем stats для простых предметов
							quality_path = "Strange" if item_attrs["quality"] == 11 else "Unique"
							australium_path = "/Australium" if item_attrs["australium"] else ""
							
							url = f"https://backpack.tf/stats/{quality_path}/{item_attrs['base_name'].replace(' ', '%20')}/Tradable/Craftable{australium_path}"
							
							logger.info(f"[Arbitrage][SELL] Stats URL → {url}")
							
							await page.goto(url, timeout=30000, wait_until="domcontentloaded")
							logger.info(f"[Arbitrage][SELL] At → {url}")
							
							# Ждём загрузки цен
							await asyncio.sleep(self.delays["page_load"])
							
							# Получаем цены из stats
							price_elements = await page.locator(".price").all()
							
							if price_elements:
								prices = []
								for elem in price_elements[:5]:  # Берём первые 5 цен
									try:
										price_text = await elem.text_content()
										if price_text:
											prices.append(price_text)
									except Exception as e:
										continue
								
								if prices:
									logger.debug(f"Нашёл {len(prices)} объявлений для {item} (sell): {prices}")
									
									# Парсим минимальную цену
									min_price = min(prices, key=lambda x: parse_price(x)[0])
									price_value, price_currency = parse_price(min_price)
									
									# Конвертируем в ref
									if price_currency == "keys":
										key_price = self.runtime_key_price_ref or KEY_PRICE_REF
										price_ref = price_value * key_price
									else:
										price_ref = price_value
									
									results[item] = {
										"value": price_value,
										"currency": price_currency,
										"ref": price_ref,
										"source": "SELLOrders"
									}
									
									logger.info(f"[Arbitrage] Цена {item} (sell): {price_value} {price_currency} ({results[item]['source']})")
									break
					
					# Если успешно получили цену, выходим из retry цикла
					if item in results:
						break
					
				except Exception as e:
					logger.warning(f"[Arbitrage] Попытка {attempt + 1} для {item} не удалась: {e}")
					if attempt < self.max_retries - 1:
						await asyncio.sleep(self.retry_delay)
					else:
						logger.error(f"[Arbitrage] Все попытки для {item} не удались: {e}")
			
			# Если не удалось получить цену
			if item not in results:
				results[item] = {
					"value": 0.0,
					"currency": "unknown",
					"ref": 0.0,
					"source": "Failed"
				}
				logger.warning(f"[Arbitrage] Не удалось получить цену для {item}")
			
			# Небольшая задержка между запросами
			await asyncio.sleep(self.delays["page_load"])
		
		return results
	
	async def analyze_upgrade_profitability(self, page: Page, base_item: str, kit_type: str = "specialized"):
		"""
		Анализирует рентабельность апгрейда базового предмета с killstreak kit
		
		Args:
			page: Playwright page
			base_item: базовый предмет (например, "Rocket Launcher")
			kit_type: тип кита ("specialized" или "professional")
		
		Returns:
			dict с результатами анализа
		"""
		logger.info(f"[Upgrade Analysis] Анализирую рентабельность апгрейда {base_item} с {kit_type} killstreak kit")
		
		# Определяем информацию о ките
		kit_info = {
			"specialized": {
				"name": "Specialized Killstreak Kit",
				"killstreak_tier": 2,
				"price_range": "47-50 ref",
				"avg_price_ref": 48.5
			},
			"professional": {
				"name": "Professional Killstreak Kit", 
				"killstreak_tier": 3,
				"price_range": "2 keys 20 ref",
				"avg_price_ref": 124.0  # 2 keys * 52 ref + 20 ref = 104 + 20 = 124 ref
			}
		}
		
		if kit_type not in kit_info:
			logger.error(f"[Upgrade Analysis] Неподдерживаемый тип кита: {kit_type}")
			return None
		
		kit = kit_info[kit_type]
		
		try:
			# 1. Получаем цену базового предмета
			logger.info(f"[Upgrade Analysis] Получаю цену базового предмета: {base_item}")
			base_price_data = await self.fetch_prices(page, [base_item], "sell")
			base_price = base_price_data.get(base_item, {})
			
			if not base_price or base_price.get("value", 0) == 0:
				logger.warning(f"[Upgrade Analysis] Не удалось получить цену для базового предмета {base_item}")
				return None
			
			base_price_ref = base_price.get("ref", 0)
			logger.info(f"[Upgrade Analysis] Базовая цена {base_item}: {base_price.get('value')} {base_price.get('currency')} = {base_price_ref} ref")
			
			# 2. Получаем цену апгрейднутого предмета
			# Формируем название апгрейднутого предмета
			upgraded_item_name = f"Strange {kit['name']} {base_item}"
			logger.info(f"[Upgrade Analysis] Получаю цену апгрейднутого предмета: {upgraded_item_name}")
			
			upgraded_price_data = await self.fetch_prices(page, [upgraded_item_name], "buy")
			upgraded_price = upgraded_price_data.get(upgraded_item_name, {})
			
			if not upgraded_price or upgraded_price.get("value", 0) == 0:
				logger.warning(f"[Upgrade Analysis] Не удалось получить цену для апгрейднутого предмета {upgraded_item_name}")
				return None
			
			upgraded_price_ref = upgraded_price.get("ref", 0)
			logger.info(f"[Upgrade Analysis] Цена апгрейда {upgraded_item_name}: {upgraded_price.get('value')} {upgraded_price.get('currency')} = {upgraded_price_ref} ref")
			
			# 3. Рассчитываем общую стоимость апгрейда
			kit_cost_ref = kit["avg_price_ref"]
			total_cost = base_price_ref + kit_cost_ref
			
			# 4. Рассчитываем прибыль/убыток
			profit_ref = upgraded_price_ref - total_cost
			profit_percent = (profit_ref / total_cost * 100) if total_cost > 0 else 0
			is_profitable = profit_ref > 0
			
			# 5. Формируем результат
			result = {
				"base_item": base_item,
				"kit_type": kit_type,
				"kit_name": kit["name"],
				"base_price": base_price,
				"kit_cost": {"ref": kit_cost_ref, "range": kit["price_range"]},
				"upgraded_item": upgraded_item_name,
				"upgraded_price": upgraded_price,
				"total_cost": total_cost,
				"profit": {
					"ref": profit_ref,
					"percent": profit_percent,
					"is_profitable": is_profitable
				},
				"analysis": {
					"recommendation": "ПРИБЫЛЬНО" if is_profitable else "УБЫТОЧНО",
					"roi": f"{profit_percent:.1f}%",
					"break_even": f"{(base_price_ref / total_cost * 100):.1f}%" if total_cost > 0 else "0%"
				}
			}
			
			# 6. Логируем результат
			logger.info(f"[Upgrade Analysis] === РЕЗУЛЬТАТ АНАЛИЗА АПГРЕЙДА ===")
			logger.info(f"[Upgrade Analysis] Предмет: {base_item}")
			logger.info(f"[Upgrade Analysis] Кит: {kit['name']} ({kit['price_range']})")
			logger.info(f"[Upgrade Analysis] Базовая цена: {base_price.get('value')} {base_price.get('currency')} ({base_price_ref} ref)")
			logger.info(f"[Upgrade Analysis] Стоимость кита: {kit_cost_ref} ref")
			logger.info(f"[Upgrade Analysis] Общая стоимость: {total_cost} ref")
			logger.info(f"[Upgrade Analysis] Цена апгрейда: {upgraded_price.get('value')} {upgraded_price.get('currency')} ({upgraded_price_ref} ref)")
			logger.info(f"[Upgrade Analysis] Прибыль/убыток: {profit_ref:+.2f} ref ({profit_percent:+.1f}%)")
			logger.info(f"[Upgrade Analysis] Рекомендация: {result['analysis']['recommendation']}")
			
			return result
			
		except Exception as e:
			logger.error(f"[Upgrade Analysis] Ошибка при анализе апгрейда {base_item} + {kit_type}: {e}")
			return None
	
	async def analyze_multiple_upgrades(self, page: Page, base_items: List[str], kit_types: List[str] = None) -> Dict[str, List]:
		"""
		Анализирует рентабельность апгрейдов для нескольких предметов
		
		Args:
			page: Playwright page
			base_items: список базовых предметов
			kit_types: список типов китов (по умолчанию ["specialized", "professional"])
		
		Returns:
			dict с результатами анализа
		"""
		if kit_types is None:
			kit_types = ["specialized", "professional"]
		
		logger.info(f"[Upgrade Analysis] Запускаю массовый анализ апгрейдов для {len(base_items)} предметов")
		
		results = []
		
		for base_item in base_items:
			for kit_type in kit_types:
				logger.info(f"[Upgrade Analysis] Анализирую {base_item} + {kit_type} kit...")
				
				result = await self.analyze_upgrade_profitability(page, base_item, kit_type)
				if result:
					results.append(result)
				
				# Небольшая задержка между запросами
				await asyncio.sleep(self.delays["page_load"])
		
		# Выводим сводный отчёт
		self._print_upgrade_summary(results)
		
		return results
	
	def _print_upgrade_summary(self, results: List[Dict]):
		"""Выводит сводный отчёт по всем анализам апгрейдов"""
		if not results:
			logger.info("[Upgrade Analysis] Нет результатов для анализа")
			return
		
		logger.info("[Upgrade Analysis] " + "="*79)
		logger.info("[Upgrade Analysis] СВОДНЫЙ ОТЧЁТ ПО АНАЛИЗУ АПГРЕЙДОВ")
		logger.info("[Upgrade Analysis] " + "="*79)
		
		# Группируем результаты по предметам
		by_item = {}
		for result in results:
			item = result["base_item"]
			if item not in by_item:
				by_item[item] = {}
			by_item[item][result["kit_type"]] = result
		
		# Выводим результаты по предметам
		for item, kits in by_item.items():
			logger.info(f"[Upgrade Analysis] {item}:")
			for kit_type in ["specialized", "professional"]:
				if kit_type in kits:
					result = kits[kit_type]
					profit = result["profit"]
					status = "✅ ПРИБЫЛЬНО" if profit["is_profitable"] else "❌ УБЫТОЧНО"
					logger.info(f"[Upgrade Analysis]   {kit_type}: {status} ({profit['ref']:+.2f} ref, {profit['percent']:+.1f}%)")
		
		logger.info("[Upgrade Analysis] " + "="*79)
		logger.info("[Upgrade Analysis] ТОП ПРИБЫЛЬНЫХ АПГРЕЙДОВ:")
		logger.info("[Upgrade Analysis] " + "="*79)
		
		# Разделяем на прибыльные и убыточные
		profitable = [r for r in results if r["profit"]["is_profitable"]]
		unprofitable = [r for r in results if not r["profit"]["is_profitable"]]
		
		if profitable:
			# Сортируем по прибыли
			profitable.sort(key=lambda x: x["profit"]["ref"], reverse=True)
			for i, result in enumerate(profitable[:5], 1):
				profit = result["profit"]
				logger.info(f"[Upgrade Analysis] {i}. {result['base_item']} + {result['kit_type']}: +{profit['ref']:.2f} ref (+{profit['percent']:.1f}%)")
		else:
			logger.info("[Upgrade Analysis] ПРИБЫЛЬНЫХ АПГРЕЙДОВ НЕТ")
		
		if unprofitable:
			logger.info("[Upgrade Analysis] НЕПРИБЫЛЬНЫЕ АПГРЕЙДЫ:")
			# Сортируем по убытку
			unprofitable.sort(key=lambda x: x["profit"]["ref"])
			for i, result in enumerate(unprofitable[:5], 1):
				profit = result["profit"]
				logger.info(f"[Upgrade Analysis] {i}. {result['base_item']} + {result['kit_type']}: {profit['ref']:.2f} ref ({profit['percent']:.1f}%)")
		
		logger.info("[Upgrade Analysis] " + "="*79)
		logger.info(f"[Upgrade Analysis] Всего прибыльных: {len(profitable)}, неприбыльных: {len(unprofitable)}")
	
	async def run(self) -> Dict[str, any]:
		"""
		Основной метод запуска анализа
		"""
		start_time = asyncio.get_event_loop().time()
		
		async with async_playwright() as p:
			# Инициализация браузера с оптимизациями
			browser = await p.chromium.launch(
				headless=False,
				args=[
					"--no-sandbox",
					"--disable-dev-shm-usage",
					"--disable-gpu",
					"--disable-web-security",
					"--disable-features=VizDisplayCompositor"
				]
			)
			
			context = await browser.new_context(
				viewport={"width": 1920, "height": 1080},
				user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
			)
			
			# Загружаем куки если есть
			if COOKIES_FILE.exists():
				try:
					raw = json.loads(COOKIES_FILE.read_text())
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
			
			# Прогрев через ключ
			await page.goto("https://backpack.tf/stats/Unique/Mann%20Co.%20Supply%20Crate%20Key/Tradable/Craftable", 
							timeout=90000, wait_until="domcontentloaded")
			
			# Определяем цену ключа
			self.runtime_key_price_ref = await self._detect_key_price_ref(page)
			
			# Запускаем анализ апгрейдов если есть предметы для анализа
			upgrade_results = []
			if self.upgrade_analysis_items:
				logger.info(f"[Arbitrage] Запускаю анализ рентабельности апгрейдов для {len(self.upgrade_analysis_items)} предметов")
				upgrade_results = await self.analyze_multiple_upgrades(page, self.upgrade_analysis_items)
			
			# Основной анализ арбитража
			results = {
				"sell": {},
				"buy": {},
				"upgrade_analysis": upgrade_results
			}
			
			# Анализ sell предметов
			if self.sell_items:
				sell_prices = await self.fetch_prices(page, self.sell_items, "sell")
				results["sell"] = sell_prices
			
			# Анализ buy предметов
			if self.buy_items:
				buy_prices = await self.fetch_prices(page, self.buy_items, "buy")
				results["buy"] = buy_prices
			
			await browser.close()
			
			# Статистика
			total_time = asyncio.get_event_loop().time() - start_time
			logger.info(f"[Arbitrage] Статистика: общее время={total_time:.2f}с, предметов={len(self.sell_items) + len(self.buy_items)}, среднее время на предмет={total_time / (len(self.sell_items) + len(self.buy_items)):.2f}с")
			
			# Выводим результаты
			print("\n=== Результаты арбитража ===")
			
			if results["sell"]:
				print("\n--- SELL ---")
				for item, data in results["sell"].items():
					print(f"{item}: {data['value']} {data['currency']} ({data['source']})")
			
			if results["buy"]:
				print("\n--- BUY ---")
				for item, data in results["buy"].items():
					print(f"{item}: {data['value']} {data['currency']} ({data['source']})")
			
			return results
	
	async def run_upgrade_analysis(self, base_items: List[str] = None, kit_types: List[str] = None) -> Dict[str, List]:
		"""
		Запускает только анализ апгрейдов без основного арбитража
		
		Args:
			base_items: список базовых предметов (если None, берётся из конфига)
			kit_types: список типов китов (если None, берётся ["specialized", "professional"])
		
		Returns:
			dict с результатами анализа
		"""
		if base_items is None:
			base_items = self.upgrade_analysis_items
		
		if kit_types is None:
			kit_types = ["specialized", "professional"]
		
		start_time = asyncio.get_event_loop().time()
		
		async with async_playwright() as p:
			browser = await p.chromium.launch(headless=False)
			context = await browser.new_context()
			
			# Загружаем куки если есть
			if COOKIES_FILE.exists():
				try:
					raw = json.loads(COOKIES_FILE.read_text())
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
					logger.info("[Upgrade Analysis] Куки подгружены")
				except Exception as e:
					logger.error(f"[Upgrade Analysis] Ошибка при загрузке куки: {e}")

			page = await context.new_page()
			await page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
			
			# Прогрев через ключ
			await page.goto("https://backpack.tf/stats/Unique/Mann%20Co.%20Supply%20Crate%20Key/Tradable/Craftable", 
							timeout=90000, wait_until="domcontentloaded")
			
			# Определяем цену ключа
			self.runtime_key_price_ref = await self._detect_key_price_ref(page)
			
			# Запускаем анализ
			results = await self.analyze_multiple_upgrades(page, base_items, kit_types)
			
			await browser.close()
			
			# Статистика
			total_time = asyncio.get_event_loop().time() - start_time
			logger.info(f"[Upgrade Analysis] Анализ завершён за {total_time:.2f}с")
		
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


async def test_upgrade_analysis():
	"""
	Тестирует новую логику анализа рентабельности апгрейдов
	"""
	print("\n=== Тест анализа апгрейдов ===")
	
	# Создаём экземпляр класса
	arbitrage = UpgradeArbitrage()
	
	# Тестовые предметы для анализа
	test_items = ["Rocket Launcher", "Degreaser", "Ambassador"]
	
	print(f"Тестирую анализ апгрейдов для предметов: {test_items}")
	print("Запускаю анализ...")
	
	try:
		# Запускаем анализ
		results = await arbitrage.run_upgrade_analysis(test_items)
		
		if results:
			print("✅ Анализ апгрейдов успешно завершён!")
			print(f"Результаты: {len(results)} предметов проанализировано")
		else:
			print("❌ Анализ апгрейдов не вернул результатов")
			
	except Exception as e:
		print(f"❌ Ошибка при тестировании анализа апгрейдов: {e}")


if __name__ == "__main__":
	test_parse_item_attributes()
	
	# Запускаем тест анализа апгрейдов (если есть asyncio)
	try:
		asyncio.run(test_upgrade_analysis())
	except Exception as e:
		print(f"Тест анализа апгрейдов пропущен (требует asyncio): {e}")