# План upstream-исправления одного Bless-пакета

Read-only анализ, 18.10.2026. Во время заморозки под UI-only Preview8 изменён
**только этот документ**. Production, DTO, тесты, данные, зависимости и vendor
не изменялись; тесты/сборка не запускались. Bless остаётся pending. Ниже — план
для отдельного задания, не отчёт о выполненных исправлениях.

База: Nat20 0.7.0, commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`,
`vendor/nat20/UPSTREAM.json`. Три дефекта уже воспроизведены в
`tests/test_nat20_bless.py`, результаты описаны в `NAT20_BLESS_IMPLEMENTATION.md`.
Повторять текущие gap-тесты ради этого документа не требовалось.

Проверить публичную историю [первоисточника Nat20](https://github.com/tapestria/nat20/commits/main/)
не удалось: инструмент чтения вернул `restricted URL`. Готовый публичный fix
**не подтверждён**; это не утверждение, что его нет. До собственного патча стоит
повторить проверку конкретного upstream commit/PR и его регрессий, без обновления
рабочего окружения. Локальная закреплённая копия — источник анализа ниже.

## 1. Концентрация: использовать существующий effect-aware save

Файлы ниже относительно
`vendor/nat20/packages/dnd5e-engine/src/dnd5e_engine/`.

Проблема: `orchestrator.py:_emit_apply_damage` (около 5115, save-блок около 5180)
напрямую вызывает `save_modifier` и `roll_d20_test`. Это обходит Bless/Bane
sidecars. Не добавлять d4 вручную и не перекидывать save в Solo.

Минимальная upstream-правка:

1. В том же месте damage lifecycle собрать контекст для повреждённого
   концентрирующегося combatant через существующие
   `_project_target_modifiers` → `activities/build_context.py:build_activity_context`.
   При необходимости выделить короткий upstream helper построения save context,
   который только соединяет эти функции. Не переносить весь activity walker
   и не писать альтернативную проекцию modifiers.
2. Передать актуальные effect/condition save sidecars, penalty и **тот же**
   `live.rng` в `activities/save_primitive.py:roll_save(..., ability='con', dc=...)`.
   Существующую формулу/ограничение concentration DC оставить в её нынешнем узле.
3. Создать прежний `ConcentrationCheck` из возвращённого `SaveRoll`:
   total/succeeded/natural/modifier/mode/sources. Не эмитить дополнительный
   `SaveRolled`: одна концентрационная проверка должна остаться одним save-событием.
   Сохранить существующую последовательность damage → check → drop/death.
4. `roll_save` уже умеет Legendary Resistance. Удалить прежнее второе решение
   `_consume_armed_legendary_resistance` из этого пути либо делегировать ему
   единожды через существующее согласование. Использовать
   `_sync_legendary_resistance`/`_emit_legendary_resistance_used` по текущей модели;
   нельзя списать ресурс и в context, и повторно в live state.

`roll_save` уже бросает d20 **до** bonus dice и учитывает save bonus, условия,
penalty, advantage/disadvantage и преобразование результата. Это переиспользование
математического обработчика, а не ещё одна реализация saving throw.

Будущие целевые тесты:

- В `tests/test_nat20_bless.py:test_gap_native_concentration_save_omits_bless_bonus`
  сохранить старый воспроизводитель под старым runtime; для исправленного runtime
  сделать отдельный позитивный тест: реальный Blessed даёт ровно один d4 на
  CON save, влияет на success и корректно снимается при failure.
- Upstream: `tests/test_concentration_save_modifier.py`,
  `test_concentration_check_event.py`, `e2e/test_c13_concentration.py`;
  добавить Bless/Bane, без эффекта, exhaustion, сохранение порядка RNG,
  ровно один `ConcentrationCheck`, отсутствие дублирующего `SaveRolled`.
- Адресный тест Legendary Resistance: один расход при конверсии провала,
  ноль расходов на успехе. Не включать новый каталог монстров в Solo.
- Без bonus effects старые natural/total/events должны совпасть; с Bless поток
  после save закономерно меняется из-за нового d4 — не подгонять его под старый.

## 2. Повторное наложение: один экземпляр на точную identity

Причина: `orchestrator.py:_emit_apply_effect_applied` (около 5376) делает append,
а `_emit_apply_effect_expired` (около 5471) удаляет первый совпавший экземпляр.
`_record_effect_lifecycle_links` намеренно считает одинаковую
`(target_id, id, origin)` refresh: chain одна, effects становятся двумя.

Переиспользовать `rules/effects.py:dedupe_by_identity`: он уже задаёт нужный
трёхкомпонентный ключ и сохраняет **первый** экземпляр. Поэтому слепое
`dedupe(old + new)` неверно — сохранит старый effect.

Минимальная upstream-правка — upsert в canonical effect store при EffectApplied:
новый effect заменяет старую точную identity, остальные эффекты сохраняют
свой порядок. Собирать вход `dedupe_by_identity` так, чтобы новый экземпляр
занимал прежнюю позицию этой identity и выигрывал у её дублей. Не переставлять
чужие эффекты в начало: порядок bonus dice важен для RNG.

Сохранить нынешнюю refresh-семантику lifecycle: одна chain entry, новый срок
через существующий `_record_effect_lifecycle_links`, обычный расход ячейки за
каждый настоящий cast. Не дедуплицировать события cast/расход ресурсов и не
рисовать фиктивный `ConcentrationDropped`, которого engine не произвёл.
Требование «снять старую концентрацию в момент начала иного cast» отдельно
остаётся в прежнем concentration cascade; не переписывать его в этом пакете.

При гарантии уникальности store существующий expiry fold удалит эффект целиком.
Входные дубли `_seed_active_effects` в новом выпуске лучше явно отвергать,
сверяя identity через тот же helper, а не молча ремонтировать старое сохранение.
Для обновлений с изменёнными statuses обязательна проверка condition ownership
через существующие apply/expire folds; патч Bless со `statuses=[]` сам по себе
не доказывает универсальный upsert любых эффектов.

Будущие тесты:

- Превратить для нового runtime
  `test_gap_same_identity_recast_retains_a_duplicate_after_drop` в positive:
  cast → следующий ход → recast → одна effect identity → один d4 в атаке →
  drop → ноль эффектов/модификаторов/chain links. Две ячейки расходуются, не одна.
- Проверить refresh срока на старой границе expiry и после перезапуска.
- `tests/test_rules_effects_and_gambits.py` уже содержит first-wins/target/origin
  тесты helper; дополнить live-path тестами в `test_get_actor_active_effects.py`
  и `test_concentration_drop_effect_name.py`.
- Одинаковые id у разных target или origin не сливать. Удаление одной identity
  не должно стирать чужое владение condition. Это проверка ownership, не обещание
  полного переноса всех правил сочетания заклинаний.

## 3. Standalone save: общий словарь ключей, прежний evaluator

Проблема: `check.py:resolve_check` вызывает
`rules/effects.py:apply_changes_to_check`/`filter_changes_by_bucket`, которые
сопоставляют `save.bonus`, тогда как Blessed содержит
`system.bonuses.abilities.save`. Боевой `_fold_active_effect_changes` уже знает
этот alias, но standalone не использует его.

Минимальная upstream-правка:

- Выделить чистую нормализацию **одного нужного alias** в общем rules-слое:
  `system.bonuses.abilities.save` → `save.bonus`; остальные ключи сохраняются.
- Использовать её в `filter_changes_by_bucket` и в существующей ветке
  `orchestrator.py:_fold_active_effect_changes`, чтобы словарь не разошёлся снова.
- Сохранить `apply_changes_to_check`, modes, priority и `rng`. Не менять исходный
  ActiveEffect и не дописывать рядом второй искусственный `save.bonus`.
- Не обобщать эту правку на четыре attack siblings: их однократное свёртывание
  в боевом fold уже работает; наивный общий alias даст четыре d4.

Тесты: для нового runtime positive вместо
`test_gap_standalone_save_does_not_alias_foundry_bless_keys`, а также upstream
`tests/test_apply_changes_to_check.py` и `test_rules_effects_and_gambits.py`:
настоящий runtime Blessed → ровно один d4 в saving_throw, ноль в ability/skill,
старый canonical `save.bonus` работает, сохраняются priority и d20-before-bonus.
Seeded A/B сравнивает natural/total и effect breakdown. Не требовать одинаковой
семантики поля modifier у двух API: CheckResult включает effect dice в modifier,
SaveRoll/ConcentrationCheck оставляют modifier плоским.

Порядок небольших патчей: общий alias → invariant effect identity → подключение
concentration save к существующему primitive. Для каждого — отдельные целевые
регрессии; без широкого рефакторинга остальных saving-throw путей.

## Pinned hash и replay: нельзя просто заменить установленный пакет

Текущий `release(selector)` изолирует **контент**, но проверяет один общий
установленный runtime 0.7.0 и его файлы. После любого upstream-патча изменятся
code hashes, release fingerprint и часть events/RNG. Старый fighter manifest
сам по себе не обеспечит совместимость. Подменять UPSTREAM.json так, будто это
тот же commit, или отключать проверку запрещено.

После отдельного разрешения нужен новый проверенный upstream commit/release
и отдельная связка runtime+content с новым fingerprint. Для старых digest должен
остаться доступен старый worker с неизменным 0.7.0; для нового — исправленный.
Минимальный хостовый шов: доверенная таблица digest → проверенный worker/runtime
asset и corpus, с теми же JSON-командами и журналом. Выбор не приходит из AI,
кампании не передают пути/модули. Не загружать две версии одного Python-пакета
в один уже импортированный процесс. Способ изоляции/упаковки Windows workers
нужно отдельно доказать; это ещё не готовая возможность Preview8.

Старые active combats всегда replay на прежнем runtime: даже корректное
исправление концентрации может изменить последующие броски, смерти и награды.
Не пересчитывать прошлый канон новым RNG. Для pending Bless создать новую
версию профиля/выпуска, а не переписать старые hash/recipe.

Acceptance до смены pin: fixture `nat20-preview7-fighter-combat.json`, idempotent
старый ответ, старые memory signatures, новый процесс/продолжение, неизвестный
digest fail-closed; отдельно новый Bless со всеми позитивными тестами и zero
mandatory gaps. Явную миграцию, если понадобится, проектировать отдельным шагом
на проверенной границе, не внутри активного боя.

## Самый короткий путь к законному фиксированному Cleric

Не универсальный конструктор, а один полный choice ledger с проверенными
обязательными пассивами и отдельным списком исполняемых действий.

1. Сохранить уже импортированного human/Cleric 1 и Mace+Amulet, без щита:
   меньше новых зависимостей, чем смена вида. Завершить Divine Order выбором
   Protector через существующие FeatureChoice/GrantRef и
   `build_spec._choice_options/_split_picks`, а не добавлением слова в features.
   В `dnd5e-srd-data/tools/regen.py:main` уже есть `_inject_grants` с обработкой
   ItemChoice; переиспользовать его подход только для выбранного поднабора,
   не запускать полную регенерацию corpus.
2. Кандидат background — Criminal `phbbgCriminal000`: есть в локальном
   origins24, 2024/CC-BY, даёт Alert `phbftAlert000000`. Для Human Versatile —
   Skilled `phbftSkilled0000` из feats24, тоже CC-BY: это не требует ещё одной
   магической подсистемы Magic Initiate. Проверить Human Skillful и три Skilled
   proficiency picks отдельно от двух class skills и background grants;
   определить языки и инструмент. Список статов/выборов задаётся данными, не AI.
3. Сохранить исходный standard array и явно записать допустимые background ASI,
   например DEX+2/CON+1. У локального Criminal hint называет CHA, а locked data
   разрешает INT; выбранные DEX/CON совпадают в обеих частях, но расхождение
   надо пометить provenance/review и не выдавать его за решённый общий importer.
   Финальные HP/AC/initiative/proficiencies выводит Native derive_sheet.
4. Источник `spellcasting-cleric.json` прямо задаёт три cantrips и четыре
   prepared spells. Можно зафиксировать его рекомендованные выборы:
   Guidance/Sacred Flame/Thaumaturgy и Bless/Cure Wounds/Guiding Bolt/Shield of Faith.
   Все семь ID требуют проверки class list/источника. Полный выбранный список —
   данные законного листа; исполняемая capability пока **только Bless**. Остальные
   действия явно отвергаются, их описание не становится разрешением на cast.
5. Закрыть dependency graph: `derive_sheet._always_on_changes` сейчас умеет
   залогировать и пропустить отсутствующий feature — это не доказательство
   законного листа. Mandatory grants/пассивы должны разрешаться и применяться.
   В частности, не считать Resourceful/его выдачу ресурса после отдыха готовыми
   без проверки. Неподдержанные активные способности можно явно пометить unbound;
   обязательные пассивные изменения и выдачу ресурсов молча пропускать нельзя.

Такой профиль остаётся кандидатом до target tests всех выбранных passives,
компонентов, ASI/slots/skills и отдыха. Наличие полных метаданных не является
доказательством полного исполняемого Cleric.

## Самый короткий путь через границу боя и время вне боя

Нынешнего совета «drop перед победой» недостаточно: неожиданный последний hit
может закончить бой с Bless. `submit_player_intent` отвергает ended combat,
а host apply_action сейчас допускает там только end_combat. Это реальный тупик,
не основание автоматически снимать эффект или отменять победу.

**Минимальный ограниченный выпуск:** новый публичный upstream-вызов добровольного
снятия концентрации, допустимый и на читаемом ended handle, с проверкой caster и
делегированием существующему `_drop_concentration`. Хост использует прежний
закрытый DropConcentration, не обращается к private registry. Игрок выбирает
снятие явно; после него старый end_combat/rest путь работает без активного таймера.
Это не поддержка переноса Bless во внешний мир. Отдельно проверить выдачу events
после queue sentinel ended боя: события нельзя потерять из-за завершившегося
`narration_events`/drain; новый public result должен вернуть их надёжно.

**Если нужен настоящий перенос времени:** добавить в upstream публичный экспорт
оставшегося concentration clock и явный time-advance reducer, переиспользующий
существующие `_hook_concentration_expiry`, `_drop_concentration` и expiry cascade.
`EndCombatResult.final_active_effects` уже экспортируется, но одного списка
недостаточно: в Blessed duration пустая, а `concentration_rounds_remaining`
хранится отдельно. Экспортировать identity, остаток и фазу отсчёта, не подменять
их исходной минутой. При seed следующего боя clock должен восстановиться,
а не стартовать заново.

Самая узкая первая операция времени — явное ожидание минуты либо утверждённый
short/long rest, без симуляции путешествий. Авторизованную длительность передать
Native reducer; истечение должно вернуть настоящие typed events. Существующий
Solo elapsed time/journal остаётся единственным каноном, Native отвечает за
семантику effect clock. Не писать в Solo вторую формулу expiry и не списывать
те же turn_end/секунды повторно при replay.

Тесты нового временного шва: победа с активным Bless → save/new process → явный
drop или time advance → end/rest; boundary за шаг до expiry; следующий бой без
возобновления полной минуты; повтор команды без второго advance; undo/restore
часов и effect facts; failure rollback; slots не возвращаются от drop/expiry,
обычные ячейки возвращает только прежний Native long-rest resolver.

До этих доказательств pending не снимается. Документ завершён; реализация,
публикация upstream patch, обновление pin и сборка требуют отдельных заданий.
