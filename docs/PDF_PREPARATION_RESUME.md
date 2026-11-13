# Серверный режим возобновления PDF-подготовки

Контракт, 18 октября 2026. Владелец backend меняет PreparationPipeline и свои
тесты; Сборка подключает creation projection, Интерфейс использует поля сервера.
app.py/creation.py/web/engine/memory, fingerprints и хорошие blocks не меняются.

## Публичные поля status / latest

```json
{"preparation_mode":"world_structure","source_ruleset":"dnd2014"}
```

- preparation_mode: только world_structure | source_extract | unknown.
- source_ruleset: dnd2014 | dnd2024 | null; сохранённая редакция подготовки
  источника, а не выбранная позднее механика Nat20 и не догадка по имени PDF.
- Поля присутствуют уже в queued/running, остаются в partial/failed/cancelled,
  после нового процесса и локальной проверки. Они не означают world-ready.
- Метаданные сохраняются в существующем JSON result строки creation_runs,
  атомарно при queued. Новых таблиц/колонок/миграций для этого изменения нет.
- Старые записи: допустимо восстановить режим из явного сохранённого boolean
  start_contract.world_structure. Без него и новых metadata — unknown, даже если
  done==total/status==ready. Редакция может быть известна независимо от режима.
- Если edition нет в metadata/definition, status может вернуть единственную
  сохранённую редакцию из creation_batch_history. Из истории не выводится режим.
  При противоречивых редакциях возвращается null. Текущий ruleset героя/черновика
  не используется как подстановка вместо сохранённой редакции источника.
- В public JSON не возвращаются metadata целиком, private body, quotes, documents,
  prompts или ключи. Существующие безопасные счётчики/диагностики остаются.

## UI / wiring

`pipeline.status(run_id)` и `pipeline.latest(draft_id)` возвращают эти поля.
Существующий creation GET уже включает latest в preparation; Сборка при нужной
дополнительной проекции берёт эти два поля из latest, не из private raw/result.

Для resume world_structure UI явно отправляет world_structure=true, сохранённый
source_ruleset, consent и новый request_id. Для source_extract — false. unknown
требует явного выбора режима/редакции; неизвестное не заменяется false автоматически.
Просмотр статуса и local readiness не отправляют книгу. Переиспользование блоков
решает прежний fingerprint/signature механизм; метаданные не входят в него.

Сервер дополнительно блокирует новый запрос с пропущенным world_structure после
world/unknown run: HTTP409 до HTTP провайдеру, книга не отправляется. Явный false
остаётся отдельным выбором компактного режима, а не автоматическим resume мира.
Тот же request_id с прежними параметрами возвращает исходный run без нового вызова.

## Реализация и сохранность

Изменены только preparation_pipeline.py, preparation_contracts.py, собственный
tests/test_preparation_resume_metadata.py и этот отчёт. В существующем JSON result:

```json
{"preparation_metadata":{"preparation_mode":"world_structure","source_ruleset":"dnd2014"}}
```

Это допустимое промежуточное значение result без definition/quarantine; status
показывает summary=null. Финальный result сохраняет metadata вместе с прежней
проверенной структурой. Progress/cancel/error/restart не удаляют JSON. Локальная
readiness переносит metadata и имеющийся start_contract в новую локальную запись,
сохраняет исходную edition при проверке и не вызывает AI. Конфликт исходных блоков
с записанной edition остаётся конфликтом валидации, а не переключает её молча.

Для unknown публичный ready=false даже при старом status=ready/37 из 37. Локальная
проверка не применяет такой результат в Draft как новый готовый мир. world_result
не принимает unknown/source_extract за отдельное подтверждение структурного мира.
Известность режима сама по себе не снимает source/ACL/world-confirm/stale guards.

Fingerprint/signature, source parts, их raw/body/attempts и правила тарификации
не менялись. Метаданные не подмешиваются в ModelConfig или generation identity.
Расширена только Pydantic-схема безопасной проекции; новых таблиц/колонок нет.
Creation API/web/engine/memory/.3 dist, готовые source_extraction hotfix и ZIP
не изменялись. Личные профили, source/user/project DB/WAL не открывались.

## Целевые проверки

Новый изолированный стенд, SOLO_TEST=1 до app, MockTransport; никаких живых/платных
AI-вызовов. Для resume используются синтетические TXT-блоки; настоящий синтетический
PDF и Nat20-путь переиспользованы из предыдущего smoke, а не заменены обещанием.

- queued и наблюдённый running возвращают world_structure / dnd2014;
- partial structured сохраняется на сервере и читается в новом реальном процессе;
- creation GET `.preparation`, status и локальная readiness возвращают режим/edition;
- локальная readiness делает **0 AI-вызовов**;
- после перезапуска явный resume запрашивает **только ранее упавший блок 1**;
  signature/fingerprint/body hash/attempts каждого хорошего блока прежние;
- тот же request_id идемпотентен; пропущенный mode даёт 409 и 0 HTTP-вызовов;
- повторён тот же сценарий с удалённой metadata старого partial: сервер честно
  показывает unknown и сохранённую edition из истории; после явного выбора full
  хорошие блоки тоже переиспользуются;
- legacy ready/37 из 37 без режима не становится world-ready; неизвестные,
  ошибочные и не-boolean старые признаки не повышаются до world_structure;
- private source/body/key canary отсутствует во всех публичных ответах/проекции;
- прежний PDF → явное source2014/mechanics2024 → Rogue → разговор/проверка → save →
  новый процесс проходит с новыми метаданными.

Финальный прогон: **10 passed**, 21,90 с (9 новых проверок + прежний PDF/Nat20 E2E).
Ранее в этом этапе отдельно прошёл прогон **11 passed** с двумя прежними resume /
split-after-restart регрессиями; результаты не выдаются за полный suite.
Предупреждения только прежние Starlette/lark deprecation. Full/EXE/build не запускались.

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_preparation_resume_metadata.py tests/test_pdf_nat20_start.py::test_real_pdf_confirmed_world_2014_with_explicit_2024_rogue_and_continuation --basetemp "$env:TEMP/solo-pdf-resume-mode-tests"
```

Ограничение: старый partial без явной metadata/boolean режима невозможно достоверно
распознать как world по одному hash/fingerprint. Возвращается unknown, нужен выбор
человека. Последующий reuse зависит от прежнего generation identity: другая модель,
параметры или материалы намеренно не считаются теми же оплаченными блоками.

Очистка: собственные TestClient и дочерние процессы завершены. Изолированный
`C:\Users\iwa41\AppData\Local\Temp\solo-pdf-resume-mode-tests` оставлен для
согласованной очистки. Новая попытка удаления не выполнялась; прежний policy-отказ
не обходился. Исходники/тесты/отчёт сохранены, чужие стенды и .3 артефакты не тронуты.
