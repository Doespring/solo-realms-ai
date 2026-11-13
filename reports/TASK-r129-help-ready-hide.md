# ЗАДАЧА r129 · Помощь, Подготовка и Засада: из движка на экран и в корпус

Дата: 30.10.2026. Постановщик — прораб (`default`). Исполнитель — `coder`.
Дерево: `/home/hermes/projects/SoloRealmsAI-Nat20`, ветка `work/nat20-engine`,
HEAD `fa5c802` (r128b). Windows-пути из старых документов игнорировать.

> Одна задача — один класс работы. Здесь: **три действия, которые движок уже
> умеет, но игра не предлагает**. Новых правил из книги, классов и социальных
> действий не трогать — это следующие карточки.

---

## 0. Что я уже проверил сам (перепроверять не надо)

Прогон настоящим движком из `.venv` этого дерева
(`/home/hermes/.hermes/cache/scratch/nat20probe/probe_intents.py`,
`SOLO_TEST=1 .venv/bin/python`), бой человек-воин 1 ур. против монстра,
`rng_seed=5`:

```
OK   dodge            events=['IntentSubmitted','TurnPhase','TurnEnded','TurnStarted','TurnPhase']
OK   help             events=[...] IntentSubmitted:help
FAIL help-no-target   IntentRejectedError: target_invalid: actor_id='pc1' help target None
                      is not a living combatant within 5 ft
OK   ready            events=['IntentSubmitted']
OK   ready-no-trigger events=['IntentSubmitted']
FAIL hide             IntentRejectedError: target_invalid: ... is not behind Three-Quarters/Total
                      cover, Heavily Obscured, or in Darkness — Hide requires one of those
OK   disengage        events=['IntentSubmitted']
```

Выводы, на которых строится задача:

1. **Движок умеет `help`, `ready`, `hide`.** `PlayerIntent.intent_type` —
   закрытый литерал, в нём есть `... 'dodge', 'disengage', 'hide', 'help',
   'ready', 'reaction', ...`. Ни `search`, ни `study`, ни `influence` там нет
   (это следующая карточка, host-уровень).
2. **Отказ по `help`/`hide` — содержательный, а не «не умею»**: движок
   проверяет 5 футов и укрытие. Значит кнопку нельзя показывать, когда условие
   не выполнено, — иначе игрок получит `target_invalid`.
3. **`ready` регистрирует `_PendingReaction`** (`orchestrator.py:14896
   _register_pending_reaction`, разрешение — `_pop_pending_reaction:14976`).
   Произвольный «жду, пока он выйдет» движок НЕ умеет: триггеры ограничены
   литералом (`cast_spell`, `hit_by_attack`, `targeted_by_magic_missile`).
4. **Свежая матрица апстрима** (снята 30.10.2026 с
   https://tapestria.github.io/nat20/capabilities/): `Help` ✅ Resolved,
   `Hide` ✅ Resolved, `Dodge` ✅ Resolved, `Readied spell cast` ✅ Resolved,
   `Arbitrary SRD "Ready an action with a custom trigger"` ❌ Not modelled,
   `Flanking`/`Elevation`/`Multi-tile`/`Dispel Magic`/`Lair actions` ❌.
   Документ `GAP-TO-PROTOTYPE.md` устарел: он называет Help и Ready
   «Not modelled / blocked» — это неправда для текущего пина 0.9.0+solo.6.
5. **Экран предлагает из native-действий только** `('Уклонение','dodge'),
   ('Отход','disengage'), ('Встать','stand_up')` —
   `solo/nat20_screen.py`, блок `if experimental(runtime.get('definition')):`
   (строки ~56-62). Помощи, Подготовки и Засады в интерфейсе нет вовсе,
   хотя чат-путь их принимает (`solo/game_commands.py:90-92`,
   `NativeAction.operation`).

## 1. ЦЕЛЬ

Три действия доступны игроку на игровом экране, каждое предлагается только
когда движок его действительно примет, и три записи корпуса перестают врать
про их статус. Доказательство — новые тесты, исполняющие движок.

## 2. ФАЙЛЫ

| Файл | Что делать |
|---|---|
| `solo/nat20_screen.py` | добавить кандидатов `help` (только при живом союзнике в 5 фт), `ready` (триггер `hit_by_attack`), `hide` (только при выполненном условии укрытия/затемнения) |
| `rules_data/v2/chapters/phb-01/02-actions-social.rules.json` | три правила: `dnd2024.phb01.action_help`, `...action_ready`, `...action_hide` |
| `tests/test_nat20_help_ready_hide.py` | **новый файл**, проверки ниже |
| `web/game-screen.js` | только если выяснишь, что клиент фильтрует серверные кандидаты (в прошлый раз так было с `screen/commands`) |
| `reports/ENGINE_WORKSPACE_PROGRESS.md`, `STATE.json` | раздел r129 и обновление `proven`/`not_proven` |

## 3. НЕ ТРОГАТЬ

- `vendor/**` и `vendor/nat20/PATCHES.json` — правок движка в этой задаче нет;
  перепин пинов не требуется. Если всё же правишь `.py` вендора — значит
  задача понята неверно.
- Чужие незакоммиченные файлы (в дереве лежат `reports/*`, `_critic_run_mech.txt`,
  `reports/prototype-3/**`). Не делать `git add .`, `git checkout --`,
  `git clean`, `reset --hard`.
- Корпус класса `phb-03-classes` и любые новые разделы книги.
- Социальные действия (`influence`, `study`, `search`) — отдельная карточка.

## 4. ЧТО ИМЕННО СДЕЛАТЬ

1. **Помощь.** Кандидат появляется, только если в `snapshot['actors']` есть
   живой (hp > 0) союзник (`team == 'hero'`, не сам герой) в клетке по
   Чебышёву ≤ 1 от героя. Цель — `target_id` этого союзника. **Если союзника
   нет — кнопки нет** (в одиночной кампании это норма, и это честно; писать
   «Помощь» и получать `target_invalid` нельзя).
2. **Подготовка.** Кандидат `('Подготовиться: удар, если атакуют',
   dict(kind='native_action', operation='ready', reaction_trigger='hit_by_attack'))`.
   Не предлагать произвольный триггер и не обещать «жду, пока он выйдет» —
   это `Not modelled`. Кнопка остаётся видимой и после нажатия (действие не
   завершает ход сам по себе — проверь по `TurnPhase`, не додумывай).
3. **Засада.** Кандидат `hide` только когда герой в клетке с укрытием
   `three_quarters`/`total` (см. `GridScene.cover_cells`), либо в тяжёлом
   затемнении/темноте, если это выведено в снапшоте. Как читать признак из
   `snapshot` — решай по коду, но не угадывай: если признака в снапшоте нет,
   кнопку не показывать и записать это ограничение в отчёт строкой.
4. **Никаких выдуманных проверок.** Каждый кандидат по-прежнему проходит
   существующий `backend._preflight(current, command)` (цикл в
   `nat20_screen.py` уже это делает). Не ослаблять preflight, чтобы кнопка
   «появилась».
5. **Корпус.** У трёх правил поставить статус из **закрытого** списка:
   `readiness.status` ∈ {`adapter_verified`, `kernel_verified`,
   `engine_subset_verified`, `adjudication_required`, `blocked`};
   `implementation` ∈ {`existing_engine_subset`, `kernel_only_unexecuted`,
   `not_implemented`, `kernel_verified`}. Для трёх действий, которые движок
   исполняет своими хендлерами, правильный статус — `engine_subset_verified`
   с `handler` и `blockers: []`; в `formal_behavior` того же файла дописать
   поведение. Своё слово в `status` даёт `JSON Schema rejected instance` без
   имени поля.
6. **Где правда неполная — так и оставить.** `action_ready` покрыт только
   предустановленными триггерами: в `blockers`/`formal_behavior` это должно
   быть сказано, а не спрятано за «готово».

## 5. ПРИЁМКА (без вывода команды задача не закрыта)

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_nat20_help_ready_hide.py -q --tb=short
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_nat20_screen.py tests/test_r124_screen_commands.py -q
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_phb_chapter_one.py tests/test_phb_classes_map.py -q
```

Тест-файл обязан доказать исполнением, а не наличием строки:

- `help` без цели → `IntentRejectedError` с `help target None` (ветка отказа);
- `help` с союзником в 5 фт → `IntentSubmitted:help`, у союзника появляется
  источник `help`;
- `ready` + `hit_by_attack` → `_PendingReaction` зарегистрирован, и после
  атаки монстра в следующем ходу реакция разрешается (событие реакций /
  очередь пустеет) либо честно записано, что хост её не разрешает;
- `hide` без укрытия → `target_invalid` с текстом про укрытие; с укрытием
  `three_quarters` (seed из `tests/test_nat20_hide.py` = 5) → успех;
- на экране: кандидатов `help`/`hide` нет, когда условие не выполнено, и они
  есть, когда выполнено (сравнивать по списку кандидатов, а не по вёрстке).

Полный прогон в этой задаче **не нужен** (он идёт у прораба). Достаточно трёх
команд выше плюс назвать, сколько тестов было до/после.

## 6. ОТЧЁТ (15 строк max, без пересказа кода)

```
СДЕЛАНО: <одна строка>
ТЕСТЫ: <команда → числа до/после>
СТАТУСЫ КОРПУСА: <правило → было → стало>
ГДЕ НЕ УВЕРЕН: <что прорабу проверить руками>
ОГРАНИЧЕНИЯ: <что честно осталось вне>
КОММИТ: <sha>
```

Плохое — первым. Слова «работает» без вывода команды отчёт не принимает.

## 7. ЗАПИСАТЬ ТОЧКУ ПРОДОЛЖЕНИЯ

В `reports/ENGINE_WORKSPACE_PROGRESS.md` — раздел «r129», в `STATE.json` —
что теперь в `proven`, что осталось в `not_proven`. Следующая карточка
(социальные действия) стартует чтением этого раздела, а не репозитория.
