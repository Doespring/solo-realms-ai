# r227 — жёсткая схема генерации (Structured Outputs)

## Что изменено

| Файл | Что делает |
|---|---|
| `solo/structured_output.py` (новый) | `strict_schema()` собирает JSON Schema из `Model.model_json_schema()`: убирает `default`/`title`, ставит все поля в `required`, `additionalProperties:false`; `strict_problems()` называет узлы, которые strict-режим выразить не может (свободный `data: dict`, `pattern`, `min/max`, `oneOf`); `sanitize()` чистит ответ по той же схеме и возвращает `{path,action,reason}`; `error_lines()` — ошибки pydantic построчно для ремонта. |
| `solo/cloud.py` | Схема уходит провайдеру как `response_format: {type: json_schema, json_schema:{name, strict, schema}}`. `strict:true` — только если схема strict-валидна; иначе тот же schema без флага (модель видит форму, провайдер не отвергает 400-й). Отказ именно схемы (400/422 + маркер неподдержки) — один откат на `json_object` и память на процесс; http-код и `schema_strict` остаются в метрике. |
| `solo/preparation_format.py` | `read()` — путь подготовки: лояльный разбор повторов ключей + санитайзер по обычной схеме модели (видно, какие поля реально обязательные). `decode()` остаётся строгой проверкой сохранённого wire-формата. `wire_schema(strict=True)` — версия для провайдера. |
| `solo/source_extraction.py` | `parse_answer()` — тот же санитайзер для компактного контракта `entries`. |
| `solo/cloud_setup.py` | Маршрут, заявивший `structured_outputs` в каталоге, получает `json_schema`, иначе `json_object`. |
| `solo/cloud_cost.py` | `schema_mode` пишется в журнал `calls`. |
| `solo/preparation_pipeline.py` | Один ремонт на блок (только для ошибок pydantic; ошибки ссылок и обрезанный JSON в ремонт не идут — они не лечатся повтором). Ошибки pydantic построчно + прошлый ответ в промпте. `part_metric()` пишет `schema_mode(s)`, `schema_strict`, `repairs`, `calls`, `bytes`, `schema_dropped_keys`, `duplicate_keys`, `duration_ms` в `creation_parts.metric`; `structure` прогона суммирует их. Потери первого ответа не теряются при удачном ремонте. |
| `tests/test_r227_structured_outputs.py` (новый) | 17 тестов на реальные формы из лога владельца. |

## Доказательства

- `tests/test_r227_structured_outputs.py`: **17 passed**.
- `scripts/probe_r227_preparation.py` — прогон пайплайна на 9 блоках, ответы модели дословно из журнала владельца
  (`runtime/web-local/preparation.sqlite3`), страницы — синтетический текст:
  `done 9/9`, `structural_errors 0`, `extra_inputs_present false`, `dropped_keys 25`, `repairs 0`.
  Итоговый статус прогона `blocked` — это проверка канона (цитаты не совпадают с синтетическими
  страницами), а не структура.
- `source_replay` в том же отчёте — каждый из 33 ответов владельца против **его собственных страниц
  книги**: 30/33 стали валидной моделью `PreparationBatch`, выброшено 272 ключа; 3 отказа:
  `JSONDecodeError` (обрезанный ответ), `SourceReferenceError` (выдуманный номер фрагмента),
  `ValidationError` (например `entities.2.location` не строка — это и есть вход ремонта).
- Полный обход `tests/` пакетами по 4 файла (`reports`/`~/.hermes/cache/scratch/nat20run/r227/results.jsonl`):
  **3310 passed, 100 failed, 42 skipped, 0 errors** (93 пакета, 0 таймаутов).
  База того же дерева до правки (пакетный прогон r225): 3286 passed / 110 failed / 39 skipped.
  Новых падений нет: единственное отличие, `test_session_continuity_restart::test_a_campaign_id_alone_resumes_the_session_on_a_fresh_app`,
  в одиночном прогоне проходит (3 passed) — флаки; в базе r225 его не было в падениях только потому,
  что файл попал в другой пакет. 11 падений базы (combat/dialogue/nat20_bless/companion) закрыты
  работой r225, которая шла в этом же дереве. Остальные 100 — известные: 43 `test_hybrid_rules_production`
  (`legacy_campaign_unsupported`, правила вне этой раскладки) и 37 `test_phb_chapter_one_production`
  (нет данных `rules_data`), плюс 20 разрозненных, все были красными и до правки.
- Целевой набор по затронутым модулям (cloud/preparation/source/creation/ai_roles): **223 passed, 1 skipped**.

## Что честно не сделано

- Живой вызов RouterAI не выполнялся: на этой машине не подтверждён ключ, а платный вызов без
  разрешения владельца не делается. Поэтому «провайдер принял схему» проверено на мок-транспорте
  (`test_provider_receives_response_format_json_schema_from_models`) и на откате
  (`test_schema_refusal_falls_back_to_json_object_once_and_is_remembered`).
- `strict: true` для подготовки не отправляется: `PreparedEntity.data: dict` — свободный словарь,
  strict-режим его не выражает (`additionalProperties` обязан быть `false`). Схема при этом уходит
  (json_schema без strict), а причины видны в `schema_strict`/`schema_bytes`. Это ограничение модели
  данных, а не обход требования: врать провайдеру «схема strict» и получать 400 — хуже.
- `SourceReferenceError` и `JSONDecodeError` не запускают ремонт: неизвестный номер фрагмента и
  обрезанный по `max_tokens` ответ повтором не лечатся; обрезка уходит в штатный split по
  `hit_token_limit`.
