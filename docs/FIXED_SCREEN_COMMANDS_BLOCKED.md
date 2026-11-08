# Найденный и исправленный дефект: экран Nat20 не мог отправить команду

Дата: 27 октября 2026.

## Симптом

`POST /api/campaigns/{cid}/screen/commands` на кампании с Nat20 отвечал **409**
`command_backend_required` — то есть игровой экран движка не мог выполнить ни одного действия через HTTP.

```
{"detail":{"code":"command_backend_required",
  "message":"В этой кампании действия выполняются через игровой экран Nat20. Прежний обработчик отключён."}}
```

Сообщение при этом утверждает, что действие выполняет экран Nat20, — а блокировался именно его эндпоинт.

## Причина

`solo/playable_ui.py:128` — middleware `one_backend` пропускает только пути из списка:

```python
service=position==1 and bool(re.fullmatch(
    r'(command-turn|commands|saves|restore/[^/]+|undo|notes|dice|master|gm|level-up)', suffix))
```

Эндпоинт экрана — `screen/commands` (`solo/play_screen.py:186`) — в список не входил,
поэтому попадал под общий запрет «в этой кампании действует только Nat20-экран».
Это была ошибка в собственном исключении: заблокирован оказался тот самый обработчик,
ради которого запрет и вводился.

## Исправление

`screen/commands` добавлен в список исключений. Эндпоинт `play_screen.py:186` и так отправляет
команду в тот же backend (`command_backend`), что и `commands`, — второго калькулятора не появилось.

## Проверка

`tests/test_play_screen.py::test_screen_uses_engine_commands_and_projects_engine_rolls`
теперь строит кампанию с движком (через `Nat20GameEngine.create_experimental_campaign`)
и проходит полностью:

- экран отдаёт `command_turn`, GET не меняет ни состояние, ни историю;
- `POST screen/commands` → 200, повтор возвращает тот же ответ;
- `snapshot.last_roll.label == 'Проверка'` — реальный бросок движка;
- `engine_snapshot.event_id == 'nat20:screen-check'` — событие записано движком.

Итог файла: **4 passed**.

## Осталось

`tests/command_turn` и остальные тесты, ходившие через `scripts.command_smoke.setup`,
по-прежнему создают кампанию без Nat20. Это отдельная группа (см. `docs/FAILURE_CLASSIFICATION.md`),
её надо переводить на кампанию с движком по одному тесту, сохраняя проверяемый смысл.
