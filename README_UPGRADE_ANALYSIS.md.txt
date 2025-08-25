# Анализ рентабельности апгрейдов TF2

## Описание

Новая логика позволяет анализировать рентабельность апгрейдов предметов в Team Fortress 2 с помощью killstreak kits.

## Как это работает

1. **Анализ базового предмета**: Бот получает текущую цену базового предмета (например, "Rocket Launcher") через sell orders
2. **Расчёт стоимости апгрейда**: Добавляет фиксированную стоимость killstreak kit к цене базового предмета
3. **Анализ апгрейднутого предмета**: Получает цену уже готового апгрейднутого предмета (например, "Strange Specialized Killstreak Rocket Launcher") через buy orders
4. **Расчёт прибыли**: Сравнивает общую стоимость (базовая цена + стоимость кита) с ценой продажи апгрейда

## Поддерживаемые типы китов

### Specialized Killstreak Kit
- **Стоимость**: 47-50 ref (в среднем 48.5 ref)
- **Тип**: killstreak_tier = 2
- **Пример**: "Strange Specialized Killstreak Rocket Launcher"

### Professional Killstreak Kit  
- **Стоимость**: 2 keys 20 ref (в среднем 124 ref)
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