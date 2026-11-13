# Этап 2 — UI READY / source freeze

18 октября 2026, Europe/Samara. Production только `web/game-screen.js` и `web/prototype-actions.js`. Backend/GM/engine/API/DTO не менялись, этап 3 не начат, `.5` immutable. Full/EXE/ZIP/live/paid не запускались.

## Подключение

- Существующие visible snapshot resources `death_save:successes`, `death_save:failures`, `death_save:is_stable`, `dead` показаны отдельным блоком героя: успехи/провалы, стабилизация либо подтверждённая смерть. Восстановление сознания при HP>0 не называется dying. Отдельной машины состояний или собственного d20 нет.
- Серверные offered commands отображаются прежним общим dispatch: при 0 HP в активном бою доступен `end_turn`, при смерти — `end_combat`. UI не добавляет свою награду/XP или законность хода. Вне боя серверный terminal при 0 HP сохраняется; служебное восстановление остаётся доступным.
- Существующая HTTP prototype-проекция уже выдаёт selected spell `status` через `describe`; дополнительный маршрут/поле не потребовались. При `narrative_or_unsupported_in_combat` выбранное заклинание получает понятную локальную пометку, submit отключён и защищён также от программного submit. Выбор Magic Missile снова разрешает отправку. То же ограничение применяется к выбору заклинания реакции/подготовки. Guards Hide/Bless не изменены.
- Все controls прежнего этапа Native parameters сохранены: pool/charges/direction/trigger/bonus/grip/shove и точная сериализация отсутствующих/false/0 значений.

## Один реальный browser/API smoke

`tests/test_stage2_ui.py` + `.cjs`: **1 passed, 50.44 с**. Настоящие registered API, pinned Native, изолированные synthetic campaigns, MockLLM, headless Edge **1280×800**, только loopback. `SOLO_TEST=1` до app import. Endpoint response fixtures **нет**, реальных AI-вызовов **0**, JavaScript ошибок **0**, memory.audit чистый.

1. Герой с 1 HP достигает 0 HP через реальные Native turns. Браузер показывает 1 успех / 0 провалов, terminal=null и доступную серверную кнопку «Закончить ход».
2. Служебное сохранение → end_turn → изменённые death counters → явное окно подтверждения restore. Snapshot resources восстановились точно; 0 HP/end_turn остались доступны.
3. Следующие Native turns подтверждают смерть; UI показывает её и принимает offered «Завершить бой». Python host подтверждает **experience=0** после поражения.
4. Реальная selected capability заклинания **Alarm** сообщает narrative/unsupported. UI объясняет ограничение, не отправляет даже программный submit; весь `/screen` до/после совпадает, wizard version остаётся **0** — action/slot не расходовались. Magic Missile остаётся доступным; статус Bless и guard Hide присутствуют.

Промежуточные красные результаты исправляли только ожидания нового test helper: null вместо undefined, обязательное окно restore, terminal после закрытия боя вместо ожидания активной строки ввода. Финальный полный узкий прогон зелёный. Статус stable выводится по серверному флагу; отдельный стабилизирующий natural-roll сценарий этим browser smoke не повторялся — engine-покрытие описано в `NAT20_STAGE2.md`. Raw Native death roll не визуализировался и не заменялся выдуманным natural d20.

Артефакты: [trace](../reports/stage2-ui/results.json), [freeze JSON / SHA256](../reports/stage2-ui/freeze.json), [восстановленный dying герой](../reports/stage2-ui/dying-restored.png), [локальное ограничение Alarm](../reports/stage2-ui/unsupported-spell.png). Оба итоговых снимка просмотрены; `git diff --check` без замечаний.

## Владельцу старой приёмки

`tests/test_preview7_acceptance.py::test_zero_hp_stops_commands_and_keeps_checkpoint` — старый общий Preview7 acceptance, не собственный UI-тест этого чата. Он **не удалён и не изменён**. Его ожидание `scenario_stopped_zero_hp`/запрета end_turn устарело по новому engine-контракту; root/владельцу необходимо обновить на death-save/end_combat путь. Эта зависимость явно передана вместо изменения чужого файла.

Сборке/root: оба production-файла **frozen**, точные hashes в freeze.json. Общий integration-документ не редактировался. Собственные новые файлы: `tests/test_stage2_ui.py`, `tests/test_stage2_ui.cjs`, этот отчёт и evidence. Owner-copy/следующий gate — у координатора, сборка не выполнялась.

Очистка: собственных Python/Node процессов стенда после завершения **0**. Исходники/тесты/trace/freeze и снимки сохранены. По указанию не повторять policy-отказы новая очистка не запускалась и старые запреты не обходились. Остались собственные synthetic profiles `reports/stage2-ui/{tmp-smoke,tmp-smoke-2,tmp-smoke-final,tmp-smoke-pass}` и ранний `failure.png` для согласованной очистки. Пользовательские профили/project DB/WAL, чужие стенды и immutable artifacts не трогались.
