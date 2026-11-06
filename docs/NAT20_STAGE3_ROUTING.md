# Этап3 — единый маршрут команд Nat20

18 октября 2026. **Routing READY / source freeze**. Только исходники; общий итог этапа3 ожидает остальных стыков.

## Изменения и контракт

- `command_backend.native_backend` выбирает Nat20 по **наличию** ключа, включая повреждённое значение. Существующий command_backend использует тот же выбор; legacy не преобразуется в Nat20 автоматически.
- `CommandEngine.execute()` перед legacy-транзакцией передаёт Nat20-кампанию прежнему `Nat20GameEngine.execute`. Тот повторно читает authoritative state, проверяет версию/idempotence и атомарно записывает GameEvent/GameSnapshot/память. Второго commit, хранилища или своих бросков нет. Guard legacy-транзакции сохранён.
- `CommandEngine.resolve(current,command)` сохраняет **список результатов**. Для Nat20 новый чистый `Nat20GameEngine.resolve` использует прежние `_preflight`/disposable `_worker` и сериализованную копию DurableState с сохранённым seed. Он не меняет current, БД, ресурсы и persistent/global RNG. `preview` переиспользует этот seam и отбрасывает результат. Legacy resolve остаётся прежним переходом на переданной копии.
- `play_screen.commands_for` использует `CommandEngine(None).resolve(probe,...)`: caller не менялся, просмотр доступности не исполняет команду. Проверяется только выбранное действие, не полный каталог.
- В preview7 прежний stop при0HP заменён текущим dying → Native death saves через end_turn → подтверждённая смерть → end_combat/defeat_tpk без XP → восстановление checkpoint. Coverage сохранено. В command-cycle старый отказ прямому CommandEngine заменён успешным общим маршрутом.

## Аудит

Game.command-turn и HTTP commands уже выбирали Nat20 через command_backend; `game.py` не менялся. Поддержанные Nat20/prototype creation API уже создают явные Native definition/runtime. Старый `/api/campaigns` принимает несовместимый Hero/legacy DTO: он оставлен для совместимости, без молчаливого превращения в CharacterBuildSpec. Сохранения не мигрировали; hybrid/rulesets/combat не развивались. Nat20 pin/runtime/механики этапа2 не менялись; в nat20_api добавлен только чистый seam.

## Целевой прогон

**9 passed, 104,02 с**, один прогон; три прежних Starlette/lark deprecation warnings. Доказательства: `reports/stage3-routing/{tests.xml,tests.log,tested-source-sha256.json,freeze.json}`.

- Новые4 routing cases: Wizard/Monk check, short/long rest, start/turn/end combat, Native spell/feature. Pure resolve даёт тот же native_result/events, что последующее execute. Полный SQL dump, входной current и global RNG совпадают до/после resolve и availability. Ловушки запрещают legacy check/rest/combat.
- На исполнение приходится ровно одна journal row и пара GameEvent/GameSnapshot. Повтор не бросает заново. Проверены stale, memory-ingest fault/полный rollback, undo, отказ старому request на брошенной ветке, новый fork, replay и продолжение в новом Python-процессе.
- Повреждённый nat20 не вызывает fallback; stale probe не меняет состояние.
- Обновлённая zeroHP-проверка1, Native undo/common-backend1 и настоящий HTTP Native cycle/save/new-process1.
- Прежние legacy rollback и rest/early-finish2. Engine14 этапа2 переиспользован без повторения.

После прогона хеши всех шести изменённых файлов совпали с проверенными. Full suite, UI-проход, EXE/ZIP и живые/платные AI-вызовы не запускались. SQL-проверки относятся только к собственным одноразовым профилям.

Границы: pure resolve выполняет выбранную команду в disposable worker и отбрасывает результат; оно не гарантирует результат будущей изменившейся сцены. Hide/Bless и остальные upstream-ограничения не сняты. Прежние бинарники не отражают новые исходники.

Очистка: процессы завершены. Единственная попытка удалить проверенный `reports/stage3-routing/tmp-targeted` отклонена автоматической проверкой **blocked by policy**. Остались22 файла /6 197 248 байт; повторов/обходов нет. Исходники, тесты, XML, лог и хеши сохранены; чужие стенды не тронуты. Solarimus AI копирует root после принятия freeze.
