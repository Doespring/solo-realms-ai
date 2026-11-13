# A04 / F07 + F08 + F13 — сцена показывает текст для Мастера, осмотр требует собеседника

Дата: 08.11.2026. Ветка `work/nat20-engine`, дерево `SoloRealmsAI-Nat20`.
Живая база, профиль и порт 19215 не трогались: проверки — pytest на временных
базах, модель синтетическая (`PreviewMock` / `MockLLM`), ни одного платного вызова.

## Факт, с которого начали

Аудит 08.11 (S01, S78, S60):

* **F07.** «Осматриваюсь» в кампании по PDF возвращал страницу книги — заголовок
  эпизода, обзор приключения и планы ведущего, — а тот же длинный текст стоял
  под историей как статус.
* **F08.** «Что вокруг, какие выходы, кто присутствует?» → «Здесь нет доступного
  собеседника»: одна часть составного намерения отменяла весь ход.
* **F13.** Вне боя единственной целью числился сам герой.

## Причина

1. `solo/prototype_prepared.py::bind_library_module` клал в место и в событие
   старта **сырой текст первой страницы главы** (`excerpt`) — вместе с
   заголовком «Эпизод N», обзором приключения и половиной Мастера.
2. `solo/prototype_gm.py::present_intent` считал разговором всё, что модель
   назвала `conversation`, и требовал ровно одного собеседника: при пустой сцене
   ход заканчивался отказом, а одна фраза о собеседнике отменяла осмотр.
   Разбора реплики на части не было вовсе.
3. `solo/nat20_api.py::capabilities` вне боя отдавал `targets=['hero']`.

## Что сделано

**`solo/public_scene.py` (новый) — контракт сцены из четырёх разных текстов.**
`scene_contract(place)` возвращает `source_text` (страница книги как напечатана),
`private_facts` (тайны, заголовки, планы ведущего), `public_scene` (то, что видит
игрок) и `runtime_status` (одна служебная строка ≤ 120 знаков). В сцену игрока
проходят только открытые предложения: заголовки книги, колонцифры, служебные
слова («эпизод», «совет», «сессия») и разделы ведущего отбрасываются. `for_player()`
— тот же фильтр на выходе рассказчика: служебная фраза не возвращается в сцену
даже из воспоминаний кампании.

**`solo/scene_intents.py` (новый) — разбор реплики на части.**
`observe` (осмотр и вопросы о сцене), `talk` (обращение к названному человеку),
`ask_master` (вопрос ведущему), `other` (механика — не подменяется). Кавычки не
режутся; «Скажи, что вокруг?» — осмотр, а не разговор с отсутствующим человеком;
`unmet()` возвращает выполненные части нетронутыми, а невыполненные называет
по-русски.

**`solo/prototype_gm.py::present_intent`.** Пакет рассказчика собирается из
`public_scene`, а не из сырого описания места; факты фильтруются. Разговор без
собеседника больше не отказ: ход отвечает на то, что видно. Названный собеседник
(цель от интерпретатора) по-прежнему ведёт в разговор. Составная реплика:
выполненная часть остаётся, невыполненная дописывается строкой
`Не выполнено: «…» — здесь не с кем говорить.` — и попадает в журнал, а не только
в ответ клиенту.

**`solo/prototype_prepared.py::bind_library_module`.** Старт и описание места
берутся из открытой части страницы; сырая страница сохраняется отдельно в
`preparation.library.source_text`.

**`solo/world.py::location_view`.** Место отдаёт `public_scene` и `status`
(≤ 120 знаков) отдельно от книжного `description`; `web/play.js` показывает в
полосе под историей `status`, а не всю сцену.

**`solo/nat20_api.py`.** `targets_of()`: вне боя цель — тот, кто стоит в сцене;
герой целью не является. В бою список прежний (инициатива, «hero» — законная
цель заклинания).

**`solo/model_protocol.py`.** В контракт интерпретатора добавлено разделение:
вопросы о сцене — осмотр, а не разговор; разговор — только обращение к
названному видимому NPC.

## Доказательства

`.venv/bin/python -m pytest tests/test_a04_scene_contract.py -q` → **17 passed**
(23 с, модель синтетическая).

Покрытие приёмки Q04/Q05/Q07:

| № | Приёмка | Проверка |
|---|---|---|
| 1 | Осмотр без NPC проходит, 0 отказов «нет собеседника» | `test_observation_in_an_empty_scene_is_answered_not_refused`; сквозной `test_observation_and_a_misread_scene_question_are_both_answered` (модель нарочно отвечает `conversation`) |
| 2 | На 1-м ходу нет «эпизод/совет/сессия» | `test_book_page_becomes_a_public_scene_without_the_masters_half`, `test_the_book_page_never_reaches_the_player_on_the_first_turn`, `test_a_campaign_from_a_book_opens_on_a_public_scene_not_on_the_page` — на странице книги из `fixtures/hoard_pages.py` |
| 3 | Статус ≤ 120 знаков и не повторяет сцену | `test_status_is_one_short_line_and_does_not_repeat_the_scene`, `…opens_on_a_public_scene…` (`state.locations[0].status`) |
| 4 | Себя в целях нет | `test_the_hero_is_not_a_target_out_of_combat` (настоящий движок, `fighter-standard-array-2024-v1`) |
| 5 | Составная реплика: сделанное видно, отклонённое названо | `test_a_composite_reply_keeps_the_worked_part_and_names_the_rest` (строка проверена и в ответе, и в журнале) |

Регрессия по затронутым путям (все прогоны — `.venv`, `SOLO_TEST`, без сети):

* `test_r254_grounded_narrator_role.py`, `test_r250_start_scene.py`,
  `test_chat_turn_reaches_engine.py`, `test_narrator_failure_is_named.py`,
  `test_prototype_gm.py`, `test_module_scene_from_book.py`,
  `test_book_checks_reach_campaign.py`, `test_player_visible_text.py`,
  `test_native_actions_in_shipped_scene.py` — **88 passed**
  (после правки двух сторожей — см. ниже);
* `test_world.py`, `test_prototype_gm_names.py`, `test_prototype_host.py`,
  `test_prototype_integration.py`, `test_playable_ui.py`, `test_play_screen.py`,
  `test_playable_scene_memory.py`, `test_nat20_social_actions.py`,
  `test_dialogue_paging_api.py`, `test_social_attitude_and_checks.py`,
  `test_r236_worlds.py`, `test_r237_playable_start.py`, `test_first_playable.py`
  — **79 passed, 4 failed**; все четыре падения — `legacy_campaign_unsupported`
  (кампания прежней версии не открывается; `solo/app.py`, не эта задача) и
  воспроизводятся на базовом коммите `HEAD~2` в отдельном worktree;
* `test_pdf_world_reaches_campaign.py`, `test_pdf_world_http.py`,
  `test_chapter_titles_with_page_numbers.py` — **21 passed** после правки одного
  сторожа (ниже);
* `test_master_handoff_f01.py`, `test_confirmed_map_on_a_turn.py`,
  `test_nat20_npc_recruit_r218.py` — **29 passed**;
* `test_nat20_stage2.py`, `test_playable_ai_readiness.py`, `test_pdf_begin_large.py`
  — **25 passed, 3 failed**; те же три падения есть на базовом коммите
  (`test_nat20_stage2` — эффекты/смерть, `playable_ai_readiness` — пакеты ролей);
* `test_nat20_prototype.py` — **5 passed, 1 failed** (`expects 'Stale'`, а код
  отвечает русской причиной; падает и на базовом коммите).

Два сторожа закрепляли прежнее поведение и обновлены вместе с контрактом (не
ослаблены):

* `tests/test_prototype_gm_names.py` ждал буквальную строку прежнего
  присваивания `status`; теперь проверяет новую строку и `kind=status`, а
  поведение (`kind == 'observation'` в журнале) проверяет
  `tests/test_a04_scene_contract.py`.
* `tests/test_pdf_world_reaches_campaign.py` требовал, чтобы описание места было
  сырой страницей книги — это и есть F07. Теперь проверяет, что страница лежит в
  `preparation.library.source_text`, а в описании места нет заголовка главы.

`pytest tests/` целиком не доведён до конца: прогон упирается в чужие
незакоммиченные правки (`solo/adventure_worker.py` — отказ «в PDF нет текстового
слоя», из-за него падают `test_adventure_continuity`), поэтому цифры даны по
затронутым путям, а не одной строкой по всему дереву.

## Ограничения (честно)

* **Пункт 3 задания отменён** комментарием владельца 08.11: панели действий в игре
  нет. Поэтому «вне боя первым в панели „Осмотреть“» проверить нечем; из F13
  закрыта серверная половина (цель и её допустимость в разборе реплики).
* **Уже начатые кампании**: описание места чинится при чтении (статус и сцена в
  ответе Мастера — чистые), но сохранённая в журнале строка старта остаётся
  страницей книги: журнал не перезаписывается. Новые кампании чистые с первого
  хода.
* `drop_prone`, «воину доступны заклинания» — это список возможностей **движка**
  (`SHIPPED_NATIVE_OPERATIONS`, каталог листа); A04 его не меняет, это отдельные
  F-пункты движка.
* Механика Nat20, база и лимиты не трогались.

## Следующая операция

Перенос в основное приложение — за интегратором: правки касаются `solo/` и
`web/play.js` этой ветки (`prototype_gm`, `world`, `nat20_api`, `model_protocol`,
`prototype_prepared`) плюс два новых модуля и новый тест.
