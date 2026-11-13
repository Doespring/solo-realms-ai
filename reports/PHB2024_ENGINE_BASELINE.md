# PHB 2024: исходное состояние движка (раунд 1)

Дата: 2026-10-27. Поток: `SoloRealmsAI-Nat20`, ветка `work/nat20-engine`, HEAD `52bf958`.
Python 3.12.10 (`SoloRealmsAI-Nat20\.venv\Scripts\python.exe`).

## 1. Источник и его версия

| что | значение |
|---|---|
| пин Foundry | `foundryvtt/dnd5e` commit `965ad2d0cf5d063dac675ba078b5bd3c3c0dd449` (pinned 2026-05-30) |
| контрольная сумма | `758b3a3df15523d7e2e29a50fb71ed08c6f4b462d76cfa0696166800d956c72b` |
| локальная копия | `integration/nat20/sources/foundry-5.3.3/dnd5e/packs` — 11.35 МБ, 9 паков |
| редакция корпуса | `rules_version: dnd2024`, `nat20_version: 0.7.0`, `schema_version: 1.0.0` |

Паки на месте: `classes24`, `origins24`, `feats24`, `spells24`, `equipment24`,
`content24`, `actors24`, `monsterfeatures24`, `tables24` (LevelDB).

## 2. Что реально доступно движку

Загрузчик `dnd5e_srd_data.loader.BundledAssetLoader` читает встроенный SRD-корпус
`vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/`:

| категория | файлов |
|---|---|
| classes | **12** (все классы PHB) |
| subclasses | **12** (по одному на класс) |
| species | 14 |
| backgrounds | 4 |
| feats | 17 |
| spells | 339 |
| items | 546 |
| monsters | 341 |
| features | 261 |
| traits | 102 |
| conditions | 15 |

Данные развития классов полные по объёму: `fighter.json` несёт **31** запись
`advancement` до **уровня 20** (AbilityScoreImprovement, HitPoints, ItemChoice, ItemGrant,
ScaleValue, Subclass, Trait), 12 выданных свойств, d10, спасброски str/con.

## 3. Ключевые разрывы, установленные проверкой

1. **Подклассов 12 вместо ~48.** В PHB 2024 у каждого класса четыре подкласса; в корпусе
   по одному (berserker, champion, devotion, draconic, evoker, fiend, hand, hunter, land,
   life, lore, thief). Это главный разрыв объёма, и он не про механику, а про данные.
2. **`integration/nat20/imported/canonical` — не PHB.** Там 12 документов
   (features 6, items 3, classes 1, species 1, monsters 1); `manifest.scope` прямо
   говорит: «level-one human fighter, equipment and goblin-warrior; action capability
   allowlist remains narrow». Это проектный образец, а не полный корпус.
3. **Генератор корпуса невоспроизводим в этой папке.** `tools/regen.py` ждёт YAML в
   `dnd5e-srd-data/raw_sources/`, а там только `PINS.json`. Значит текущий `canonical/`
   получен внешним прогоном, и повторить его здесь нельзя. Это нарушает п.10 поручения
   (воспроизводимость генерации корпуса).
4. **Фильтр корпуса — только SRD.** `tools/translators/foundry.py:470 _is_srd_doc`
   пропускает документ, лишь если у него `flags.srd` или лицензия `CC-BY-4.0` с правилами
   2014/2024. Закрытый материал PHB в корпус не попадает принципиально — это корректно
   по лицензии, но означает, что «PHB-пакет» нельзя собрать из этих паков.

## 4. Осиротевшие тесты (не относятся к Nat20)

Пять тестов ссылаются на модуль `rules_data`, удалённый при переработке движка
(коммит `9723a04` «Snapshot before D&D 2024 engine and chat redesign»):
`test_hybrid_rules_integration.py`, `test_hybrid_rules_runtime.py`, `test_phb_chapter_one.py`,
`test_schema_validator.py`, `test_nat20_playable_rules.py`. Они проверяют схемы `routing`/`dmg`
и классификатор прежней архитектуры, к PHB 2024 и Nat20 отношения не имеют. Из базового
прогона движка исключены явным `--ignore`, а не помечены пройденными.

## Следующая операция

Составить матрицу глав PHB 2024 против реализованных модулей движка и начать с проверки
полного развития персонажа до 20 уровня на нескольких классах.
