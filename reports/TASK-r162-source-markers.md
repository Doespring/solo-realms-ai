# r162 — маркер srd/phb у каждой записи, включая существующие id

## ЦЕЛЬ

Каждая запись контента несёт маркер источника — `srd` или `phb`. Решение
владельца 01.11: «На все id мы ставим маркеры srd и phb» + «Существующие тоже
подписываем». Значит маркер ставится и в **id существующих 332 правил**, а не
только у новых. Одна выборка по маркеру делит корпус на SRD-часть (CC-BY-4.0) и
PHB-часть (купленная книга владельца).

## ЧТО ИЗМЕРЕНО ПРОРАБОМ 01.11 (не перевыводить)

- корпус `rules_data/v2/chapters/*/*.json`: **332 правила**, у ВСЕХ `source` с
  `document_id='phb_2024_ru'`, sha256 книги, `pdf_page`, `printed_page`,
  `section`. То есть **все 332 — PHB-происхождения**, SRD-записей в корпусе
  нет ни одной.
- id сейчас: `dnd2024.phbNN.<тема>`; «phbNN» — номер главы, не лицензия.
  Уникальных id, на которые есть перекрёстные ссылки внутри корпуса
  (`dependencies`, `behavior_tests`): **343**.
- ссылок на эти id **вне** `rules_data/v2/chapters/`: **715** в 8 файлах —
  `tests/test_phb_classes_map.py`, `tests/test_phb_chapter_one.py`,
  `tests/test_phb_chapter_one_production.py`,
  `tests/test_hybrid_rules_production.py`, `scripts/build_phb03_classes.py`,
  `solo/hybrid_rules.py`, `rules_data/v2/coverage/phb-01.json`,
  `rules_data/v2/routing.schema.json`.
- канонические данные движка (`vendor/nat20/.../dnd5e_srd_data/canonical`):
  **1663 файла**, лицензия CC-BY-4.0 задана манифестом релиза, а не записью —
  они SRD по определению; их файлы не патчим, маркер приклеивает хост-слой.

## СХЕМА МАРКИРОВКИ (не менять без владельца)

- `phb2024.phbNN.<тема>` — запись из книги владельца: у бывшего
  `dnd2024.phbNN.<тема>` меняется ТОЛЬКО первый сегмент (`dnd2024` → `phb2024`),
  номер главы и тема остаются байт в байт, чтобы diff был читаемым.
- `srd2024.<категория>.<slug>` — запись из SRD 5.2.1 (новые; в корпусе пока
  таких нет).
- В данных рядом с id: `source.kind ∈ {'phb','srd'}`; для phb существующие
  `document_id`/`pdf_page`/`printed_page` не меняются.
- Записи движка получают маркер хост-слоем (`provenance='srd-5.2.1'` для
  пакета, `phb-2024-ru` для наших файлов подклассов из r147) — **вендор и
  `PATCHES.json` не трогаются**.

## ЧТО СДЕЛАТЬ (порядок обязателен)

1. Переименовать 332 id в корпусе механически: первый сегмент `dnd2024` →
   `phb2024`, остальное не трогать. Скриптом с проверкой, что изменились только
   эти строки.
2. Починить все ссылки на id: 343 внутри корпуса (`dependencies`,
   `behavior_tests`) и 715 в 8 файлах выше. Итог: ни одного `dnd2024.phb` в дереве.
3. **Пины.** `solo/hybrid_rules.py` держит SHA-256 глав (`PINS`,
   `CHAPTER_SHA256`): после правки id байты глав меняются — пересчитать пины
   ровно для изменённых файлов и показать зелёный `test_hybrid_rules_production`.
   `coverage/phb-01.json` привести в соответствие.
4. Добавить `source.kind='phb'` всем 332 записям.
5. Правило на будущее записать в `reports/ENGINE_WORKSPACE_PROGRESS.md`: новый id
   рождается сразу с маркером (`phb2024.*` или `srd2024.*`). r147 уже так делает
   для подклассов.
6. Страж-тест: запись без `source.kind` или с id без маркера `phb2024`/`srd2024`
   валит тест.

## ФАЙЛЫ

- `rules_data/v2/chapters/*/*.json`, `rules_data/v2/coverage/phb-01.json`,
  `rules_data/v2/routing.schema.json`
- `solo/hybrid_rules.py` (пины), `scripts/build_phb03_classes.py`
- 4 теста из списка выше
- новый `tests/test_source_markers.py`
- хост-слой маркера движковых записей: `solo/nat20_content.py` (там же, где
  обёртка загрузчика из r147) + проекция каталога `solo/nat20_prototype.py`

## НЕ ТРОГАТЬ

- `vendor/nat20/**`, `PATCHES.json`, `.venv/**/dnd5e_srd_data/**`.
- Тексты правил, `readiness`, страницы, статусы — меняется только id и
  добавляется `source.kind`.
- `/home/hermes/projects/SoloRealmsAI-app`, массовые удаления, `git add .`,
  `reset --hard`, `git clean`.

## ПРИЁМКА (вывод в отчёт)

1. `grep -rn "dnd2024\.phb" --include=*.json --include=*.py --include=*.js . | wc -l`
   → **0**.
2. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_source_markers.py -q` →
   N passed: все 332 с маркером, запись без маркера валит тест, проекция отдаёт
   маркер.
3. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_phb_classes_map.py
   tests/test_phb_chapter_one.py tests/test_phb_chapter_one_production.py
   tests/test_hybrid_rules_production.py -q` → 0 failed (пины пересчитаны).
4. `.venv/bin/python scripts/verify_pins.py --root .` → ноль расхождений.
5. Счёт: `srd`: N, `phb`: 332 по корпусу; сколько записей хост-слой отдаёт как
   srd по канону. Числа в отчёт.

## ОТЧЁТ (15 строк max)

Коммит `r162: <что>`. Сколько id переименовано, сколько ссылок починено, какие
пины пересчитаны, счёт по маркерам. Плохое — первой строкой.

## ПОТОЛОК

Два подхода. Не закрылось — отчёт фактами (какой тест или пин не сошёлся),
карточку не закрывать.
