# r152 — проверки из книги доезжают до кампании (разрыв цепочки PDF → игра)

## ЦЕЛЬ

Одна проверяемая вещь: после привязки книги к кампании
(`POST /api/preparation/drafts/{id}/world` + `POST /api/prototype/campaigns` с
`preparation_draft`) в `state['locations'][0]` лежат проверки книги, и ход
«Проверить: Скрытность» превращается в бросок движка по Сл из книги, а не в
отказ.

## ЧТО ПРОВЕРЕНО ПРОРАБОМ (факты на HEAD ab72fcd, живой прогон, не догадка)

Прогон на собственном порту 19560, профиль `runtime/foreman-probe`,
офлайн-мок, книга `runtime/books/Hoard of the Dragon Queen RUS.pdf` (8,9 МБ):

- `POST /api/preparation/drafts/{id}/sources` → `{"path":"library","job":…}` — ветка r148 жива;
- job `complete`, `done=107`, `chapters=8`, `modules=8`;
- `GET /api/adventure/library/{bundle}/modules` → 8 модулей `ready`;
  у модуля 01 в `scene.checks` — **8** проверок, `scene.npcs` — 1;
- в sqlite `runtime/foreman-probe/adventure.sqlite3` таблица `adventure_modules`:
  `world.locations[0]` = «Гринест», **`checks` 8, `npc` 1** — данные на месте;
- `POST …/world` → `{"bound":true,"module":"auto-7ad6e955-01","pages":[[8,15]],
  "scene":{"locations":1,"npcs":1,"checks":8}}` — привязка отчиталась о 8 проверках;
- `POST /api/prototype/campaigns` → 200, `notice` «… проверок 8 …»;
- **`GET /api/campaigns/{c}` → `state['locations'][0]` = `{"description","facts",
  "interactions","name","npc","objects"}`, `checks` — КЛЮЧА НЕТ ВООБЩЕ, `npc` — 1.**

То есть место, описание и NPC из книги доехали, а проверки — нет. `notice`
врёт про 8 проверок, потому что считает их по `package`, а не по записанному
состоянию.

Где искать (гипотеза, доказать или опровергнуть):

1. `solo/prototype_prepared.py`, функция `bind_library_module`, вложенная
   `install(state)` (~строки 484–510): там `WorldLocation(..., checks=dict(place.checks))`
   затем `state['locations']=[lands_here.model_dump()]` и `import_package(state, rest)`
   (rest без `scene_name`, поэтому Гринест не трогается).
2. Порядок применения: `preparation.store.record_scenario(campaign,'prepared-world',scene,install)`
   и последующее чтение `store.campaign(cid)['state']`. Похоже, `checks` теряются
   между `install` и сохранением/чтением состояния (пересборка локации через
   `WorldLocation(**loc)` c дефолтами, или второй снимок состояния поверх).
3. Валидатор `WorldLocation.valid` принимает только ключи
   `loc:<...>:(skill|ability|saving_throw):<...>:<DC>` — если ключ книги не
   подходит, `install` упал бы `ValueError`; он не падает, значит либо `place.checks`
   пуст в момент install (но тогда откуда 8 в notice — проверить порядок
   арифметики `binding['scene']['checks']`), либо потеря идёт после.

## ФАЙЛЫ

- `solo/prototype_prepared.py` (основная правка)
- `solo/world.py` — только чтение, если понадобится разобраться с формой
- `solo/document_store.py` / модуль, где `record_scenario` пишет состояние — по факту
- новый тест `tests/test_book_checks_reach_campaign.py`

## НЕ ТРОГАТЬ

- `vendor/nat20/**` и `PATCHES.json` — в этой задаче не нужны.
- `tests/test_module_scene_from_book.py` (16 passed) — он держит сцену модуля, не подгонять.
- Никаких `git add .`, `git checkout --`, `git clean`, `reset --hard`.
- Не «чинить» тестом, который проверяет `package`, а не состояние кампании.

## ПРИЁМКА (все команды — вывод в отчёт)

1. Новый тест `tests/test_book_checks_reach_campaign.py`, который создаёт
   кампанию из книги и требует:
   - `state['locations'][0]['checks']` непуст, число совпадает с
     `module['scene']['checks']`, у проверки есть `page` и `quote`;
   - ключ проверки проходит регулярку `WorldLocation.valid`;
   - после `POST /api/nat20/... command-turn` «Проверить: Скрытность» ответ
     содержит бросок (d20 + модификатор против Сл из книги), а не отказ
     `target_invalid`/`ai_error`.
2. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_book_checks_reach_campaign.py -q`
   → N passed.
3. Батч смежных: `test_pdf_world_reaches_campaign, test_module_scene_from_book,
   test_adventure_continue*, test_preparation*, test_source_checks` → 0 failed.
4. Живой прогон на настоящей книге (свой порт, НЕ 19215, свой каталог
   `runtime/r152-live*`, офлайн-мок) и вывод:
   `state['locations'][0]['checks']` — сколько и один ключ целиком,
   затем один ход «Проверить: Скрытность» с текстом ответа.
   Если живой ход требует настроенного профиля — создать профиль с моком так,
   как это делали r149/r150, и написать в отчёте чем именно.

## ОТЧЁТ (15 строк max)

Коммит `r152: <что>`. Где терялись проверки (файл:строка), как доказано, число
проверок в кампании до/после, вывод приёмочных команд. Плохое — первой строкой.

## ПОТОЛОК

Если задача не закрывается за два подхода — остановиться, отчитаться фактами
(где потеря, что уже проверено), карточку не закрывать.
