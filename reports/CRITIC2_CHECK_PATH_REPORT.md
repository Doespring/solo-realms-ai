# Отчёт независимого критика №2 — сквозной путь реплики и браузерная достоверность

Дата: 2026-10-25. Ветка: `feature/critical-player-ux`. Код НЕ правился (роль критика).
Важно: файлы в процессе аудита менялись; ниже — состояние на момент проверки, с указанием уже исправленного.

## Как проверялось
- Чтение исходников: `solo/natural.py`, `solo/game.py`, `solo/model_protocol.py`, `solo/game_commands.py`, `solo/command_engine.py`, `solo/nat20_api.py`, `solo/nat20_worker.py`, `solo/nat20_checks.py`, `solo/nat20_contracts.py`, `solo/play_screen.py`, `solo/world_service.py`, `solo/rulesets.py`.
- Прямые пробы модулей венва (`.venv`), SOLO_TEST=1, сети нет, платных вызовов нет.
- Тесты: `tests/test_nat20_command_cycle.py` (7 тестов, реальный Nat20 + CommandMockLLM), `tests/test_core.py`, `tests/test_world.py`, `tests/test_tabletop_b.py`.

## Что подтверждено как РАБОТАЕТ (не дефект)
1. Сохранение и перезапуск результата проверки: бросок сохраняется, повтор запроса возвращает тот же бросок (dice:[17], total:22), narrative_policy='replayed', вызовов модели 0. Проверено на новом Store из той же БД.
2. Невалидный ответ интерпретатора не меняет состояние: `not json` -> clarification, лишнее поле `dc` -> ai_error, неизвестный kind -> clarification; версия 0, history=[], requests=null во всех случаях.
3. Уточнение не засчитывается за проверку: finish_question, строки requests не появляется, history не растёт.
4. available_checks в Nat20 берётся из definition.checks (solo/nat20_api.py:295), а не из place.checks — как заявлено.

## ДЕФЕКТЫ

### D1 (критический, РЕГРЕССИЯ) — solo/natural.py:342-351: пропал fallback обычного шага
Из resolve() удалён хвост results.append({'type':step.kind,'text':step.text,'target':step.target}), который был в HEAD. Теперь при scenario_checks=True speak/move/wait/observe не дают результата вообще.
- Ожидается: обычный тип шага в results.
- Проверено: natural.resolve(state, Plan([Step(kind='speak',...)])) -> results=[]; pytest tests/test_core.py -q -> **19 failed**, все test_40_contracts_preserve_deferred_actions[*], IndexError на tests/test_core.py:172.
- Как починить: вернуть results.append({'type':step.kind,'text':step.text,'target':step.target}) в конец тела цикла.

### D2 (критический, УЖЕ ИСПРАВЛЕН В ПРОЦЕССЕ) — solo/world_service.py:31 / solo/nat20_api.py:464: _recovery_started в Strict-состоянии
DurableState — Strict (extra='forbid'), поля _recovery_started нет; запись в runtime ломала сохранение->перезапуск: '1 validation error for DurableState / _recovery_started / Extra inputs are not permitted'.
- Сейчас: nat20_api.py передаёт копию recovery_runtime; tests/test_nat20_command_cycle.py::test_http_native_cycle_save_new_process_continue_and_memory -> passed.
- Хвост: solo/world_service.py:31 всё ещё пишет runtime['_recovery_started']=int(before) в живой runtime-дикт — нужен такой же копирующий приём.

### D3 (высокий) — solo/game.py:534-778: Nat20-чат вообще не использует слой Соло
play_command не содержит natural, check_for, master_ruling, room_for_roll, guard( (проверено inspect.getsource). Решение «нужен ли бросок и какой id» принимает единолично модель.
- Следствие: слои (b) «печатная проверка с происхождением» и (c) «решение мастера» в реальном пути реплики отсутствуют; origin/разделение слоёв живут только в legacy-пути _play.
- Проверка: inspect.getsource(Game.play_command) не содержит check_for.

### D4 (высокий) — solo/game_commands.py:14-16: Сл мастера нельзя передать в Nat20
Check имеет только kind и check_id; полей dc/skill/ability нет, extra='forbid' -> ValidationError: Extra inputs are not permitted. DC всегда берётся из definition.checks/CHECKS (solo/nat20_worker.py:283-290). Пункт поручения 5 («check с выбранным id/Сл») невыполним по контракту.

### D5 (высокий) — solo/game.py:731-734 + solo/game.py:156: отказ вместо уточнения
Незарегистрированный check_id (напр. acrobatics) даёт 'Мастер отказал: действие невозможно в текущем состоянии. Герой не пострадал.' (refusal_text), при реальной причине 'Check is not in the trusted scene registry'.
- Ожидается: честное объяснение + уточнение («навык не входит в сцену; назовите навык/характеристику»).
- Проверено через HTTP: status=rejected, текст отказа.

### D6 (средний) — solo/natural.py:212: whitelist остаётся для speak
check_for для kind=='speak' пропускает только убеждаю|уговариваю|обманываю|лгу|пытаюсь убедить. «запугиваю стражника» -> None, хотя room_for_roll('запугиваю стражника') == True и inferred_skill знает intimidation. То же для observe (строка 213).
- Проверка: natural.check_for(Step(kind='speak', text='запугиваю трактирщика'), state) -> (None,None,None).

### D7 (средний) — solo/natural.py:161-164 vs solo/natural.py:177-180: нужен бросок, но не назначается
room_for_roll знает перелезаю|перепрыгиваю|взламываю..., а inferred_skill — нет (перелезть, выслеживаю). Для kind='check' без ability master_ruling возвращает None.
- Проверено: check_for('пытаюсь перелезть через забор') -> None при room_for_roll=True; inferred_skill('пытаюсь перелезть через забор') == ''; «выслеживаю зверя» -> None при room_for_roll=True.

### D8 (средний) — solo/natural.py:229 + solo/natural.py:168-198: очевидное получает бросок
«ищу монету в кармане» -> DC15 dexterity, origin=master. Бросок «по смыслу» (room_for_roll) без оценки неопределённости.
- Проверка: check_for(Step(kind='check', text='ищу монету в кармане'), state) -> не None.

### D9 (средний) — solo/natural.py:223 + solo/natural.py:200-202: origin='source' без происхождения
Печатная проверка без source/page/title даёт origin='source', source_ref=''. Заявленное «печатные проверки с происхождением (source/page)» не гарантировано.
- Проверка: place.checks без полей source/page -> _provenance()=='' при origin=='source'.

### D10 (средний) — solo/nat20_worker.py:293-294: SceneCheck без advantage
Код читает check.advantage/check.disadvantage при is_prototype; SceneCheck (solo/nat20_campaign.py:27-38) таких полей не имеет: AttributeError: 'SceneCheck' object has no attribute 'advantage'. PrototypeCheck их имеет (solo/nat20_prototype.py:68-69).

### D11 (средний) — solo/play_screen.py:50-60: кнопки игры в Nat20-кампании
commands_for выдаёт «Проверить: athletics / perception / strength» (плюс отдых/бой). Требование «кнопок игры не добавлять» нарушено.
- Проверка: commands_for(store.campaign(cid), None) для Nat20-кампании -> 3 кнопки «Проверить: ...».

### D12 (высокий) — solo/rulesets.py: нет prerequisite «невозможно»
Пункт поручения 4 в rulesets.py отсутствует: нет ни «невозможно», ни impossible вне текстов ошибок. Невозможное не блокируется — любая проверка с DC проходит по правилам движка.

### D13 (низкий) — подготовка не извлекает книжные проверки
solo/preparation_pipeline.py / solo/preparation.py / solo/preparation_format.py: упоминаний checks как извлекаемых данных нет; place.checks заполняется только прототипными путями (solo/prototype_prepared.py:382). Заявленное «подготовка извлекает книжные проверки с источником» в коде не подтверждается.

## Сводка по вопросам поручения
- place.checks как белый список: D6 (speak/observe) и D13 (данных нет); в Nat20-чате вообще не используется (D3).
- Отказ вместо уточнения: D5.
- Бросок не нужен, но назначается: D8. Нужен, а не назначается: D6, D7.
- Сл мастера как факт источника: D9 (+ D4 — Сл мастера не доходит до Nat20).
- Число механики считает не Nat20: в Nat20-чате всё считает Nat20 (подтверждено); в legacy-пути считает RulesetEngine (solo/natural.py:347) — допустимо только вне Nat20-кампании.
- Обрыв пути: D1 (пустые results), D3 (нет слоя Соло), D5.
