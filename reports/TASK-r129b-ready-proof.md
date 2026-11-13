# ЗАДАЧА r129b · Подготовка: доказать реакцию или убрать кнопку

Дата: 30.10.2026. Постановщик — прораб (`default`). Исполнитель — `coder`.
Дерево: `/home/hermes/projects/SoloRealmsAI-Nat20`, ветка `work/nat20-engine`.
Стартует сразу после r129 (уже done). Маленькая задача: 15-20 минут.

---

## 0. Что я проверил сам

`tests/test_nat20_help_ready_hide.py` → **9 passed**, `test_nat20_screen.py +
test_r124_screen_commands.py + test_phb_chapter_one.py + test_phb_classes_map.py`
→ **209 passed**. Это принято.

Не принято одно: экран предлагает
`('Подготовиться: реакция при попадании атакой', operation='ready',
reaction_trigger='hit_by_attack')`, а в корпусе того же файла ты сам записал
блокер: «Подготовленная атака оружием снимается из очереди без разрешения».

Прогон движка (`PlayerIntent(intent_type='ready', reaction_trigger='hit_by_attack')`)
даёт только `IntentSubmitted` — ни `TurnEnded`, ни события реакции. То есть
кнопка обещает механику, которой нет: игрок нажимает, Действие тратится,
реакция не наступает.

## 1. ЦЕЛЬ

Одно из двух, доказанное прогоном:

- **(а)** `ready` с `hit_by_attack` разрешается: есть тест «подготовился →
  противник атакует → событие реакции наступило, слот реакции потрачен»;
- **(б)** не разрешается: кнопка Подготовки уходит с экрана (и из чат-кандидатов),
  правило в корпусе остаётся `engine_subset_verified` с блокером, а в отчёте
  написано, что именно замерено.

Третий вариант — «оставить как есть» — не принимается.

## 2. ФАЙЛЫ

- `solo/nat20_screen.py` — кандидат `ready`.
- `tests/test_nat20_help_ready_hide.py` — тест-доказательство (а) или сторож (б).
- при варианте (б): `solo/command_engine.py` / `solo/game_commands.py` — если
  `ready` нужно убрать и из чат-пути, а не только с экрана (решение обосновать:
  чат-путь может принимать действие, даже если кнопки нет).
- `reports/ENGINE_WORKSPACE_PROGRESS.md` — строка «r129b».

## 3. НЕ ТРОГАТЬ

- `vendor/**`, `vendor/nat20/PATCHES.json` — движок не правим, пины не двигаем.
- Корпус `action_help` (уже `engine_subset_verified`), `action_hide` (blocked).
- Чужие незакоммиченные файлы; никаких `git add .`, `git checkout --`,
  `reset --hard`, `git clean`.
- Полный прогон: **не запускать** (машина 2 vCPU, гейтвей падал по watchdog от
  перегрузки). Только точечные файлы, через `nice -n 10`.

## 4. ПРИЁМКА

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 nice -n 10 .venv/bin/python -m pytest tests/test_nat20_help_ready_hide.py -q
SOLO_TEST=1 nice -n 10 .venv/bin/python -m pytest tests/test_nat20_screen.py -q
```

Плюс вывод замера: для варианта (б) — список кандидатов экрана до/после
(`['dodge','disengage','stand_up','help']`), для (а) — имена событий реакции.

## 5. ОТЧЁТ (8 строк max)

```
ВАРИАНТ: (а) доказано / (б) убрано
ЗАМЕР: <события / список кандидатов>
ТЕСТЫ: <команда → числа>
ГДЕ НЕ УВЕРЕН: <что проверить руками>
КОММИТ: <sha>
```
