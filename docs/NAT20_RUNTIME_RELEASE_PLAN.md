# Nat20: план двух неизменных runtime releases

18.10.2026. **Только план.** Preview9 фиксирован; dual runtime не реализован,
сборка/установка/смена pins не выполнялись. Repair v2 ещё требует независимого
review. Наличие будущего patched runtime само по себе не разрешает Bless.

## От чего отталкиваемся

`Nat20GameEngine._worker` уже запускает disposable subprocess: source
`python -m solo.nat20_worker`, frozen `SoloRealmsAI.exe --nat20-worker`;
UTF-8 JSON, timeout 45 с, CREATE_NO_WINDOW, ошибка — без commit.
Worker проверяет recipe и replay; транзакции, request ID и expected_version
остаются у существующего адаптера.

`DurableState` сейчас имеет `nat20-state-v1`, engine_version `0.7.0`,
release_digest, seed, definition и combat recipe. `nat20_content.release()`
сверяет package versions и все upstream Python hashes; его digest объединяет
runtime hashes, content manifest и dice versions. **Этот digest не является
только content ID.** Frozen spec собирает один engine namespace/PYZ.
`desktop_main --nat20-worker` импортирует его до какого-либо выбора runtime.

## Минимальное решение

Сохранить тот же subprocess/JSON/replay путь. Для будущего выпуска подготовить
два отдельных worker-only bundle с собственными import roots и manifests:
старый exact engine + совместимый зафиксированный worker/adapter и будущий
проверенный fork. Не менять установленный engine в рабочей .venv; не подменять
`sys.modules` и не импортировать две версии одного namespace в процесс хоста.

| Привязка | Runtime / контент | Политика |
|---|---|---|
| Старый v1 digest `374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75` | Nat20 0.7.0, commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`, прежний worker protocol и минимальный fighter corpus | Точная legacy alias → старый bundle. Оба зарегистрированных fighter и прежний recipe с definition=null; старые bytes/JSON-форму не переписывать |
| Будущий новый recipe | Отдельный runtime ID + content ID + их hashes | Пока НЕ зарегистрирован/НЕ активен. Точные hashes назначаются после review/build gates, не берутся из названия v2 или номера 0.7.0 |
| Неизвестный/несогласованный digest или пара IDs | Никакой | Явный отказ; не выбирать latest/старый/legacy Solo fallback |

Старый bundle должен фиксировать **и worker/adapter**, не только upstream
orchestrator: обновление общего replay-кода тоже может изменить результат.
Worker-only baseline собирается из сохранённого совместимого снимка, сравнивается
с worker Preview9 на настоящих старых saves и затем остаётся immutable.
Нынешний in-app `--nat20-worker` остаётся legacy входом до отдельного перехода;
не считать его неизменным, если его общий adapter уже переписан.

## Минимальные файлы и identity

- Доверенный bundled реестр, например `integration/nat20/runtime-releases.json`:
  runtime ID → относительный worker path + manifest hash; content ID → проверенный
  content manifest hash; разрешённые пары/редакция/protocol и точные legacy aliases.
  IDs/пути не принимает AI, HTTP-команда или пользовательская кампания.
- `runtimes/<runtime-id>/manifest.json` и worker-only EXE с его библиотеками:
  parent commit, одобренный patch hash, полная карта code/bundle hashes, schema/
  protocol и фактические Python/d20/Lark/cachetools versions. Исходники schema
  package dnd5e_srd_data относятся к code identity. Старый UPSTREAM.json не менять;
  fork получает собственный честный manifest, не выдаётся за немодифицированный pin.
- Контент оставить по существующим путям `integration/nat20/imported` и
  `playable`, не переносить ради структуры. Старые manifest/documents/profiles
  immutable. Новый corpus — отдельный release только после проверки dependencies.
  Content hash связывает manifest, необходимые typed documents, encounter profile,
  definitions и их hashes/редакцию/provenance.
- Будущий recipe v2 хранит явные `runtime_release_id`, `content_release_id` и
  execution digest, связывающий обе identity с protocol. Старый v1 читается через
  строгий отдельный вариант DTO и legacy map; ему не добавляются null/новые поля,
  не пересчитываются старый digest или подписи Memory.

Code hash и content hash независимы: правка engine создаёт новый runtime,
правка игровых данных — новый content release. Совместимость пары разрешается
реестром; одинаковый `engine_version=0.7.0` её не доказывает.

## Выбор и frozen packaging

1. Dispatcher выбирает identity из сохранённого state/recipe до запуска worker;
   bootstrap нового процесса делает выбор до его engine-specific imports.
   Проверяются формат и точное соответствие реестру. Новая кампания
   получает пару из доверенного зарегистрированного definition, не из «latest».
   Старый fighter продолжает старый runtime при каждом execute/verify/availability/
   restore, включая checkpoint и undo.
2. Запускается только зарегистрированный worker. Хост сверяет bundle/manifest
   hashes; worker проверяет собственную identity, origins импортированных модулей,
   версии и code/content hashes, затем recipe и replay. Ответ обязан соответствовать
   запрошенной паре/protocol. Старые hash gates не отключаются.
3. В будущем PyInstaller собирает workers отдельно из изолированных source trees;
   основной portable ZIP включает их целиком с собственными dependencies/metadata,
   grammar d20/Lark и лицензиями. Два engine не складываются в общий PYZ namespace.
   Bootstrap worker импортирует stdlib/маршрутизацию до выбора engine, не GUI.
4. В source-режиме — отдельные trusted roots через bootstrap до engine import;
   PYTHONPATH пользователя/установленная latest-библиотека не являются selector.
   Если публичные native types изменятся, текущие host imports PartyMemberSpec/
   CharacterBuildSpec требуют отдельного DTO-compatibility gate, не import shim.

Неизвестный release, отсутствующий файл, неверный hash/protocol или replay
divergence дают отказ с точной причиной; кампания и журнал сохраняются. Нет
автозагрузки пакетов, implicit migration, смены backend или платного повтора.
Явная миграция — отдельное разрешённое действие с backup/проверкой, не этот план.
Тестовые env-флаги не разрешают pending runtime/профиль во frozen.

## Gates до реализации/выпуска

- Закрыть ownership-дефект общего upsert и review repair v2; сохранить остальные
  gates Cleric/V/S/M/post-combat clock. Engine patch не активирует заклинания.
- Новый EXE читает реальный старый save/checkpoint: fixture preview7 и сохранённый
  QA-бой preview8/9. Выбран старый worker; state/view/effects/events после replay
  и следующей той же команды совпадают с baseline. Сохранение/новый процесс/
  продолжение, duplicate ID/stale/rollback/undo не меняют runtime identity.
- Отдельно проверить будущую пару: её численные/events/RNG expectations, сохранение
  и restart. Старый worker отказывает новой паре, новый не перехватывает старую.
- Негативные проверки: unknown IDs, подмена manifest/code/content, несовместимая
  пара/редакция, missing bundle, schema mismatch, timeout/crash/divergence.
  Никаких записей/снятия pending/fallback; native Windows smoke без system Python.

Изменения будущей реализации ограничить dispatcher `nat20_api._worker`,
worker bootstrap/registry, versioned recipe DTO и packaging workers.
Store/events/Memory/UI и механический расчёт заново не строить. Их существующие
границы и контрактные проверки сохраняются. Сейчас изменён только этот документ;
следующий шаг — дождаться независимого v2 review.
