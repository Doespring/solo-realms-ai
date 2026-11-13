# r158 — проверенная книга подключается к кампании Nat20, а не отбивается 409

## ЦЕЛЬ

Пользовательский путь «книга → проверенный сценарий → кампания → карта/раздатка»
для кампании, начатой на экране Nat20, сейчас односторонний: клиент зовёт
`POST /api/adventure/review/campaigns/{id}/attach` и `…/continue`
(`web/adventure.js`), а гейт `one_backend` отдаёт `409 command_backend_required`
(`solo/playable_ui.py:129,144`). Разрешить этот путь, не открывая второй
механический бэкенд.

## ЧТО ПРОВЕРЕНО ПРОРАБОМ (не перевыводить)

- `solo/playable_ui.py:129`: `service=True` только для суффиксов
  `command-turn|commands|screen/commands|saves|restore/[^/]+|undo|notes|dice|master|gm|level-up`
  плюс `world/command|world/preview|world/import`.
- Для `api/adventure/review/campaigns/{id}/attach` части пути
  `['api','adventure','review','campaigns',id,'attach']`, `parts.index('campaigns',1)=3`,
  значит `position!=1` → `service=False` → 409 (строка 144).
- Клиент этот маршрут действительно зовёт: `web/adventure.js` — `…/continue`
  с `bundle`, `module`, `request_id`, `confirm`, `standalone`.
- `world/*` уже сделан по тому же принципу: слой мира разрешён, механика — нет.
  Значит прецедент есть, новый бэкенд не вводится.

## ЧТО СДЕЛАТЬ

1. Разрешить в гейте ровно эти два маршрута для Nat20-кампании:
   `adventure/review/campaigns/{id}/attach` и `adventure/review/campaigns/{id}/continue`
   (и суффиксы `review/materials/{id}` PUT/POST, если они тоже отбиваются —
   проверить фактом, а не догадкой: `parts.index('campaigns')` их не касается,
   но убедиться прогоном).
2. Ничего не менять в правилах разрешения механики: `command-turn`, броски,
   отдых, level-up остаются только за экраном Nat20.
3. Если `AdventureService.attach()` на состоянии Nat20-кампании падает не 409, а
   своей ошибкой (форма состояния другая) — это находка, её и чинить; но тогда в
   отчёте назвать, какое поле не сходится, с цитатой.

## ФАЙЛЫ

- `solo/playable_ui.py` (гейт — основная правка)
- `solo/adventure_api.py` / `solo/adventure_engine.py` — только если `attach()`
  не умеет состояние кампании Nat20
- тест: `tests/test_attach_on_nat20_campaign.py`

## НЕ ТРОГАТЬ

- `vendor/nat20/**`, `PATCHES.json`.
- Правило одного бэкенда: механика только через экран Nat20.
- Раскрытие материалов и логику `reveal()` из r155.

## ПРИЁМКА (вывод в отчёт)

1. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_attach_on_nat20_campaign.py -q`
   → N passed. Тест обязан проверять оба: `POST …/attach` и `…/continue` на
   Nat20-кампании дают не 409, и при этом `POST …/command-turn` по-прежнему
   проходит через Nat20 (гейт не ослаблен для механики).
2. Батч смежных: `test_r124_screen_commands, test_nat20_playable_rules,
   test_chat_turn_reaches_engine, test_confirmed_map_on_a_turn,
   test_adventure_engine` → 0 failed.
3. Живой прогон по HTTP (свой порт, `runtime/r158-live*`, офлайн-мок):
   кампания, начатая на экране Nat20 → `POST …/attach` (`confirm:true`) →
   в отчёте статус и код ответа, появился ли `state['adventure']`; затем ход
   «Показываю игрокам карту …» из r155 → материал в `known_media`; и один
   механический ход через `command-turn` → 200, а не 409.

## ОТЧЁТ (15 строк max)

Коммит `r158: <что>`. Коды ответов до/после, что стало доступно, чем доказано,
что осталось за экраном Nat20. Плохое — первой строкой.

## ПОТОЛОК

Два подхода. Не закрылось — отчёт фактами (какая ошибка вместо 409), карточку не
закрывать.
