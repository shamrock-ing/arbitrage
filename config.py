# === Backpack.tf API credentials ===
# Ключ берётся тут: https://backpack.tf/developer (нужен доступ к classifieds API)

BPTF_TOKEN = "a1BwJUW/CHggLArNv6KFVxlHLoshHNRFhV/4naZ3/Kw="         

STEAM_ID = "76561198189472678"             

# === Pricing ===
# Фикс/средняя цена Killstreak Kit (refined)

KIT_COST_REF = 48.0

# Если встречаются цены в ключах — укажи цену ключа в ref,
# иначе оставь None, и такие объявления будут пропускаться.
KEY_PRICE_REF = None  # например 60.0

# Фильтры сделок
MIN_PROFIT_SCRAP = 9     # минимум 1 ref прибыли
MIN_ROI = 0.05           # минимум 5% ROI

# Пауза между вызовами API
THROTTLE_SEC = 0.3

# Список оружия по умолчанию (если нет файла weapons.txt)
WEAPONS = [
    # добавь свои названия сюда или используй weapons.txt
]


# Предметы для поиска
SEARCH_ITEMS = [
    "Team Captain",
    "Rocket Launcher",
    "Strange Shotgun"
]