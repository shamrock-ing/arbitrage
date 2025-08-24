#!/usr/bin/env python3

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
	
	return {
		"quality": quality,
		"killstreak_tier": killstreak_tier,
		"australium": australium,
		"base_name": base_name
	}


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


if __name__ == "__main__":
	test_parse_item_attributes()
