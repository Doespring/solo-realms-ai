# Prototype memory — 2026-10-18

Сохраняется прежний event-derived Memory/FTS/ACL и журнал Store. Владение:
`engine_memory.py`, `memory.py`, `world.py`, отдельный `test_prototype_memory.py`.
Общие DTO и их расширения принадлежат чату «Правила»; game.py — чату «ИИ».

Публичные сигнатуры не меняются:

```python
EngineMemoryAdapter(store).project(db, journal_id, raw_event, raw_snapshot)
EngineMemoryAdapter(store).context(campaign, *, npc_id=None, query='', as_of=None,
    branch_id=None, limit=6, budget=6000, current_snapshot=None)
Memory.ingest(db, event_id)
world.location_view(place, *, audience='hero')
```

Context дополнительно возвращает компактный `scene` (публичная локация/объекты/ID NPC),
свой `inventory` и при необходимости `projection_issues`. Отдельному социальному
NPC показывается его собственная каноническая локация, а не скрытый двор героя.
Эффекты не раскрывают невидимых target/caster. Динамические имена/классы/ресурсы
передаются из снимка; известных заранее имён персонажей адаптер не требует.
`npc_profile` возвращает только собственное каноническое known NPC, помеченное
belief/source_ref, а не новую истину мира. Scene для NPC строится по его месту;
исторический as-of не смешивается с переданным будущим механическим снимком.

ACL отдельного подтверждённого изменения пересекается с ACL actor и конкретного
ресурса/HP/позиции/condition/effect. Даже слишком широкий envelope не раскрывает
личный ресурс другого участника. Полные subjects остаются в источнике и БД;
в возвращаемых метаданных скрытые actor IDs фильтруются для аудитории.
Участники боя в тексте проекции также ограничены доступными аудитории; полный
состав остаётся в структурированном source event и memory_subjects, без утраты provenance.

`Memory.resolve_actor` принимает стабильный ID или UUID памяти; старое имя/alias
допустимо только при однозначности. Evidence сохраняет subjects и source_ref.
Существующий world.tick выдаёт evidence со стабильным ID NPC и ID world event.
Проза narrator не читается ни одним из этих путей.

Изменения существующего canonical `economy.items` дают scoped inventory-факты.
Каждый владелец видит только свой before/after, без раскрытия скрытого прежнего
владельца. Количество/владение изменяет InventoryEngine; память не исполняет вещи
и не считает правила. Кошелёк проецируется как личный ресурс. Сохранения/undo
используют прежние before/after и events.undone; ключи проекции привязаны к source event.

Неизвестный native payload остаётся в `events.results[].native_events` без нового
журнала/таблицы. `projection_issues` содержит ссылку на исходное событие/индекс,
`unsupported_projection`, `raw_preserved=true`, required (true/false/null).
Неизвестность required не приравнивается к optional. Пропущенный известный state
effect (например temporary HP, пока отсутствующий в GameSnapshot) помечается required.
Сведения об ошибках проходят ACL envelope; произвольный payload не сериализуется
и не становится фактом. Общий DTO неизвестного типа по-прежнему безопасно
отклоняется до commit, пока владелец контракта не расширил его единственный union.

**Запрос Правилам:** для новых item/effect-проекций и неизвестных optional effects
опубликовать расширения существующего `engine_memory_contracts.py` и requiredness.
В Memory нет второй схемы. `temporary_hp` пока не выдаётся за поддержанное поле;
статус требует видимого решения интегратора вместо молчаливого пропуска.

Реальный широкий интерфейс Правил уже использован без изменений чужих файлов:
`create_experimental_campaign(build=...,encounters=...,prepared_spells=...)` и
`native_action(operation='cast_spell',spell_id='magic-missile',target_ids=...,slot_level=1)`.
Никакой привязки к одному врагу/классу в Memory нет. Native результат закрытого
боя и прежняя стабильная подпись событий продолжают поддерживаться.

Стенд: только `reports/prototype-3`, SOLO_TEST=1 и явный новый профиль до imports.
В этом чате короткий smoke; общий full regression/EXE у интегратора. Пользовательские
data/, профили/ключи и incident evidence не открываются и не изменяются.

## Короткая проверка

```powershell
$env:SOLO_TEST='1'
$env:SOLO_DATA_DIR='<новый изолированный каталог профиля prototype-3>'
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_prototype_memory.py -q -p no:cacheprovider --basetemp '<новый каталог smoke внутри prototype-3>'
```

Результат: **3 passed, 20.86 s**, только две прежние Lark deprecation warnings.
После добавления отрицательной проверки narrator/re-ingest отдельно повторена
только соответствующая фикстура: **1 passed, 2 deselected, 2.73 s**.
Финальное усиление проверки скрытого участника в combat_started/subjects прошло
отдельно: **1 passed, 2 deselected, 2.89 s**. JUnit:
`reports/prototype-3/smoke.xml`, `reports/prototype-3/privacy.xml`.
Full suite/EXE в этом чате не запускались.

1. **Явная DTO-фикстура:** Мэй, Соль, Каменный страж, скрытый четвёртый actor;
   отдельные Эша и Даган в разных местах. Личный focus не раскрывается даже при
   широком event ACL; scoped effects/subjects не раскрывают скрытого actor.
   InventoryEngine передаёт предмет; настоящий существующий world.tick перемещает
   NPC и выдаёт source-ID evidence. NPC читает свою локацию и личные знания.
   Save → новый процесс → контекст, неизвестные optional/required native payloads,
   retry/re-ingest/undo/ветка/checkpoint проверены. Фикстура не заявляется боем Nat20.
2. **Реальный прежний Nat20:** другой герой по имени Мирена, Second Wind,
   native healing_applied, ресурс, replay, повтор без дубля и ветка после undo.
3. **Реальный широкий Nat20:** волшебник Тэя, отдельная локация Архив, два rat
   с разными IDs/позициями. Magic Missile даёт три native damage_applied по двум
   целям и расходует spell slot. Memory получает реальные итоговые damage/resource
   с исходными source refs; чужие counters исключены. Checkpoint → новый процесс
   → реальный restore/следующая команда → возврат checkpoint/retry без дублей.

Не заявлены полнота PHB, поддержка всех native event types, закрытие инцидента,
live AI или новое производство правил. Непроецируемая optional структура сохраняется
в старом журнале и обозначается явно; типы/requiredness общего DTO расширяет Правила.

Очистка: собственных работающих процессов нет; сохранены smoke.xml/privacy.xml. Каталоги
`reports/prototype-3/smoke-01`, `smoke-02`, `smoke-03`, `generic-final`, `privacy-final` переданы Сборке
на последовательное удаление после приёмки smoke (новые синтетические SQLite,
не пользовательские данные). Сейчас общую очистку ведёт Сборка, параллельное
удаление не выполнялось. Старый `.test-playable-scene-tmp` — ранее отмеченный
policy-blocked остаток; запрет не обходился.
