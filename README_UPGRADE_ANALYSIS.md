# Анализ рентабельности апгрейдов TF2

## Описание

Новая логика позволяет анализировать рентабельность апгрейдов предметов в Team Fortress 2 с помощью killstreak kits.

## Как это работает

1. **Анализ базового предмета**: Бот получает текущую цену базового предмета (например, "Rocket Launcher")
2. **Расчёт стоимости апгрейда**: Добавляет стоимость killstreak kit к цене базового предмета
3. **Анализ апгрейднутого предмета**: Получает цену уже апгрейднутого предмета (например, "Strange Specialized Killstreak Rocket Launcher")
4. **Расчёт прибыли**: Сравнивает общую стоимость с ценой продажи апгрейда

## Поддерживаемые типы китов

### Specialized Killstreak Kit
- **Стоимость**: 47-50 ref (в среднем 48.5 ref)
- **Тип**: killstreak_tier = 2
- **Пример**: "Strange Specialized Killstreak Rocket Launcher"

### Professional Killstreak Kit  
- **Стоимость**: 2 keys 20 ref (в среднем 140 ref)
- **Тип**: killstreak_tier = 3
- **Пример**: "Strange Professional Killstreak Rocket Launcher"

## Конфигурация

Добавьте в `config.json`:

```json
{
  "upgrade_analysis_items": [
    "Rocket Launcher",
    "Degreaser", 
    "Ambassador",
    "Flame Thrower"
  ]
}
```

## Использование

### Автоматический анализ (при запуске основного скрипта)
```python
from tf2_arbitrage import UpgradeArbitrage

arbitrage = UpgradeArbitrage()
results = await arbitrage.run()
# Результаты будут в results["upgrade_analysis"]
```

### Отдельный анализ апгрейдов
```python
from tf2_arbitrage import UpgradeArbitrage

arbitrage = UpgradeArbitrage()

# Анализ предметов из конфига
results = await arbitrage.run_upgrade_analysis()

# Анализ конкретных предметов
custom_items = ["Rocket Launcher", "Degreaser"]
results = await arbitrage.run_upgrade_analysis(custom_items)

# Анализ только specialized kits
results = await arbitrage.run_upgrade_analysis(custom_items, ["specialized"])

# Анализ только professional kits  
results = await arbitrage.run_upgrade_analysis(custom_items, ["professional"])
```

## Пример вывода

```
[Upgrade Analysis] === РЕЗУЛЬТАТ АНАЛИЗА АПГРЕЙДА ===
[Upgrade Analysis] Предмет: Rocket Launcher
[Upgrade Analysis] Кит: Specialized Killstreak Kit (47-50 ref)
[Upgrade Analysis] Базовая цена: 9.77 ref (9.77 ref)
[Upgrade Analysis] Стоимость кита: 48.50 ref
[Upgrade Analysis] Общая стоимость: 58.27 ref
[Upgrade Analysis] Цена апгрейда: 3.25 keys (169.00 ref)
[Upgrade Analysis] Прибыль/убыток: 110.73 ref (190.0%)
[Upgrade Analysis] Рекомендация: ПРИБЫЛЬНО

[Upgrade Analysis] ================================================
[Upgrade Analysis] ТОП ПРИБЫЛЬНЫХ АПГРЕЙДОВ:
[Upgrade Analysis] 1. Rocket Launcher + specialized: +110.73 ref (+190.0%)
[Upgrade Analysis] 2. Degreaser + specialized: +89.23 ref (+156.2%)
```

## Структура результатов

Каждый анализ возвращает словарь с полной информацией:

```python
{
    "base_item": "Rocket Launcher",
    "kit_type": "specialized", 
    "kit_name": "Specialized Killstreak Kit",
    "base_price": {"value": 9.77, "currency": "ref", "ref": 9.77},
    "kit_cost": {"ref": 48.5, "range": "47-50 ref"},
    "upgraded_item": "Strange Specialized Killstreak Rocket Launcher",
    "upgraded_price": {"value": 3.25, "currency": "keys", "ref": 169.0},
    "total_cost": 58.27,
    "profit": {"ref": 110.73, "percent": 190.0, "is_profitable": True},
    "analysis": {
        "recommendation": "ПРИБЫЛЬНО",
        "roi": "190.0%",
        "break_even": "44.0%"
    }
}
```

## Преимущества

- **Автоматический расчёт**: Не нужно вручную считать цены
- **Реальное время**: Использует актуальные цены с backpack.tf
- **Подробный анализ**: Показывает ROI, break-even точку
- **Массовый анализ**: Может анализировать множество предметов одновременно
- **Гибкость**: Поддерживает разные типы китов и предметов

## Ограничения

- Требует стабильное интернет-соединение
- Зависит от доступности backpack.tf
- Цены могут меняться в реальном времени
- Некоторые редкие предметы могут не иметь достаточного количества объявлений