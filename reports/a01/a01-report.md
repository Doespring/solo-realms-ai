# A01 / F04 / P1 — резервная копия при нарушенных связях SQLite

**Дата:** 08.11.2026. **Ветка:** `work/nat20-engine`. **Стенд:** `runtime/a01-live` (порт 19277), живая `runtime/web-local/` не тронута.

## Что было

`solo/user_data.py` до правки: `check_db()` первой же проверкой бросал
`ValueError('SQLite содержит нарушенные связи')`, а `snapshot()` вызывал её ДО копирования.
Итог на экране «Настройки → Данные и копии»: копия не создаётся, список копий пуст,
«Экспорт данных» файла не даёт.

## Что сделано

1. **Снимок раньше проверок.** `snapshot()` (`solo/user_data.py:86`) снимает файл через
   `sqlite3.Connection.backup` из соединения `mode=ro`, затем читает уже КОПИЮ
   (`sqlite_health`, `user_data.py:35`). Живая база только читается: ни записи, ни
   смены журнала, ни `-wal`/`-shm` в ней не появляется.
2. **Диагностика вместо исключения.** `foreign_key_problems()` (`user_data.py:15`) на каждую
   находку `PRAGMA foreign_key_check` дописывает таблицу, rowid, родителя, колонки и
   значение ключа. Отчёт `integrity-report.json` кладётся рядом с копией
   (`integrity_report`, `user_data.py:74`) и едет внутрь ZIP при экспорте.
3. **Сирот никто не удаляет.** Ни копия, ни восстановление, ни уборка копий не меняют
   записи: `consume_restore` возвращает базу как есть, `prune_backups` трогает только папки копий.
4. **Экспорт не молчит.** Ошибки `prepare_export`/`write_export` отдают 409 с русской
   причиной и кодом (`export_too_large`, `export_no_space`, `export_failed`); повреждённая
   база уезжает сырым снимком с отчётом и заголовком `X-Solo-Integrity`.
5. **Повреждение ≠ нарушенная связь.** Порча файла снова отказ (копию нельзя восстановить),
   нарушенные связи — находка в отчёт. Разделено в `snapshot()` (`user_data.py:100-114`).

## Числа приёмки

| Пункт | Доказательство |
|---|---|
| 1. Копия на базе с сиротой | `POST /api/data/backup` → 200, файл `backups/manual-…/solo.sqlite3` = **294 912 Б** (`reports/a01/live-result.json`) |
| 2. Живая база не изменена | md5 до/после нажатия кнопки на стенде: `a525b9b1bf171dda240f7df5908e320c` → **тот же**; тест `test_live_database_is_not_changed_by_the_copy` |
| 3. Таблица, rowid, родитель | отчёт: `{"table":"checkpoints","rowid":1,"parent":"campaigns","columns":[{"column":"campaign","parent_column":"id"}],"values":{"campaign":"нет-такой-кампании"}}` |
| 4. Восстановление | `plan_restore` → `consume_restore` → `PRAGMA integrity_check` = **ok**, сирота на месте; тест `test_restore_from_diagnostic_copy_passes_integrity_check_and_keeps_orphan` |
| 5. Экспорт при сбое | порча базы: копия → 409 `backup_refused`, экспорт → 200, `X-Solo-Integrity: broken_links=0; damaged=1; links=damaged`, в ZIP `solo.sqlite3` + отчёт «повреждён»; лимит размера → 409 `export_too_large` |

Проверки:

```
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_backup_with_broken_links.py -q
21 passed
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_backup_with_broken_links.py \
  tests/test_data_export_and_render_budget.py tests/test_core.py tests/test_delete_campaign.py \
  tests/test_nat20_save_data_consistency.py tests/test_memory_fts_orphans.py \
  tests/test_desktop.py tests/test_obsidian_assets.py -q
121 passed, 2 skipped
.venv/bin/python scripts/probe_a01_backup_live.py        # весь путь по HTTP на своём стенде
node scripts/probe_a01_ui.cjs <base> <out>              # кнопка на экране (нужен браузер; см. ограничения)
```

## Что нашли два критика и что исправлено

- **Блокер:** `plan_restore` убирал старые копии ДО записи плана — при полной квоте
  выбранная копия удалялась, а `pending-restore.json` оставался, и приложение больше
  не открывалось («Копия не найдена» на каждом запуске). Теперь план пишется первым,
  а `prune_backups` получает `pinned={выбранная, страховочная}` (`data_management.py:39-52`, `:91`).
- **Важно:** повреждённая база давала «успешную» копию и экспорт с `links=ok`. Теперь
  копия с повреждением — отказ, а экспорт отдаёт файл с `damaged=1` и русской причиной.
- **Важно:** сбой сборки ZIP отдавал 500 с `[object Object]` на экране. Теперь 409
  `export_failed`, экран читает и строку, и объект `detail`.
- **Мелочи:** `sqlite_health` больше не выпускает `DatabaseError` наружу и всегда
  выставляет `ok`; `import_legacy`/`consume_restore` убирают за собой `.import-*`/`.restore-*`;
  план восстановления пишется заменой (`os.replace`), поэтому обрыв записи не оставит
  половину JSON; `integrity_report` внутри `try` в `prepare_export`; экран показывает
  `summary` из ответа, а в списке копий — пометку «файл повреждён». Страховочный
  `.rollback-*` остаётся намеренно (материал отката), но ничем не выметается.

## Ограничения (честно)

- Полный отчёт вкладывается в тело `POST /api/data/backup`. На 2 000 сирот это ~350 КБ
  (замер критика), на порядки больше — будет тяжело. Ограничение детализации не делал:
  приёмка требует «каждую нарушенную запись».
- `rules-knowledge.sqlite3` (база знаний правил) не входит в `MANAGED`/`EXPORT_FILES` —
  это существовало до A01; в копию и экспорт она не попадает. Отдельная задача.
- Повреждение под работающим приложением на экране не воспроизводится: приложение на
  такой базе не стартует, поэтому UI-путь «damaged» проверен на уровне HTTP.
- `sweep_exports` по-прежнему вызывается только при следующем экспорте.
- **Открытый остаток (не блокер приёмки, нужен хозяин `desktop_main.py`):** если план
  восстановления всё же не сработает (выбранная копия удалена вручную, план испорчен
  снаружи), `consume_restore` в `desktop_main.py:11` вызывается без обработки ошибок и
  окно не откроется — тот же класс, что закрытый блокер, но причина другая. Правильное
  место правки — старт приложения: негодный план отложить в `restore-failed-*.json`
  и открыть профиль как есть. Первичная причина (уборка выбранной копии) уже устранена,
  запись плана сделана атомарной.
- Полный `pytest` (3 318 тестов, ~1 ч 40 мин) на этой ветке имеет 99 падений baseline
  (`reports/r228/pytest-full.log`); гонялся только круг затронутых модулей.

## Следующая операция

Перенос в основное приложение делает интегратор: правки касаются `solo/user_data.py`,
`solo/data_management.py`, `web/obsidian.js` и двух тестов-старожилов
(`tests/test_desktop.py:61`, `tests/test_obsidian_assets.py:54` — ожидание `DatabaseError`
заменено на русский `ValueError('SQLite повреждён…')`).
