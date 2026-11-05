# Следующий узкий этап: Bless, ресурс и концентрация

Read-only обзор 18.10.2026, пока релизное дерево занято Windows Preview.
Эта работа создаёт только данный документ. Production-код, зависимости,
контракты, определения кампаний и существующие отчёты не менялись. Новые тесты
не запускались; выводы ниже — результаты чтения кода, upstream-тестов и локальных
данных, а не новая сертификация исполнения. Реализация ждёт отдельного задания
после release gate.

## Решение для следующего пакета

Кандидат: **Bless первого уровня на самого заклинателя**, обычный заранее
проверенный Cleric 1, один зарегистрированный противник. Одна поддержанная цель,
ячейка первого уровня, обычное action; без upcast, ритуалов, AoE, нескольких целей,
реактивных заклинаний и произвольной подготовки списка. Существующие воины
не получают магию. Это отдельное новое определение, не изменение двух проверенных
fighter-профилей.

Почему этот кандидат: в Nat20 есть полная цепочка intent → activity resolver →
эффект → модификаторы броска → проверка/сброс/истечение концентрации, а Bless
есть в локальном источнике 2024. Но **именно локальный импорт 5.3.3 и Solo-проекция
этой цепочки ещё не проверены и сейчас имеют блокеры**.

Bless — эффект, а не самостоятельное состояние из перечня D&D conditions.
Нельзя придумывать condition `blessed` или `concentrating` ради существующего DTO.
Для первого пакета достаточно корректного поведения эффекта/концентрации;
Poisoned/Unconscious проверяются отдельными сценариями, без подключения каталога
заклинаний, накладывающих эти состояния.

## Доказательства в закреплённом upstream

База: `vendor/nat20/UPSTREAM.json`, Nat20 0.7.0,
commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`.
Далее пути относительно `vendor/nat20/packages/dnd5e-engine/src/dnd5e_engine/`.

| Готовый узел | Что найдено чтением | Что ещё доказать в Solo |
| --- | --- | --- |
| `build_spec.py`, `build_party.py` | `derive_sheet`, `CharacterBuildSpec`, `CombatInstance.spells_known`, `build_party_member`; ячейки из листа и классовая характеристика | Право Cleric 1 подготовить Bless, обычный билд, снаряжение и компоненты; не выдавать spell list воину ручной подстановкой |
| `orchestrator.py:267`, `:11351` | Публичные `PlayerIntent(intent_type='cast_spell', spell_id='bless', target_id='hero', slot_level=1)` и `submit_player_intent`; `_resolve_intent_activities` получает Spell из loader | Closed command + зарегистрированный spell ID, не произвольный activity/эффект/формула из AI |
| `activities/resolver.py`, `activities/effects.py` | Utility с effect riders вызывает `apply_activity_effects`; перевод `PassiveEffect` → runtime `ActiveEffect` | Эффект реально содержит и применяет бонус; пустая utility/activity не является успехом |
| `orchestrator.py:11213` | `_consume_spell_slot`, `_pay_for_cast`, `_take_spell_slot`: единственный расход ячейки внутри Nat20, `cast_failed/no_slot` при отказе | Расход 2→1, затем 1→0; атомарный отказ без расхода канона при нехватке ресурса; отсутствие повторного списания при replay |
| `orchestrator.py:5649–5895` | Sidecar преобразует Foundry attack/save bonus keys в применяемые модификаторы | Дифференциальный seeded-тест настоящих атаки и спасброска с/без Bless; не удваивать бонусы нескольких типов атак |
| `orchestrator.py:3927`, `:5204`, `:6545`, `:6777`, `:7036` | Drop cascade, CON check после урона, ownership links, лимит длительности, expiry на собственном turn_end заклинателя | Успех/провал проверки, замена концентрации повторным Bless, добровольный сброс и истечение; не писать вторую формулу CON/DC/длительности |
| `views.py:85–107`, публичные `get_live`, `get_actor_active_effects`, `drain_pending_events` | Ячейки, `concentration_chain`, runtime effects, события `spell_cast/effect_applied/concentration_check/concentration_dropped/effect_expired` | Узкая ACL-проекция и память, без доступа к testing registry или приватному `_get_live` |
| `rest.py` | `resolve_short_rest` не восстанавливает обычные Spellcasting slots; `resolve_long_rest` восстанавливает максимум | Реальные непустые ячейки после JSON round-trip; не подменять Spellcasting ресурсом Second Wind |

Частные функции в таблице — места аудита, не точки вызова хостом.
Исполнять нужно публичный API, не копировать его математику.

Upstream-тесты находятся в локальном audit checkout
`outputs/ReuseFirstAudit-2026-10-17/repos/nat20/packages/dnd5e-engine/tests/`:

- `e2e/test_c13_concentration.py`: повторная концентрация, урон/CON save,
  обнуление HP; часть fixtures имеет усиленные HP/атаки и private registry.
- `test_concentration_timed_expiry.py`, `test_concentration_lifecycle_drops.py`,
  `test_concentration_check_event.py`, `test_concentration_drop_effect_name.py`:
  длительность, события и идентичность эффектов.
- `test_duration_tick_at_turn_end.py`, `test_effect_timed_expiry.py`:
  обычные timed effects не равны concentration countdown.

Эти файлы прочитаны как свидетельства upstream-поведения; их наличие не означает,
что они были запущены здесь или что текущий Solo уже поддерживает заклинания.

## Проверенный локальный источник и разрыв перевода

Источник — LevelDB `integration/nat20/sources/foundry-5.3.3/dnd5e/packs/`:

- `spells24`, документ `phbsplBless00000`, UUID
  `Compendium.dnd5e.spells24.Item.phbsplBless00000`;
- `classes24`, документ `phbclcCleric0000`; grant первого уровня ссылается на
  `phbclcSpellcasti` и `phbclcDivineOrde`; выбор Divine Order требует проверки
  зависимостей, а не произвольного пропуска;
- оба документа: `system.source.rules=2024`, `license=CC-BY-4.0`.
  Bless и Cleric пока metadata-only в `search-catalog.json`, не в минимальном
  исполняемом `imported/manifest.json`. Laaru 2014/mixed здесь не нужен.

**Блокер импорта:** у локального Bless effect `1dzvDffEh8wZ0oQy` называется
`Blessed`, `statuses=[]`, duration — 60 seconds. Механические изменения находятся
в `effects[].system.changes[]` с `type='add'`: save и четыре attack bonus keys,
каждое со значением `1d4`. Закреплённый
`dnd5e-srd-data/tools/translators/foundry.py:_passive_effects` (около строки 802)
читает только `entry.get('changes')` и числовой `mode`. При прямом переводе такого
документа изменения будут потеряны, хотя activity и имя эффекта сохранятся.

Будущий минимальный мост: отдельная проверенная нормализация **данных** Foundry
5.3.3 `system.changes/type` в уже поддержанный формат, с явным соответствием
`add` → существующему Foundry mode. Оригинал сохраняется неизменным с SHA-256;
производная проекция имеет собственный хеш и ссылку на нормализатор. Не править
vendored Nat20 и не вычислять `1d4` в импортере. Gate должен отвергать пустые
changes, неизвестные ключи/режимы и отсутствующий effect rider до активации.

**Блокер компонентов:** у Bless текст требует Holy Symbol worth 5+ GP, но
структурное `materials.cost` локального документа равно 0. Нельзя считать cost=0
доказательством бесплатного компонента. Nat20 `_emit_spell_cast` (около 11272)
явно описывает components как metadata, **never enforced**. Нужны проверенный
предмет/профиль Holy Symbol, владение/доступность, возможность verbal/somatic
компонентов и свободная рука по поддержанному снаряжению. Без этой привязки
заклинание не готово к production; нельзя просто игнорировать компоненты или
дать модели подтвердить их наличие. Конкретный билд/экипировку сейчас не активировать.

## Пробелы текущего Solo adapter

1. `game_commands.py` запрещает `cast_spell` и `drop_concentration`;
   `nat20_worker.apply_action` принимает только attack/move/use_feature/end_turn/
   end_combat. Существующее ограничение правильно и остаётся до проверки.
2. `nat20_content.release` создаёт loader без Spell и Cleric. `CampaignDefinition`
   жёстко ограничен fighter 1; build/armor/skills не должны наследоваться жрецом.
   `initialize` также безусловно создаёт счётчик Second Wind. Новый класс требует
   отдельных проверенных данных и точного списка class capabilities.
3. `sync_hero` уже сохраняет ячейки, concentration_effect_id и активные эффекты,
   а боевой replay сравнивает целый view/effects/events после каждой команды.
   Это подходящий существующий persistence-механизм. Но view не отдаёт отдельный
   `concentration_rounds_remaining`: его восстанавливает полное повторение ходов.
   Нельзя выдавать исходные duration.seconds за оставшееся время; нужен тест
   restart непосредственно перед expiry с сопоставлением дальнейших событий.
4. `Nat20GameEngine.snapshot` не проецирует spell slots, ownership/длительность
   эффектов и концентрацию. Поэтому даже расход ячейки сейчас не станет
   типизированным resource-фактом памяти. Для slots подходит существующая
   resource-проекция; для Bless нужен согласованный additive typed effect
   lifecycle в существующих GameSnapshot/GameEvent, без второго журнала/памяти
   и без искусственных D&D conditions. Контрактное изменение — отдельный gate.
5. Урон/Poisoned/Unconscious и модификаторы concentration save проверять по
   событиям Nat20; в память передавать только реально экспонированные поля.
   Существующая Solo-проекция attack roll сохраняет только удержанный d20;
   нельзя выдумывать потерянные кости бонуса/преимущества для Narrator.
6. Граница боя/времени пока не закрыта: `end_combat` upstream возвращает
   `final_active_effects`, текущий adapter переносит эффекты героя; комментарий
   «effects end with combat» не доказывает их очистку. Обычные проверки отвергают
   активные эффекты, отдых не привязан к clock/expiry концентрации. Нельзя сбрасывать
   Bless по факту окончания боя или возрождать полную минуту в следующем.
   Понадобится проверенный путь добровольного снятия/естественного истечения и
   post-combat времени. Если он не готов, активация сценария остаётся заблокирована,
   а не завершается фиктивной очисткой состояния.

## Точные будущие файлы, после release gate

| Файл | Минимальное назначение |
| --- | --- |
| `tools/import_nat20_minimal.py` | Явный отбор Bless/Cleric/необходимых зависимостей и снаряжения; проверяемая нормализация effects 5.3.3; без fallback на весь corpus |
| `integration/nat20/imported/raw/_source/…`, `canonical/…`, `manifest.json` | Новые исходные/типизированные документы с редакцией, лицензией, хешами; фактические подкаталоги по существующим полям manifest |
| `solo/nat20_content.py` | Добавить typed Spell к ограниченному loader и provenance gates; оставить release fingerprint/migration защиту |
| `solo/nat20_campaign.py` | Одно новое разрешённое определение Cleric с проверенными подготовленным Bless и компонентами; не произвольный build/spell list |
| `integration/nat20/playable/cleric-bless-2024-v1.json` (новый), `registry.json`, `capabilities.json`, `campaign.schema.json` | Отдельный pending профиль, dependencies и readiness после тестов; прежние fighter JSON не менять |
| `solo/game_commands.py` | Закрытый CastSpell только Bless/self/base slot и DropConcentration; без dice/DC/effect/state patch от LLM |
| `solo/nat20_worker.py` | Публичные intents, gates допустимого профиля/компонентов, class-specific init, replay тех же команд; исход/ресурс/эффекты только Nat20 |
| `solo/nat20_api.py` | Подтверждённые spell-slot ресурсы и узкая effect/concentration-проекция с ACL; clock/граница боя только после доказанного поведения |
| `solo/engine_memory_contracts.py`, `solo/engine_memory.py`, `solo/nat20_contracts.py`, `integration/nat20/schemas/*.json` | Согласовать минимальную типизацию новых команд/эффектов в существующей системе и backward compatibility; не создавать параллельные DTO/журнал |
| `solo/command_engine.py`, `solo/playable_ui.py` | Явный отказ новых магических команд в прежнем engine, общий маршрут и серверные labels; без UI-арифметики/нового экрана |
| `tests/test_nat20_spell_scope.py` (новый), необходимые строгие allowlist-ожидания в `tests/test_command_cycle.py` | Изолированные сценарии ниже; не весь тестовый набор |
| `docs/PLAYABLE_RULES_SCOPE.md` | Только фактически пройденные возможности и оставшиеся ограничения |

Не нужны сейчас изменения UI, PDF pipeline, cloud-бухгалтерии, dependencies,
Nat20 vendor, Windows build или новая память. Изменение release fingerprint
потребует явной политики совместимости: старые native-кампании сейчас отвергают
иной fingerprint. Нельзя разом переимпортировать manifest и незаметно сломать
восстановление существующего бойца; выбор pinned content release/явная миграция
должны пройти отдельную проверку до активации нового профиля.

## Целевые acceptance tests будущей реализации

1. **Источник → исполнение:** проверить raw/projection хеши, 2024/CC-BY, связи
   activity→effect, все ожидаемые bonus changes. Повреждённый/пустой effect,
   неизвестный mode, чужая редакция — отказ до worker/канона.
2. **Обычный жрец:** лист и HP/ячейки из Nat20, проверенные законные choices/
   снаряжение/компоненты. Cast Bless/self расходует ровно одну ячейку и action,
   создаёт реальный эффект и одну concentration chain; не только `spell_cast`.
3. **Фактическая математика:** seeded A/B атака и saving throw с/без Bless;
   доказать применённый бонус, отсутствие дублирования и его исчезновение после
   снятия. Poisoned отменяет/меняет преимущества по Native, а не по client logic.
4. **Концентрация:** зарегистрированный урон → Native CON save, success/failure;
   повторный Bless заменяет предыдущий без stacking; drop не тратит action/slot;
   expiry наступает на нужной границе хода. Incapacitated/0 HP удаляет связь на
   изолированном механическом стенде, без обещания полного death/revival gameplay.
5. **Restart/replay:** save с активным Bless и израсходованной ячейкой → новый
   Python процесс → тот же view/эффекты/ресурсы → тот же следующий save/expiry
   и события. Checkpoint/undo ветвят ресурс, эффект и память согласованно.
6. **Атомарность:** повтор request ID не списывает ячейку/не бросает снова;
   stale version, отказ target/слота/компонентов, race и ошибка memory ingest
   оставляют состояние/события/эффекты неизменными. В upstream отказ может
   продвинуть временный turn: failed worker целиком отбрасывается как сейчас.
7. **Ресурс/граница времени:** short rest не восстанавливает обычную ячейку,
   long rest восстанавливает; Bless не остаётся бесконечно и не получает новую
   минуту после end combat/new combat. Проверить свободный ввод `/command-turn`,
   Narrator grounding, скрытые ресурсы NPC и отсутствие actor/effect leaks.
8. **Совместимость:** прежний fighter API/память/replay и старый Solo продолжают
   работать; fighter/неподготовленный caster не могут кастовать; catalogue-only
   spells, upcast/ритуал/чужая цель и AI-поля формул/Сл/характеристик отклоняются.
   Проверить восстановление старого fingerprint отдельно от нового профиля.

Статус: **кандидат выбран; activation blocked** до release gate, подтверждённого
импорта изменений эффекта, законного caster/components профиля, resource/effect
проекции, границы времени и целевого end-to-end прогона. Никаких защит сейчас
не снято, новая магия не активирована.
