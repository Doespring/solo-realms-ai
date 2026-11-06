# Solo + Nat20: минимальная механическая вертикаль

Актуальная приёмка Windows preview.7: `reports/WINDOWS_PREVIEW_ACCEPTANCE.md`.
Существующие command-turn и экран соединены со сценой «Письмо у старого фонаря»;
добавлен frozen-вход `--nat20-worker`. Runtime и код upstream Nat20 не изменены.
Пакет содержит проверяемые .py байты закреплённых библиотек, metadata версий,
UPSTREAM manifest и минимальный imported corpus — не весь Foundry/Laaru каталог.
Доступен `use_feature(second-wind)`; ниже сохранено описание прежней вертикали.
При 0 HP команды остановлены до восстановления; окончательная смерть не объявляется.

Публичный Python API: `solo.nat20_api.Nat20GameEngine`. Создаёт только явно новые кампании Nat20; существующие кампании Solo не мигрируются и автоматически не активируются. Существующий `Game.play_command` и HTTP `command-turn` теперь выбирают этот backend при наличии `state.nat20`; для остальных кампаний сохраняется CommandEngine. UI, профиль провайдеров и денежные лимиты не меняются. Хост обязан авторизовать доступ к кампании перед вызовом доверенного Python API; новых HTTP-маршрутов нет.

## Что действительно работает

Используются существующие Solo Store, SQLite-транзакции, события, checkpoints, undo и Memory. Общие `GameEvent` и `GameSnapshot` импортированы из `engine_memory_contracts` (`memory-v1`). Мастер и новые записи журнала используют существующий `game_commands.GameCommand`; прежнее расширение `nat20-command-v1`/`rules_version=dnd2024` сохраняется на границе worker и читается для старых записей. Полное восстанавливаемое состояние находится в блоке `nat20` версии `nat20-state-v1`; память получает узкую типизированную проекцию. JSON Schema сохранены в `schemas/`.

Механика вызывается у закреплённого Nat20 0.7.0, commit e187c813bb4e43c4e4e62942ecd83831f1cdbcaa. Исходники в `vendor/nat20` не изменены; `UPSTREAM.json` содержит хеши, лицензии и ревизию. Runtime сверяет установленные Python-файлы с этими хешами. Используется только минимальный MemoryAssetLoader, без автоматического перехода на полный bundled corpus.

Доступны восемь команд: check, start_combat, move, attack, end_turn, short_rest, long_rest, end_combat. Проверки ограничены подтверждённым каталогом athletics/perception/strength. start_combat открывает один зарегистрированный бой: воин-человек 1-го уровня с длинным мечом, кольчугой и щитом против гоблина-воина. Выборы происхождения/черт и развитие персонажа не объявляются завершёнными; это подтверждённый механический профиль для стенда, не полный character builder PHB.

## Пример без UI и LLM

```python
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine, GameCommand

engine = Nat20GameEngine(Store("my-isolated-test/game.sqlite3"))
campaign = engine.create_campaign("Воин", seed=5)
cid = campaign["id"]

def apply(request_id, action):
    version = engine.store.campaign(cid)["version"]
    return engine.execute(cid, GameCommand(request_id=request_id,
                          expected_version=version, action=action))

apply("check-1", {"kind": "check", "check_id": "athletics"})
apply("start-1", {"kind": "start_combat", "encounter_id": "basic"})
apply("move-1", {"kind": "move", "position": {"x": 3, "y": 1, "z": 0}})
apply("attack-1", {"kind": "attack", "target": "goblin"})
checkpoint = engine.store.save(cid, "Активный бой")
# После завершения процесса:
engine = Nat20GameEngine(Store("my-isolated-test/game.sqlite3"))
restored = engine.restore(cid)
# restored['runtime']['combat']['view'] содержит полный native read-model.
apply("turn-1", {"kind": "end_turn"})
```

Обычная атака Nat20 может сама завершить ход. end_turn завершает ещё открытый ход героя и вызывает штатную политику чудовищ, пока управление не вернётся герою. В этом проявляется реальная семантика Nat20; полная тайминговая эквивалентность Foundry не заявлена. end_combat разрешён после поражения противника, а не как способ удалить живых врагов. Отдых выполняется вне боя; long_rest дополнительно соблюдает существующее ограничение Solo раз в сутки.

## Сохранение и перезапуск

Nat20 предоставляет volatile handle и не предоставляет public export/import полного приватного боя. Адаптер сохраняет исходные типизированные спецификации, seed, команды, digest событий и полного public read-model на каждом шаге. В новом локальном процессе он заново вызывает start_combat и тот же журнал через public API; проверяет каждый шаг и итоговый снимок перед продолжением. Так восстанавливаются и скрытые sidecars Nat20, которые не представлены в read-model. Приватные dataclass/registry не читаются и не сериализуются; pickle не используется.

Каждая операция использует отдельный процесс. При отказе механики, divergence, несовпадении редакции/версии или сбое SQLite-транзакции незакоммиченный provider state исчезает. Идемпотентный повтор возвращает ранее сохранённое событие без запуска RNG. Повтор отменённой команды и изменённая команда с тем же ID отклоняются. Версия, состояние героя и проекция Memory фиксируются одной существующей транзакцией.

Лимит этой реализации — 256 команд одного боя и таймаут worker 45 секунд; восстановление линейно по журналу, компактного upstream checkpoint API нет. При превышении/расхождении отказ явный. Это ограниченная вертикаль, не доказательство длительной кампании.

## Данные и границы переноса

`pack-inventory.json`: автоматизированный инвентарь всех 40 пакетов двух манифестов; 9 Foundry-пакетов явно относятся к 2024. Остальные Foundry-пакеты различаются как 2014 или unknown; неопределённость не исправляется по догадке. Для каждого сохранены source, rules_version, locale, license, provenance и import_status. Макросы и Laaru spellGiver не исполнялись.

`imported/manifest.json`: реально импортированы 12 документов Foundry 5.3.3 с `system.source.rules=2024` и CC-BY-4.0. Это класс/вид, три предмета, шесть особенностей, гоблин-воин. LevelDB читается forensic reader без открытия на запись; соблюдаются активный MANIFEST, последовательности, удаления и вложенные документы. JSON преобразуется существующим Nat20 Foundry-транслятором, а структурные UUID-ссылки фиксированных grants 1-го уровня сопоставляются с уже импортированными документами. Отложенные зависимости и выборы записаны явно.

`encounter-profile.json`: отдельная узкая проекция оружия гоблина. При импорте использованы закреплённые функции Nat20 для выбора характеристики и бонуса атаки (DEX, +4), а также модификатора урона (+2). Runtime подставляет эти проверенные данные в native activity; повторного броскового/боевого алгоритма нет. Активна обычная атака скимитаром, в том числе с помехой. Атака гоблина с преимуществом, требующая дополнительного условного урона, отклоняется атомарно. Shortbow, Nimble Escape и прочие ветви гоблина импортированы как данные, но не включены в этот профиль.

Laaru 4.3.0 остаётся только русским кандидатом: DnD14, PHB/DMG/MM 2014, смешанные HB/UA и непроверенные права. Ни один его документ/скрипт не импортирован в исполняемый 2024-каталог. Доступность локального архива не считается разрешением на перераспространение всего содержимого. Для Foundry-контента сохранена CC-BY-4.0 цепочка атрибуции; изображения не импортировались, JS не исполнялся.

Пока требуют отдельной работы: остальные Foundry activity types/flags, общая магия, полные выборы класса/вида/черт, progression, conditional riders, дальнобойные и бонусные действия выбранного монстра, полный NPC каталог, UI dispatch и общий редакционный migration. Наличие JSON или описания эффекта не означает, что его механизм перенесён в Python.

## Подключение к мастеру и памяти

`solo.command_backend` выбирает backend для исполнения, контекста и снимков; испорченный блок Nat20 не переключает кампанию на старые правила. Идемпотентная выдача читает сохранённые команду/событие/снимок без нового worker, RNG или AI. Контекст Interpreter/Narrator строится прежним Memory Context Builder с ACL. Registry checks используется совместно с worker; приватные ресурсы NPC скрыты. Полные `nat20`, `native_events` и `native_result` не выдаются существующими публичными проекциями кампании/истории.

Реальные native события остаются в прежнем scripted result, а типизированные эффекты `command_resolved` поступают в Memory атомарно с состоянием. Уточнения, отказы, проверка версии внутри транзакции, undo/ветвление, проверенный Narrator и защитные границы сценариев сохранены. Полного UI-подключения и проверки с реальной облачной моделью этот этап не включает.

Целевые HTTP-проверки: `.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_nat20_command_cycle.py -q -p no:cacheprovider` с `SOLO_TEST=1`. Они используют mock Interpreter/Narrator и настоящий Nat20: check → start → move/attack → save на ходу NPC → новый процесс с HTTP-приложением → продолжение → end → short/long rest. Дополнительно проверяются повтор после дальнейших ходов, старый versioned envelope, stale/race, запреты, ACL, rollback Memory, undo и происхождение фактов. Результаты нового этапа хранятся отдельно от первоначальной Python-only приёмки в `reports/gm-command-cycle*`.

## Проверки и повторение

Целевой запуск: `.venv/Scripts/python.exe -X utf8 -B tools/test_nat20_integration.py`.
Результаты в `reports/summary.json`, JUnit и журнале. Тесты используют только свежие временные БД и настоящий новый процесс для restart. Проверяются check, оба отдыха/ресурсы, короткий бой, сохранение на ходу противника, равенство продолжения, условия, rollback, stale/idempotence/undo, отсутствие автоматической миграции и смешения редакций.

Установка runtime: `pip install -r requirements-nat20.txt`. В этой Windows-среде pip HTTPS через proxy не работал; `tools/fetch_nat20_dependencies.py` отдельно загрузил публичные пакеты через системный HTTPS с проверкой SHA256 и установил их офлайн. Версии и происхождение сохранены в `dependencies.json`. Forensic importer использует dfindexeddb с Windows-совместимыми python-snappy/zstd; это отдельные зависимости инструментов, не игрового engine. Не собирался EXE, не вызывались платные API.

Воспроизводимый импорт: `tools/import_nat20_minimal.py --foundry integration/nat20/sources/foundry-5.3.3/dnd5e`. Инвентарь создаёт `tools/inventory_rules_sources.py` с явными --archive, --foundry, --laaru, --import-manifest и --output. Оригиналы архивов и модулей доступны только для чтения; созданная выборочная распаковка находится в sources/ этой интеграции.
