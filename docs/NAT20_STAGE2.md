# Этап 2: Native механики, результат и freeze

18.10.2026, Europe/Samara. Выполнен только этап 2. Этап 3, cloud, Game,
preparation, host memory, app/web и packaging не изменялись. Native 0.7.0,
его pin/исходники и release fingerprints сохранены; нового runtime/fork нет.
Реальные/платные AI-вызовы, full suite, EXE и ZIP не запускались.

## Реализовано

| Пункт | Поведение / доказательство |
| --- | --- |
| Goblin advantage | Удалён адаптерный аварийный отказ; реальный Native Goblin бросает с Advantage против Prone и сохраняет последствия. Не пересчитывается адаптером. |
| Проверки с effects | `nat20_checks.resolve_state_check` передаёт включённые эффекты только героя в CheckSpec; чужие/disabled эффекты не влияют. Native считает бонусные кости/флаги, skill/expertise и результат. |
| Состояния | Public `project_passive_check_modifiers` / `project_passive_save_modifiers` задают помехи/преимущества. Poisoned + преимущество отменяются в Native. Для Frightened используется консервативный public default `fear_source_in_sight=True`; подтверждённого внешнего visibility input нет. |
| Истощение | Public `d20_test_penalty(ActiveCondition(...))` даёт готовый penalty, который без вычисления формулы Solo передаётся в native check/save bucket. Значение 2 и эффектовая кость проверены; положительный exhaustion виден как ресурс. |
| Auto-fail save | Public `roll_save` выполняет автоматический провал STR/DEX при соответствующих состояниях без расхода RNG. `natural_roll=None`; Shared Roll с выдуманным кубиком не создаётся. |
| Monster to-hit | Прежний `nat20_monsters` уже решает это через StatBlockMagnitudes/build_activity_context/resolve_attack. Переиспользован без изменений; формулу max(STR,DEX)+PB в Solo не вводили. Несовместимые профили по-прежнему изолированы. |
| 0 HP | Прежние широкие stop-guards заменены запретом действий недееспособного героя; `end_turn` пропускает его ход штатным `pass` и двигает NPC до следующего решения героя. Native сам вызывает death_saves на TurnStarted. Повторных бросков на том же ходу адаптер не создаёт. |
| Death outcomes | Путь API переживает checkpointer/save/new process/replay, duplicate request, undo и rollback ошибки памяти. Native critical recovery/stabilized/dead проверены отдельно. Смерть допускает закрытие боя с поражением и без награды. |
| Spell capability status | Вычисляется из canonical activities/effect payload + настоящего public CONJURATION_ALLOWLIST. Нет ручного списка из 105 ID. Unsupported cast отказывает до расхода Action/slot и commit. |

Stand-alone проверка с нераспознанным Foundry dotted check/save key получает
локальный отказ для конкретного эффекта; такой ключ не становится молча
успешным числовым бонусом. Универсальный alias translator в pinned Native
отсутствует. Проверки во время активного боя этим этапом не добавлялись.
Механика generic check/save buckets и штатных условий подключена; произвольная
семантика восприятия/зрения/сценических решений остаётся за Мастером.

## Два подтверждённых блокера: guards сохранены

**Hide.** Настоящий public PlayerIntent(hide) проверяет cover/visibility,
выполняет Stealth, ставит Invisible/once-per-turn marker, но оставляет Action
доступным. Это воспроизведено новым тестом на текущем pin. Public API не имеет
операции «оплатить Hide действием и сохранить его ход». Подмена через Dash/Ready/
Pass дала бы постороннюю механику/конец хода. Удаление guard или ручное изменение
Combatant.action_available нарушило бы директиву единственного калькулятора.
Нужен исправленный native payment в `_handle_hide` либо public intent funding
seam; менять pin/runtime не разрешено. Отказ только Hide, Rogue/остальные действия
сохраняются.

**Bless.** Три существующих минимальных Native-repro повторно прошли как
**доказательство дефекта**, а не успешное правило: concentration CON save
не получает положенный 1d4; standalone CheckSpec не читает Foundry save key;
повторное наложение и drop оставляют orphan effect с прежней identity.
`submit_player_intent` не даёт callback для endogenous concentration save,
public removal/rekey эффекта или переопределения native RNG/state отсутствуют.
Alias только для внешней проверки не чинит концентрацию/recast, поэтому
это не достаточное исправление адаптера. Нужен корректный native concentration
save/effect identity handler; fork/private-state patch не делались. Guard только
Bless сохранён, Magic Missile/caster и остальная магия не отключены.
Файлы старой магической ветки/экспериментальных патчей не изменялись.

## Контракт для UI / Interpreter (владельцу стыка)

- Новых команд нет: смерть использует существующий `{kind:'end_turn'}`.
  При 0 HP сервер предлагает его; обычные attack/move/feature/spell отвергает.
- При Native death — только `end_combat`; результат `defeat_tpk`, XP не выдаётся.
- Existing GameSnapshot hero resources: `death_save:successes`,
  `death_save:failures`, `death_save:is_stable` и `dead=1` при смерти.
  Они private/known_by hero; NPC private counters не раскрываются.
  Все значения — Native Combatant.death_saves/dead_ids, не отдельный журнал.
- `Nat20Screen.terminal=None` во время активного dying/dead боя: старый UI
  отключает все кнопки при truthy terminal, что помешало бы `end_turn`/закрытию.
  Resource labels уже человекочитаемые. Вне боя при 0 HP остаётся конкретный
  `native_zero_hp_outside_combat`: восстановление/воскрешение не подключено.
- Native DeathSaveRolled содержит total/outcome/counts, **не natural d20**.
  Полный event хранится в прежнем journal row.native_events и возвращается
  Python API. Shared Roll не подделывается. GUI может показать total/outcome,
  если владелец проекции добавит чтение этого evidence; данный этап не правит web.
- `catalog/catalog_detail(...).execution_status` теперь включает
  `narrative_or_unsupported_in_combat`. Дополнительно
  `capabilities(current).spell_execution` содержит компактный `{spell_slug:status}`
  только выбранных spells, без обхода всего каталога на каждый ход.
  Владелец Game/SceneCompiler должен включить **spell_execution** в свой whitelist
  копируемых capability полей; данный этап не меняет его файл. Catalogue detail
  уже выдаёт этот статус существующему lookup/UI. `native_candidate` — не сертификат.
- Старый `tests/test_preview7_acceptance.py::test_zero_hp_stops_commands_and_keeps_checkpoint`
  намеренно ожидает прежний stop при 0 HP; это устаревшая приёмка. Её владельцу
  нужно заменить ожидание полного stop на подтверждённый death-save путь. Чужой
  тест не удалялся/не переписывался. Новый screen/path test проверен здесь.

## Проверки / покрытие

**14 уникальных целевых тестов passed** по последнему результату каждого, не
один общий прогон. `reports/nat20-stage2/results.json` перечисляет XML и тесты.
Новые 7 тестов в `test_nat20_stage2.py` + 3 существующих Bless gap repro +
старые fighter/atomicity, Goblin bonus/damage, Wizard Magic Missile replay,
предмет Potion of Healing / native charge / Wolf turn.

Начальный `tests.xml` имел 2 harness-ошибки: запрет socket.connect перекрыл
локальный Windows asyncio wakeup socket. Разрешён только loopback; внешняя сеть
по-прежнему запрещена. Эти тесты успешно повторены в `fixes.xml`/`projection.xml`.
Runtime-ошибок этим запуском не выявлено. Full/предыдущие 60 tests не повторялись.

По текущему pin: 339 spells; 121 без исполняемой боевой нагрузки, 218 candidates
по данным. Из candidates Bless отдельно blocked: видимые статусы 121/217/1.
Число не 105, поскольку учитывает utility без meaningful rider и неподдержанные
native summon/enchant/transform. 546 items: 396 с typed activities, 150 reference
без activities. Это инвентарь payloads; индивидуально исполняемость всех items
не заявляется. Potion of Healing поведенчески проверен. JSON coverage сохраняет
статус каждого spell/item и различает reference/candidate от behavioral test.

## Остаточные upstream limits / граница этапа

Timed-effect expiry вне боя, mixed-class Hit Dice, восстановление стабильного
героя через 1d4 часа и воскрешение, full component/material/free-hand accounting,
все narrative spells и разные per-action to-hit одного монстра остаются
отдельными задачами. Hero.death_saves во время боя авторитетен в durable Native
read-model/replay; новые несовместимые поля в PartyMemberSpec не добавлены.
Стабильный герой не получает лишних death rolls — это условие проверяет Native.
Новый host death state machine не вводился. Существующие версии DTO, ACL и SHA
content release не мигрировались. Этап 3 не начат; source copy в Solarimus AI
выполнит координатор после проверки freeze.

Freeze engine-файлов и точные SHA256: `reports/nat20-stage2/freeze.json`.
Очистка: автоматическая проверка отклонила единственный запрос на удаление
собственного стенда с причиной `blocked by policy`. Повторного удаления или
обхода другим способом не было. Остались собственные synthetic profiles
`reports/nat20-stage2/tmp-*` и первичная `probe.sqlite3`; точные существующие
пути перечислены в `results.json`. Все тестовые процессы завершены, серверов
не запускал. XML, coverage и freeze JSON оставлены как результаты. Старые
policy-blocked стенды не трогались. Итоговую ручную копию в Solarimus AI делает
координатор; здесь туда ничего не записывалось.
