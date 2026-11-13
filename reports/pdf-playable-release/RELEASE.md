# Windows preview 2.2.0-prototype.5 — 18 октября 2026

Сборка принята в указанном объёме: один matched-source gate и два запуска настоящего EXE прошли. Полный synthetic PDF API-путь в frozen теперь проверен; пользовательские книги и живой AI этим не сертифицированы.

## Запуск

Распаковать `dist/SoloRealmsAI-Windows-2.2.0-prototype.5.zip` целиком в пустую папку, запустить `SoloRealmsAI.exe`, сохранив `_internal` рядом. Бесплатная игровая проверка — `Offline-Mock.cmd`, отдельный профиль с явной меткой MOCK. Специальная PDF QA-фикстура автоматически в нём не включается.

Готовый EXE: `dist/pdf-playable/SoloRealmsAI-Prototype/SoloRealmsAI.exe`. Корневой `Desktop.cmd` не переключён автоматически. Нужны Windows10 19041+/11, WebView2 и .NET; Python отдельно не нужен. Installer/цифровой подписи нет. Обычный профиль `SoloRealmsAI-Prototype-1`, offline-профиль с суффиксом `-Offline`; schema12 не менялась.

## Реальные изменения

- Параметры существующих Native-действий показываются по выбранному действию/серверной схеме: заряды, направление, ячейка, запас способности и применимые дополнительные поля. Клиент не вычисляет механику. Пустые значения не превращаются в нулевое перемещение; повтор сохраняет прежние параметры/версию/request_id.
- Добавлена строго явная QA-фикстура в существующий desktop smoke: только `--smoke-test`, `--smoke-scope pdf-world`, `--offline-mock`, `--data-dir` и `--smoke-pdf-phase prepare|continue`. Первая фаза требует новый пустой профиль, вторая — marker успешной первой фазы. Нормальные/default-профили отклоняются. Обычный player/cloud/OfflineMock путь не заменён фикстурой.
- Фикстура генерирует настоящий одностраничный текстовый PDF, отвечает только через закрытый `httpx.MockTransport`, без внешнего fallback/redirects/DPAPI. Пакет проходит стандартные source/JSON/consent/world/Native проверки; готовый мир напрямую не подставляется. Hooks не импортируют tests/scripts из EXE.
- Переносимый Windows output `.5` отделён от `.1–.4`. Новых правил, DTO, арифметики или fork Nat20 не добавлено.

## Проверки

**Один matched-source gate: 11 passed, 22,51 с**, три прежних Starlette/lark deprecation warnings. Новый hook/guards5 + host4 + packaging2. Renderer в этом API-тесте — явно обозначенная заглушка, app пересоздаётся в одном процессе; это отдельно от native-доказательства ниже. Полный suite не запускался. `regression.xml`, `regression.log`.

Переиспользованы отдельные owner-прогоны: UI1 PASS/50,90 с и factory1 PASS/12,51 с, а также `.4` baseline24 PASS. Они не суммируются и не выдаются за один чистый suite. Настоящий browser/API UI-прогон проверил Wand2 charges с потерянным ответом/однократным расходом, Burning Hands с ячейкой/направлением и Lay on Hands3 pool points; отдельные dispatch/409 fixtures отмечены в `docs/NATIVE_ACTION_PARAMETERS_UI.md`.

**Сам собранный EXE, свежий тестовый профиль, две отдельные OS-фазы:**

1. PID8220: настоящий PDF upload/import → стандартная preparation API с явным согласием → подтверждение мира → Native Rogue/human с тремя подтверждениями → осмотр, разговор с NPC и проверка → API-сохранение. Получена версия4. Исходная редакция2014 и выбранная механика2024 сохранены отдельно; фиктивного боя нет.
2. PID24988: повторно запущен тот же EXE/профиль. Совпали native SHA, canonical screen snapshot и результат Native replay/restore; сохранённый checkpoint присутствует. Следующая проверка дала **4 → 5**, новых preparation-запросов **0**.

Реальные Edge Chromium окна/локальные шрифты и ресурсы загрузились, оба окна/backend закрыты. Пробы выполнялись через настоящий встроенный HTTP API; это не новый полный проход по кнопкам Windows UI. Полный процесс занял21,54/11,30 с; сама QA-проба13,95/4,21 с. Метрики `ready_ms` относятся к отсчёту loaded-callback существующего hook, не к полному времени запуска.

Доказательства: `native-prepare.json`, `native-continue.json`, `native-*-process.json`, `native-state-proof.json`, `mock-packets.jsonl`.

## Известная ошибка фикстуры и расходы

Принято **8 mock-пакетов**: preparation1, Interpreter4, Narrator3. В отдельном synthetic ledger **9 записей**: 8ok и **1 Narrator http_400** для `pdf-look`. Это не ноль ошибок AI.

Причина установлена по turn_ref/логам и коду: пакет осмотра из `prototype_gm` содержит `place/atmosphere/confirmed/npc/memory`; узкий fixture predicate распознаёт Narrator только по `outcomes/player_words/description_sources`. Он отклонил осмотр локальным400, не записав его в журнал принятых пакетов. Существующий `engine_fallback` выдал подтверждённое описание сцены; игровой `command-turn` вернул200/status observation. Диалог, проверки, сохранение и продолжение завершились. Это ограничение покрытия mock-фикстуры; `.5` не пересобиралась ради него. Детали — `synthetic-ledger-summary.json`.

Поле `mock_transport_requests` native JSON считает принятые записи, поэтому8 не равно9 ledger entries. Отдельного главного `cloud-usage.sqlite3` нет: create_app переиспользует ledger фикстуры. Реальных провайдерских вызовов и оплаты — **0** благодаря закрытому транспорту; synthetic usage/cost не являются реальными токенами/расходами и не списывают пользовательский резерв. Пользовательские API-профили/история расходов не подключались.

## Артефакты и сохранность

ZIP: `dist/SoloRealmsAI-Windows-2.2.0-prototype.5.zip`, **39 915 258 байт**.

SHA256: `51c7e3056f216c292d32885b2a9847fb78926c9878fd18b839d4442d4e6397e0`.

EXE: **12 635 120 байт**, SHA256 `bb6a2c3e8c4b86a1cf0e67c62bddf38e8e4f23c0f607f2f0bac3729991f3cfa4`.

**2193 файла / 81 448 776 байт** распаковано: Python3.12.10/Windows зависимости, локальный UI/ассеты/шрифты/промпты, два публичных asset-каталога, прежний pinned Nat200.7.0/1663 canonical документа и replay-данные, README/KNOWN_ISSUES/девять PDF/Rules/UI QA-документов. Нет пользовательских баз/ключей/книг/локальных моделей. Точный состав с каждым хешем — `release-manifest.json`.

CRC и все ZIP/bundle хеши прошли, extras нет. На момент приёмки код/включённые документы совпали с source snapshot, pin и старые ZIP/EXE/manifests `.1–.4` не изменены. Root независимо подтвердил это без повторного runtime: `root-independent-verification.json`. Drive frozen `.3` не изменялся, загрузок этот чат не выполнял.

Пользовательские SQLite/AppData не подключались и не мигрировались. Host guard сравнивал только byte hashes project data. Прежний data-preservation incident остаётся **OPEN**. Локальные fallback не подтверждают новые факты. Неподдержанные обязательные механики, upstream Rage/Bless/Hide, ограничения магии/смерти/полного D&D2024 остаются в `KNOWN_ISSUES.md`; arbitrary user PDF/live AI quality не проверены.

Очистка: попытка удалить только новые собственные build/tmp-regression/native-profile после остановки процессов отклонена автоматической проверкой `blocked by policy`. Остались **265 файлов / 60 738 588 байт**, пути/проверка границ в `cleanup-plan.json`, итог `cleanup.json`. Повторов/обходов и удаления старых blocked стендов нет. Итоговые артефакты и минимальные QA-доказательства сохранены; общая очистка не объявляется завершённой. Production frozen для передачи root.
