# r193 — полный базовый прогон `tests/`: имена падений

Прогон: юнит `nat20-r193.service` (`systemd-run --user --collect`), 2026-11-02,
батч-раннер `run_test_batches.py --batch 3`, дерево
`/home/hermes/projects/SoloRealmsAI-Nat20`, HEAD `f7e5635d`.
Данные: `reports/r193-run/results.jsonl` (117 записей), логи пакетов
`reports/r193-run/logs/bNNN.txt`.

**Итог одной строкой:** файлов 350, пакетов 117, passed 3234, failed 8,
skipped 38, OOM-обрывов 0.

- время pytest по пакетам — 3 203 с (53,4 мин), wall ~58 мин;
- самый долгий пакет — `b009` (179 passed, **4:03**), таймаутов пакета нет;
- юнит закрылся `inactive`, `results.jsonl` последний раз изменён 23:37:59 UTC;
- `journalctl -k --since '2 hours ago' | grep -ci 'oom-kill|killed process'` → **0**;
- тесты и код не правились: прогон только измерял.

## Три ведра

| Ведро | Число тестов | Что это |
|---|---|---|
| (а) среда / версия Python | **1** | тест не знает про символ компилятора CPython 3.14 |
| (б) красные, чинятся тестом/данными, игрока не касаются | **3** | ожидание теста разошлось с корпусом |
| (в) настоящие дефекты (предсуществующие, не регрессии r174–r191) | **4** | английские слаги и маршрут «осматриваю» доходят до игрока |

Ни одно падение не относится к правкам r174–r191: все восемь воспроизведены
отдельным прогоном на HEAD `f7e5635d` (команды ниже), а два из них журнал уже
сверял с откатом через `git stash` (`reports/ENGINE_WORKSPACE_PROGRESS.md`,
разделы r85 и r188/r191).

## Ведро (а) — среда: 1

| Тест | Ошибка |
|---|---|
| `tests/test_no_unbound_names.py::test_no_module_reads_a_name_nothing_binds` | `armor_class.py in top reads __conditional_annotations__`, `damage_conditions.py …`, `nat20_content.py …` |

Причина не в коде: сам файл `solo/*.py` строку `__conditional_annotations__`
не содержит (`grep -rn` по `solo/` даёт только `.pyc`). Символ добавляет
**компилятор CPython 3.14** в таблицу модуля, где есть аннотация на верхнем
уровне. Проверка на четырёх строках в `.venv/bin/python` (3.14.9):

```
src = "x: int = 1\n\ndef f(a: str) -> int:\n    return 2\n"
symtable.symtable(src, 'm', 'exec').get_symbols()
→ ('__conditional_annotations__', is_referenced=True, is_assigned=False)
```

То есть тест ловит не дефект, а версию интерпретатора. Лечение — белый список
имени (рядом с `__file__`, `__name__`, `__doc__`, `__package__`) либо пропуск
символов, которых нет в исходнике. Карточка: «тест `no_unbound_names` знает
про Python 3.14» — 3 строки в тесте, кода движка не касается.

## Ведро (б) — красные от расхождения теста и корпуса: 3

| Тест | Ошибка | Суть |
|---|---|---|
| `tests/test_phb_classes_map.py::test_names_are_russian` (:279) | `phb2024.phb03.bard.vardo_vdohnovenie` | падает вторая проверка — `readiness.scope`, а не `name`: в русском тексте области стоят `D20` и `(r177)`. `name` = «Бард: Бардовское вдохновение», по-русски |
| `tests/test_nat20_wizard.py::test_a_subclass_step_appears_at_its_gate_level_only` (:81) | `assert ['champion', …ter.psi_voin'] == ['champion']` | тест ждёт литерал `['champion']`, корпус отдаёт ещё `phb2024.fighter.*`: сравнивать надо с пулом корпуса |
| `tests/test_pdf_pipeline.py::test_gemini_local_layout_then_qwen_structure_mocked_and_consent_gated` (:85) | `Regex pattern did not match` | гейт тарифа изображений (`solo/ai_roles.py:46`) отвечает раньше «Подтвердите» — ожидание мока устарело |

Все три — про то, что тест/фикстура отстали от данных, а не про поведение
игры. Две карточки по 5–15 минут каждая, третья (pdf) — разобрать, что именно
должен отвечать гейт, и либо поправить фикстуру, либо признать тест
устаревшим.

## Ведро (в) — настоящие дефекты: 4 (две причины)

### 1. Английские коды отказа доходят до игрока (2 теста)

| Тест | Ошибка |
|---|---|
| `tests/test_nat20_item_movement_and_initiative.py::test_every_cast_failure_reason_has_russian_text` (:1876) | `failure codes shown to the player in English: ['heavy_armor', 'raging']` |
| `…::test_no_slug_in_event_fields_reaches_the_player_in_english` (:1895) | `slugs shown to player in English: ['heavy_armor', 'raging']` |

Причина одна: в таблице переводов vendor
`vendor/nat20/packages/dnd5e-engine/src/dnd5e_engine/labels.py` (строка 1813 и
рядом) нет записей для двух кодов, которые `orchestrator.py` реально шлёт:
`heavy_armor` (`orchestrator.py:11156`, Rage в тяжёлой броне) и `raging`
(второй Rage в том же бою). `label(code)` возвращает сам код, игрок видит
`heavy_armor` вместо русского текста.

Это правка `vendor/**` ⇒ нужен пересчёт пина в `PATCHES.json` (см.
`solo-realms-coding`); в r193 такой правки не делалось намеренно.

### 2. «Осматриваю дверь» уходит в проверку Восприятия вместо `examine` (2 теста)

| Тест | Ошибка |
|---|---|
| `tests/test_tabletop_b.py::test_mapless_six_interactions_and_undo_idempotence` (:66) | `assert 'Поиск (дверь… 1 числа нет.' == 'Деревянная д… без защёлки.'` — ждали описание двери, получили результат поиска |
| `tests/test_tabletop_b.py::test_frozen_tabletop_probe_uses_public_endpoints_without_network` | `KeyError: 'action'` в `solo/desktop_observations.py:199` |

Оба — один маршрут: реплика «Осматриваю дверь» разбирается как поиск
(Восприятие), а событие без `action` роняет замороженную пробу. Игрок видит
бросок Восприятия вместо описания предмета — то есть путь осмотра в
бескартовой сцене не работает. Проверено: падает и на HEAD, и на откате правок
r188 (запись в журнале); к r174–r191 отношения не имеет, но дефект настоящий.

## Как воспроизвести (мои прогоны, HEAD f7e5635d)

```
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_tabletop_b.py -q --tb=line -p no:cacheprovider
→ 2 failed, 29 passed, 3 warnings in 6.88s (mapless + frozen probe)

SOLO_TEST=1 .venv/bin/python -m pytest \
  "tests/test_nat20_item_movement_and_initiative.py::test_every_cast_failure_reason_has_russian_text" \
  "tests/test_nat20_item_movement_and_initiative.py::test_no_slug_in_event_fields_reaches_the_player_in_english" \
  tests/test_nat20_wizard.py::test_a_subclass_step_appears_at_its_gate_level_only \
  "tests/test_pdf_pipeline.py::test_gemini_local_layout_then_qwen_structure_mocked_and_consent_gated" \
  -q --tb=line -p no:cacheprovider
→ 4 failed, 3 warnings in 6.15s

SOLO_TEST=1 .venv/bin/python -m pytest tests/test_no_unbound_names.py::test_no_module_reads_a_name_nothing_binds \
  tests/test_phb_classes_map.py::test_names_are_russian -q --tb=line -p no:cacheprovider
→ 2 failed in 1.27s
```

## Предлагаемая очередь карточек (в этом порядке)

1. `labels.py`: русский текст для `heavy_armor` и `raging` + перепин
   `PATCHES.json` (2 теста красных, игрок видит слаг).
2. «Осматриваю дверь» → `examine` в бескартовой сцене; заодно `action` в
   событии (2 теста красных).
3. `test_no_unbound_names` знает про `__conditional_annotations__` (1 тест).
4. `test_nat20_wizard` сравнивает пул подклассов с корпусом (1 тест).
5. `test_phb_classes_map::test_names_are_russian` — либо перевести `D20`/
   `(r177)` в `scope`, либо разрешить латиницу в служебном поле (1 тест).
6. `test_pdf_pipeline` — гейт тарифа против «Подтвердите» (1 тест).
