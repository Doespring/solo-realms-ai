# ЗАДАЧА · Nat20: разобрать 13 падений до нуля падающих тестов

Дата: 30.10.2026. Постановщик — прораб (Hermes, профиль `default`).
Исполнитель — `coder`.
База: ветка `work/nat20-engine`, HEAD `84170d2`.

> **Только баги. Правила из PHB/DMG и новые механики в этой задаче НЕ трогать.**
> Одна задача — один класс работы. Если в ходе разбора покажется, что «надо
> дописать правило», — записать в отчёт отдельной строкой и не делать.

---

## 0. Что я уже проверил сам (не перепроверяй заново)

Реальный прогон 30.10.2026, пакетами по 3 файла, 101 пакет, все 301 тест:

```
2363 passed, 13 failed, 36 skipped, 0 errors
```

**Прежний список `reports/FAILING-ALL.md` УСТАРЕЛ** — не читай его как правду
и не чини по нему. 90 из 120 падений в нём исчезли: папка `rules_data/` теперь
лежит в дереве движка, и я лично прогнал:
`test_phb_chapter_one.py` → 190 passed, `test_hybrid_rules_production.py` →
48 passed, `test_phb_chapter_one_production.py` → 41 passed.

Имена падений и логи пакетов лежат здесь:
`/home/hermes/.hermes/cache/scratch/nat20run/baseline30/`
(`results.jsonl`, `fails.txt`, `logs/bNNN.txt`).

---

## 1. ЦЕЛЬ

Из 13 падающих тестов сделать так, чтобы **каждый** попал в одну из двух
категорий:

- **зелёный** — код или данные приведены к книге, тест проверяет настоящую
  механику;
- **честный жёлтый** — предметная причина, записанная в коде теста явным
  `pytest.skip('...')` (или `xfail(strict=True)` с объяснением), с указанием,
  чего не хватает и где следить. Не `importorskip`, не удаление строки, не
  `--ignore`.

Подгонять тест под текущее поведение **запрещено**. Если тест прав, а код
врёт — правится код. Если прав код, а тест устарел — правится тест, и в
сообщении коммита одно предложение, почему устарел.

## 2. ФАЙЛЫ, ГДЕ РАБОТАТЬ

Тесты (можно править только эти):
```
tests/test_creation_options_batch.py
tests/test_desktop.py
tests/test_desktop_pdf_smoke.py
tests/test_first_playable.py
tests/test_frozen_pdf_smoke_fixture.py
tests/test_nat20_bless.py
tests/test_nat20_dash.py
tests/test_nat20_item_rest_recovery.py
tests/test_nat20_prototype.py
tests/test_nat20_release_manifest.py
tests/test_pdf_nat20_start.py
tests/test_pdf_world_http.py
tests/test_preview8_release.py
```
Код, который разрешено править (только по конкретному дефекту):
```
solo/content_verification_cache.py        (#5, доказанный дефект)
solo/prepared_world.py                    (#9, причина отказа названа, решение за тобой)
solo/nat20_worker.py                      (#6, только если нужен явный отказ)
solo/prototype_prepared.py                (#4, только если решаешь «селфить» встречу)
```

## 3. НЕ ТРОГАТЬ

- `vendor/nat20/**` и `PATCHES.json` — правок движка в этой задаче нет.
  Если всё же правишь файл vendor — **обязан** перепинить `PATCHES.json`, иначе
  весь публичный путь падает `Content differs from pinned SHA`.
- `SoloRealmsAI-app/**` (соседний поток), чужие ветки, `.git` приложения.
- `solo/combat.py`, `solo/game.py`, `solo/natural.py` — не по теме задачи.
- Не удалять тесты, не ставить `--ignore`, не переписывать фикстуры «чтобы
  сошлось».
- Не коммитить `runtime/`, `reports/ENGINE_TEST_BASELINE.md`, `*.sqlite3`,
  личные книги.
- `git add .` запрещён. Только явный список файлов.

## 4. ОКРУЖЕНИЕ И КОМАНДЫ (проверено мной на этом сервере)

```bash
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 .venv/bin/python -m pytest tests/<файл>.py -q --no-header --tb=line \
  -p no:cacheprovider
```
- `dnd5e_engine` грузится из **этого** `.venv` (Python 3.14), не из vendor.
  Правка в `vendor/` невидима, пока не синхронизирована.
- **Полный прогон здесь НЕ запускать.** 4 ГБ RAM, полный прогон одним
  процессом умирает без строки `N passed`. Прогонять пакетами:
  `/home/hermes/.hermes/cache/scratch/nat20run/baseline30/run.py 0 1 2`
  (аргументы — номера пакетов; `results.jsonl` пополняется, старые записи
  делают перезапуск дешёвым). Пакеты по 3 файла, итог — строки `FAILED`.
- Windows-пути в документах проекта игнорировать, они исторические.

## 5. КАРТОЧКИ — 13 падений с причиной (диагностика моя, проверь и действуй)

### #1 `test_first_playable.py::test_exact_duplicate_and_explicit_npc_binding_are_repaired_without_guessing`
Тест ждёт `reason == 'Неизвестная локация NPC'`. Сейчас карантин даёт
`'Местонахождение NPC не подтверждено источником'` (проверено запуском).
Факт «Лев без подтверждённой локации уходит в карантин» — выполняется.
Вопрос: это улучшенная формулировка (тогда — обновить ожидание и объяснить)
или причина другая (тогда — чинить `prepared_world.py:1425+`).

### #2 `test_nat20_item_rest_recovery.py::test_multiclass_counts_every_class`
`ValueError: multiclass into 'paladin' needs 13 in strength, charisma; short on
charisma`. Правило 13 введено коммитом `aea7d5a`; `SCORES` в тесте
(`charisma=12`) устарел. **Тест устарел, код прав.** Поднять charisma до 13
именно в этом тесте (глобальный `SCORES` меняй только если остальные тесты
файла останутся зелёными).

### #3 `test_nat20_release_manifest.py::test_a_changed_engine_file_breaks_the_release`
`DID NOT RAISE`. Я разобрал: тест портит
`vendor/nat20/.../activities/dice.py`, а `release()` проверяет **установленное**
дерево (`.venv/.../site-packages/dnd5e_engine`), правка vendor без синка
невидима — это нормальная архитектура, а не дефект движка. **Дефект теста.**
Правильно: портить файл в `Path(dnd5e_engine.__file__).parent`, откатывать
`finally`, и убедиться, что `verify_tree` реально ловит порчу.

### #4 `test_pdf_world_http.py::..._starts_explicit_2024_hero_without_battle_and_replays`
Тест: `assert state['nat20']['definition']['encounters'] == {}`. Код:
`solo/prototype_prepared.py:default_encounters()` (коммит `f908c61` «A prepared
encounter reaches the table as a real fight») теперь даёт встречу `first-blood`.
Это осознанное изменение продукта. **Решение владельца принято: тест
обновить** — писать ожидание «подготовленная встреча `first-blood` есть,
бой `combat is None`, легаси-резолвер не вызывается» и оставить смысл
«игра стартует без боя».

### #5 `test_nat20_bless.py::test_pending_release_isolated_from_preview7_and_production`
`completed a regular file` + `ValueError: Nat20 initial replay diverged`.
Это **два дефекта**, оба настоящие:

**(а) Доказанный дефект кэша проверки контента.** `verify_tree` кэширует
`(size, mtime_ns, ctime_ns, dev, ino)` -> SHA. Тест в течение **одной
секунды** откатывает файл байт-в-байт: inode прежний, а `mtime`/`ctime` с
наносекундной точностью возвращаются к прежнему значению. Строка 129
`json.dumps` без `default=` падает `TypeError`. Предлагаемый порядок:
стабильный `default=str` в `json.dumps` (сейчас — причинный баг), решить,
достаточно ли `mtime_ns+ctime_ns+ino` как ключа (на Linux — да, но по
философии файла «cache availability never establishes trust» можно дополнить
ключ `st_size` + `st_blocks` или отказаться от кэша для файлов, изменённых
в последние N секунд). Выбор — твой, но приёмка:
`.venv/bin/python -m pytest tests/test_nat20_release_manifest.py tests/test_nat20_bless.py -q`
зелёные, и `verify_tree` **по-прежнему ловит** настоящую порчу (проверь
мутацией вручную в своём прогоне).

**(б) Фикстура `tests/fixtures/nat20-preview7-fighter-combat.json` снята на
прежнем пине движка.** Я распаковал её руками и сравнил покомандно:
- `initial`: `active_effects` пусты, совпадает.
- `commands`: `[{move 3,1}, {attack goblin}]`, 2 записи.
- Расхождение: `initial_digest` расчёта — `568a5984…` против записанного
  `12cb4446…`; в `view.initiative[*]` появились поля
  `attuned_items`, `chosen_features`, `hit_dice_remaining/total`,
  `ignored_difficult_terrain`, `languages`, `weapon_masteries`; удар героя
  теперь даёт `damage 4` (goblin 10→6) и после атаки
  `current_turn_index` остаётся `0`, а в фикстуре `1`; в `view.turn`
  `attacks_remaining` 1 вместо 0.
- **Две прошлые попытки провалились, не повторяй их:** (1) правка квот;
  (2) «пересчитать `initial_digest` и `result_digest`/сбросить поля одной
  механикой» — не сошлось, потому что изменилась сама форма события и
  семантика хода.

Что делать: **переснять фикстуру текущим движком** тем же маршрутом, каким её
снимал preview7 (создание кампании → `start_combat` → `move` → `attack`), и
сохранить в ней: `release_digest` (должен остаться
`374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75`), полный
`campaign` в формате `DurableState`, и комментарий-поле вроде
`captured_with_engine` с новым `initial_digest`. Тест `test_pending_release_…`
проверяет не числа, а **изоляцию релизов** — то есть новая фикстура должна
проверять: старый релиз читается, bless в нём не зарегистрирован, обновление
до нового релиза отвергается. Если переснять фикстуру нельзя без изменения
этого смысла — стоп, записать в отчёт и **не трогать**.

Та же фикстура (или тот же маршрут) кормит **#6, #7, #8** — проверь их в
одном заходе и режь одинаково.

### #6 `test_nat20_dash.py::test_closed_contract_old_fixture_and_legacy_boundary`
`Nat20 initial replay diverged`.

### #7 `test_nat20_prototype.py::test_atomicity_and_old_fighter_release_survive`
`Nat20 initial replay diverged`.

### #8 `test_preview8_release.py::test_real_preview7_fighter_restores_and_continues_without_stand`
`Nat20 initial replay diverged`.

(Регрессий при этом быть не должно: `tests/test_nat20_bless.py`,
`tests/test_nat20_new_process.py` — первый сценарий `test_nat20_new_process`
**ожидает** строку `initial replay diverged`, не ломай его.)

### #9 `test_pdf_nat20_start.py::test_incomplete_real_source_cannot_be_promoted[missing-Неизвестная локация NPC]`
`assert (not True)` на строке 178. Тот же корень, что `#1`: карантин
срабатывает, но с другой формулировкой причины. Решай вместе с `#1`: если
причина переименована намеренно — обнови параметр
`[missing-…]` в тесте; если нет — `prepared_world.py`.

### #10 `test_creation_options_batch.py::test_exact_prechange_options_and_one_release_one_read_per_selected_file`
Снимок `/api/nat20/options` устарел: у `animate-dead`, `create-undead`,
`find-familiar`, `find-steed`, `giant-insect` пропала пометка «нет типизированных
данных», в схеме появилось `require_complete_choices`, в подклассах
`class_slug`/`min_level`, у `default_build` лишние ключи сверх
`{background_slug, ability_score_method}`. **Каждое расхождение проверь по
коду**, а не пачкой: пометка «нет типизированных данных» на деле должна
означать «обработчика нет» — если в движке обработчик появился, снимок
правится; если просто потеряли пометку — это регресс, чинится код.
`require_complete_choices`, `class_slug`, `min_level` — новые обязательные
поля контракта, снимок правится. В отчёт — построчный список «поле → вердикт».

### #11 `test_desktop.py::test_legacy_import_coherent_databases_profiles_dpapi_and_no_runtime`
`RuntimeError: Windows DPAPI unavailable` (`solo/cloud_settings.py:99`).
Тест по устройству требует Windows-крипто. Честный выход: `pytest.skip` с
текстом «DPAPI доступен только на Windows», и рядом — проверка ветки отказа
(`no plaintext secret fallback`) на Linux, чтобы смысл «на Linux секрет не
пишется в открытом виде» остался проверенным.

### #12 `test_desktop_pdf_smoke.py::test_registered_pdf_hook_and_recreated_app_continue_without_repreparing`
`assert` в `solo/desktop_observations.py:116` — «повторное приложение
переподготавливает PDF». Это не Windows-специфика, это настоящая регрессия
подготовки. Чини код, а не тест, если докажешь, что повторный `prepare`
действительно происходит (приложи трассировку/счётчик).

### #13 `test_frozen_pdf_smoke_fixture.py::test_bundled_pdf_transport_through_registered_api_and_continue`
`503` на `/command-turn` по замороженной PDF-фикстуре. 503 в этом проекте —
глобальный обработчик `ValueError` (`solo/app.py:117`), настоящая причина
внутри. Достань её (лог/`--tb=long`/подпроект с `raise`) и решай по факту:
дефект кода — чини; фикстура устарела — обнови и объясни.

## 6. ПРИЁМКА (я проверю сам, отчёт — не доказательство)

1. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_<файл>.py -q --tb=line -p no:cacheprovider`
   по **каждому** из 13 — вывод приложить.
2. Полный прогон пакетами через
   `/home/hermes/.hermes/cache/scratch/nat20run/baseline30/run.py`
   (с удалением старых `results.jsonl` перед прогоном или новым каталогом):
   итог должен быть **0 failed**, `passed` > 2363. Строку `TOTALS` приложить.
3. `python scripts/real_changes.py .` — «real content changes» не должно
   вырости незакоммиченным шумом CRLF.
4. Коммит явным списком файлов, сообщение в стиле проекта (`r125: …`).
   В отчёте — SHA.

## 7. ОТЧЁТ (15 строк максимум, по-русски)

```
#1 ok|устарел|дефект|skip — причина одной строкой
#2 … #13 то же
КОММИТ: <sha>
ПОЛНЫЙ ПРОГОН: <TOTALS>
ГДЕ ОСТАЛСЯ НЕ УВЕРЕН: <строки, которые мне надо проверить руками>
```

Без пересказа кода. Плохое — первым.

## 8. ПОСЛЕ ЭТОГО (не делать сейчас)

Следующая задача — недостающие правила PHB/DMG в корпус и обработчики.
Отдельная карточка. В этой — только баги.

## 9. Записать точку продолжения

В `reports/ENGINE_WORKSPACE_PROGRESS.md` добавить раздел «r125: разбор 13
падений» с: что было, что сделано, что осталось, следующий шаг. И обновить
`STATE.json` (`proven` / `not_proven`). Это обязательное условие приёмки —
иначе следующий агент начнёт читать репозиторий заново.
