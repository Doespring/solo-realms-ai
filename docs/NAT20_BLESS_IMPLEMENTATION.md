# Nat20 Cleric 1 / Bless — изолированный pending-пакет

18.10.2026. **Production activation: pending / запрещена.** Сделана обвязка одного
заклинания и доказаны обязательные пробелы закреплённого upstream. Полный законный
игровой жрец и завершённый магический сценарий не заявляются. Preview7 ZIP,
vendor, зависимости, `web/*`, cloud и PDF pipeline этой работой не менялись;
сборка и платные вызовы не запускались.

## Совместимость решена без миграции

`solo.nat20_content.release()` по-прежнему загружает прежние 12 документов и
возвращает fingerprint
`374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75`.
Ни старый `imported/manifest.json`, ни два fighter JSON не переписаны. Worker
выбирает выпуск по сохранённому `release_digest`; расширение выбирается только
новым профилем. Неизвестный digest или несовпадение класса/выпуска — отказ,
не автоматическое обновление. Новый fingerprint зависит от прежнего fingerprint
и отдельного manifest расширения.

До изменений снят реальный бой старым адаптером: start → move → attack.
`tests/fixtures/nat20-preview7-fighter-combat.json` хранит этот read-model,
recipe и исходный fingerprint с привязкой к
`reports/preview7/build-source.json` (`source_sha256=93df0971cadf7dce5da80d19ca25af422f6575e11c8eb2af33bc6962748cf01e`).
Новый код восстанавливает его без изменения runtime и продолжает ход через
существующий API. Сериализация старых GameSnapshot и fighter definition сохраняет
прежнюю форму: отсутствующие новые поля не вставляются как `null`/пустые списки,
поэтому подпись памяти не меняется из-за добавления DTO.

Исходный принятый ZIP имеет сообщённый release gate SHA-256
`8f1bde4793318e85a1732ed3a197481be05d930d00796b0182157d200fd88f7d`.
ZIP не пересчитывался/не пересобирался; проверка совместимости относится к
сохранённому бою, прежнему fingerprint и исходным файлам данных.

## Что реально реализовано

Отдельный `integration/nat20/releases/bless-self-2024-v1/` содержит семь новых
типизированных документов: Bless, Cleric, Spellcasting/Divine Order/Protector,
Mace и Amulet. Источник — локальный Foundry dnd5e 5.3.3, 2024, CC-BY-4.0;
каждая запись имеет pack/document ID и отдельные SHA-256 оригинала, проекции
и канонических данных. Старый каталог и исполняемый fighter-корпус не расширены
этими записями автоматически. Laaru и контент иной редакции не использовались.

`tools/import_nat20_bless.py` читает локальные LevelDB с прежним reader и вызывает
закреплённый переводчик Nat20. `solo.nat20_bless.normalize_bless` исправляет только
проверенную форму данных 5.3.3: пять `effect.system.changes` типа `add` становятся
обычными changes с Foundry mode 2. Исходник сохранён отдельно. Разрешена только
точная группа ключей attack/save со значением `1d4`; пустые/повторные/чужие ключи,
режимы/формулы, редакция и потерянные ссылки activity→effect отвергаются.
Кости и бонусы считает Nat20, JS/PDF/AI-код не исполняется.

Новый профиль `cleric-bless-2024-v1` существует только в отдельном pending registry.
Он недоступен обычному запуску и обычному `SOLO_TEST`: требуются одновременно
`SOLO_TEST=1` и отдельный `SOLO_NAT20_BLESS_STAND=1`. Даже формальная правка статуса
registry не снимает этот gate. HTTP создания кампании продолжает создавать прежнего
воина; нового выбора в UI нет.

На стенде используются нормальные характеристики стандартного массива, Cleric 1,
HP 10 и две ячейки, рассчитанные `derive_sheet/build_party_member`. Нет счётчика
Second Wind. Mace держится одной рукой, Amulet — другой; щита/другого снаряжения
нет. Источник Amulet подтверждает Holy Symbol и цену 5 GP. Закрытый профиль задаёт
возможность verbal/somatic компонентов и открытую яркую сцену без silence;
другие условия/снаряжение не покрываются. Отсутствие символа/неизвестный loadout
атомарно отвергается. `materials.cost=0` в Bless не используется как разрешение
кастовать бесплатно или без символа; реальный metadata event Nat20 оставлен
неисправленным, не считать его стоимостью оплаченного компонента.

Два аддитивных закрытых GameCommand:

- `cast_spell`: только `spell_id="bless"`, `target="hero"`; slot_level всегда
  1 на доверенной стороне, не поле AI. Native `PlayerIntent(cast_spell)` создаёт
  настоящий effect, расходует ячейку и action. Нет разрешения на upcast, ритуал,
  другую цель/заклинание, произвольные характеристики, компоненты или формулы.
- `drop_concentration`: Native `PlayerIntent(drop_concentration)`, только при
  существующей концентрации, без расхода action/ячейки. Новые команды явно
  отвергаются прежним CommandEngine и fighter-профилями.

Старые GameCommand/GameEvent/GameSnapshot, Store journal/transaction, память и
replay переиспользованы. Подтверждённые slot/resource, effect lifecycle и доступные
поля concentration-check пишутся атомарно с состоянием. Повтор ID не списывает
ячейку, stale version и отказ ресурса не меняют канон, ошибка Memory.put откатывает
всё. Проверен настоящий новый Python-процесс с активным Bless, затем продолжение,
checkpoint/restore и undo. AI по-прежнему предлагает команду; состояние меняет
только доверенный backend.

## Поля для будущего UI

Это серверные поля общих `GameSnapshot`, ответов `/command-turn` и `/commands`
и `EngineMemoryAdapter.context(...).mechanics`. `web/*` не менялся; вывод новых
эффектов через старую GET screen-проекцию ещё не подключён.

| Поле | Значение / доступ |
| --- | --- |
| `actors[hero].resources["spell_slot:1"].current` | Оставшиеся ячейки; own/private ACL |
| `actors[hero].resources.concentration.current` | 0/1 из Native chain; own/private ACL |
| `effects[]` | Только для нового профиля: `id`, `origin`, `target`, `caster`, `name`, `concentration`, `duration_limit`; per-effect ACL |
| `effects[].duration_limit.maximum_rounds/maximum_seconds` | Только действительно выданные Native значения; сейчас у Blessed `null`. Это не оставшееся время. Не показывать выдуманный countdown |
| `GameEvent.payload.effects[type="effect"]` | `target/effect_id/origin/active/concentration`, реальные Native applied/expired; эффект не маскируется D&D condition |
| `GameEvent.payload.effects[type="roll"].payload.kind="save"` | Реально выданные natural/total/success проверки концентрации; недоступные bonus/discarded dice не дорисовывать |

Подтверждено: герой видит собственные ячейки/эффект; packet NPC не получает
частный effect или соответствующие факты. Для старых snapshot поле effects
отсутствует, существующие API сохраняют прежний формат. Никакой клиентской
арифметики или нового журнала не требуется.

## Что доказанно блокирует активацию

1. **CON save после урона теряет Bless d4.** В закреплённом
   `dnd5e_engine/orchestrator.py:_emit_apply_damage` (около 5180–5228) проверка
   концентрации использует `save_modifier + penalty` и `roll_d20_test`, обходя
   effect-aware save primitive. В живом тесте `total == natural + modifier`,
   дополнительного d4 нет. Хост не перекидывает результат и не добавляет бонус сам.
2. **Повтор Bless с той же identity дублирует эффект.** Chain содержит одну
   запись, runtime effects — две. Native drop удаляет только одну, оставляя
   работающий модификатор без concentration chain. Это воспроизведено через
   публичный API. Adapter блокирует recast до расхода ячейки; duplicate effect
   identity также отвергается DTO. Разрешён только путь drop → новый cast.
   Ручной dedup/удаление оставшейся копии не добавлялись.
3. **Standalone save не понимает исходный Foundry save key.**
   `resolve_check(CheckSpec(kind="saving_throw", active_effects=...))` ищет
   `save.bonus`, но настоящий Blessed несёт `system.bonuses.abilities.save`;
   seeded A/B получает одинаковый total. Переименование/второй расчёт в хосте
   не добавлены. Standalone проверки с активным Bless остаются закрыты.
4. **Post-combat elapsed time не привязан.** Боевая длительность Native работает:
   кастовый turn_end + девять дальнейших собственных turn_end снимают минутный
   Bless. Но точный countdown не выдаётся в public view, а API для продвижения
   его вне боя не подключён. End combat с сохранившимся Bless, standalone check
   и отдых с активным эффектом отвергаются. Нельзя молча очистить эффект, сохранить
   его навечно или выдать новую минуту в следующем бою. До конца боя нужно снять
   эффект/дождаться истечения; это ограничение стенда, не полный игровой цикл.
5. **Полная законность создания Cleric пока не сертифицирована.** Импортированы
   зависимости Divine Order, но выбор/применение Protector, полный список
   cantrips/prepared spells, origin/background/талантов не прошли полноценную
   привязку. Профиль содержит механический подготовленный Bless, а не доказанный
   законченный лист жреца. Компонентный gate покрывает только фиксированную
   сцену и loadout, не универсальные V/S/M, silence или занятые руки.

Исправление обязательных upstream-узлов требует отдельного решения о проверенном
новом upstream release/миграции либо явного разрешения на портирование. В этом
пакете vendor менять запрещено; fallback не сочинялся. Старый fighter должен
сохранить свой release и после такого будущего решения.

## Целевые проверки

**67 уникальных тестов прошли:** 58 прежних + 9 нового файла. В число девяти
входят **три gap-reproducer**, которые подтверждают дефекты 1–3 выше, а не
правильность соответствующих правил. Дополнительный адресный повтор компонента/
schema/drop gate не суммируется как новый тест.

- `tests/test_command_cycle.py`, `tests/test_nat20_api.py`,
  `tests/test_engine_memory.py`: 58 passed, прежний fighter/обычный Solo,
  строгие команды, память и существующая вертикаль.
- `tests/test_nat20_bless.py`: 9 passed; pending gate, Preview7 restore/continue,
  raw/projection нормализация и Schema, компоненты/closed command, mock HTTP,
  slot 2→1→0, no_slot rollback, memory failure/idempotence/version/ACL,
  save/new process/replay/undo, drop и minute expiry, A/B атаки.
- Дифференциальная атака: одинаковые natural и modifier; Blessed добавляет
  ровно один d4, а не четыре копии sibling attack keys.
- Short rest/long rest slot semantics проверены штатными Native resolvers;
  законченная игровая сессия жреца с отдыхом после боя не объявляется пройденной.

Актуальные доказательства: `integration/nat20/reports/bless-targeted.xml`,
`bless-components.xml`, `bless-regression.xml`, `bless.json`.
`bless-http.xml` — промежуточный диагностический прогон до финальных исправлений
проверки/защит; не evidence готовности. Полный набор и сборка не запускались.

## Изменённые области

`solo/nat20_bless.py`, `nat20_content.py`, `nat20_campaign.py`, `nat20_worker.py`,
`nat20_api.py`; аддитивные команды в `game_commands.py`, отказы старого backend
в `command_engine.py`, server labels в `playable_ui.py`; DTO/ACL-проекции в
`engine_memory_contracts.py`, `engine_memory.py`; соответствующие JSON Schema,
изолированный импорт/профиль, `tools/import_nat20_bless.py`, новый тест/compat
fixture и одно allowlist-ожидание `tests/test_command_cycle.py`.
Проверенные fighter data/manifest и пользовательские кампании не мигрировались.
