# r148 — PDF → герой: большой файл идёт через библиотеку, а не отбивается лимитом 8 МБ

## ЦЕЛЬ

В диалоге «Начать приключение» (`?ui=chat`, кнопка «Загрузить приключение (PDF или текст)»)
файл больше 8 МБ должен приниматься: уходить в «Библиотеку приключений», читаться там
с прогрессом, после чего игрок попадает на шаг «Соберите героя». Маленькие файлы (≤8 МБ)
идут прежним путём, их поведение не менять.

## ЧТО ПРОВЕРЕНО ПРОРАБОМ (не перепроверяй с нуля, но и не верь на слово)

Живой прогон на `127.0.0.1:19215` (сборка `2.2.0-prototype.5`, коммит `0ec6e70`):

1. `POST /api/preparation/drafts` → черновик создаётся.
2. `POST /api/preparation/drafts/{id}/sources` с `runtime/books/Руководство Мастера 2024 v1.1.pdf`
   (57 МБ, 342 стр.) → **`{"detail":"Источник превышает 8 МБ"}`**. Лимит:
   `solo/preparation.py`, `MAX_SOURCE = 8388608`.
3. Тот же файл через библиотеку:
   `POST /api/adventure/library` (сборник) → `POST /api/adventure/library/{id}/files?purpose=book`
   → job `ed623d85-…` **complete за <20 с**: `done=342 total=342`.
   Лимиты библиотеки: `solo/adventure_library.py` — `MAX_FILE=384 МБ`, `MAX_PACKAGE=768 МБ`,
   `MAX_FILES=300`; страниц до 2000 (`solo/adventure_worker.py`).
4. Что даёт импорт: 342 страницы в поисковом индексе, 342 материала `kind='page'`,
   477 кандидатов (`chapter` 188, `instruction` 266, `media` 8, `table` 15).
5. **Модулей после импорта ноль**: `GET /api/adventure/library/{id}/modules` → `[]`.
   `adventure_modules` заполняется только `AdventureLibrary.split_collection(...)`
   (ручная нарезка границ), автонарезки нет.
6. Экран уже умеет работать с готовым модулем: `web/preparation.js`, `soloChooseAdventure(bundle, module)`
   требует `book.import_complete` и `module.ready`.

## ЧТО СДЕЛАТЬ

1. **Сервер:** маршрут/ветка, которая для файла > `MAX_SOURCE` не отбивает его, а:
   создаёт (или переиспользует) сборник → грузит файл с `purpose='book'` → отдаёт `job.id`.
   Ответ должен называть ветку: `{"path":"library","job":"<id>","bundle":"<id>"}` — чтобы клиент
   не гадал, показывать прогресс или готовый разбор.
2. **Автонарезка модуля** после `complete`: предложить границы по кандидатам
   (`kind='chapter'` / `'adventure'`) и вызвать `split_collection` с `confirm=True`
   **только для непересекающихся диапазонов**. Если кандидатов нет — один модуль
   на весь документ (страницы 1..N) с `unsupported`-пометкой «границы не найдены».
   Модуль обязан появиться в `GET /api/adventure/library/{id}/modules`.
   Решение о границах должно быть в отчёте числом: сколько глав нашлось, какой диапазон у каждого.
3. **Клиент** (`web/play.js`, обработчик `#source-file`): при ответе `path="library"` —
   опрашивать `GET /api/adventure/jobs/{id}` и писать в `#source-status` проценты
   («Читаю книгу: 120 из 342 страниц»), по завершении — «Понято: страниц N, глав M,
   модулей K» и показывать `#source-next`.
4. **Тест** `tests/test_pdf_begin_large.py`: файл > лимита идёт библиотечной ветвью,
   маленький — прежней; автонарезка даёт ≥1 модуль; разметка ответа стабильна.

## НЕ ТРОГАТЬ

- `vendor/nat20/**` и `PATCHES.json` — пины не двигать.
- Поведение файлов ≤8 МБ (их путь в `solo/preparation.py` остаётся как есть).
- Лимиты памяти/системы на VPS: не поднимать `MAX_SOURCE` вслепую, большой файл должен
  читаться библиотекой постранично, а не грузиться в память целиком.
- `web/*.js` и `solo/*.py` в этом дереве — CRLF: править `read_bytes()/replace()/write_bytes()`,
  `write_file` их ломает.

## ПРИЁМКА (вывод команд, не слова)

```
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_pdf_begin_large.py -q
# ожидание: passed, 0 failed

curl -s -X POST http://127.0.0.1:19215/api/preparation/drafts -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:19215/api/preparation/drafts/<id>/sources \
     -F "file=@runtime/books/Руководство Мастера 2024 v1.1.pdf"
# ожидание: path=library, job=<id>, никакого «Источник превышает 8 МБ»

curl -s http://127.0.0.1:19215/api/adventure/jobs/<job>
# ожидание: status=complete, done=342

curl -s http://127.0.0.1:19215/api/adventure/library/<bundle>/modules
# ожидание: непустой список, ready=true
```

Плюс не сломать соседнее: `tests/test_pdf_pipeline.py tests/test_preparation.py
tests/test_three_sources_one_shape.py` — зелёные.

## ОТЧЁТ (15 строк max)

Коммит `r148: …` явным списком файлов. В отчёте: сколько страниц прочитано, сколько
глав/модулей нашла автонарезка, время импорта, какие файлы тронуты, что осталось.
