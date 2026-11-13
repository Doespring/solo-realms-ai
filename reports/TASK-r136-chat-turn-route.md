# ЗАДАЧА r136 · Чат для nat20-кампании: реплика доходит до движка

Дата: 30.10.2026. Постановщик — прораб (`default`). Исполнитель — `coder`.
Дерево: `/home/hermes/projects/SoloRealmsAI-Nat20`, ветка `work/nat20-engine`.
**Блокер прототипа**: без этой правки режим «только чат» не играется.

> Один класс работы: маршрут хода из чат-поля для кампании backend=nat20.
> Гейт `one_backend` не ослаблять.

---

## 0. Что я проверил сам (живой прогон, не догадка)

Chromium 156 + Playwright установлены на этой машине (без root, библиотеки
распакованы в `~/.local/chromium-libs`). Прогон
`/home/hermes/.hermes/cache/scratch/pw/browser_chat_path.py` на живом сервере
(`create_app(..., PreviewMock())`, порт 19313, офлайн-мок, 0 paid):

```
STEP chat_shell  status=200 has_inventory_js=false has_game_screen_js=false
                 has_player_input=true scripts=1
NODE panels_in_dom   {"present": []}
NODE controls        {"nav":0,"visibleButtons":1,"texts":["Отправить →"]}
NODE composer        {"input":1,"send":1}
NODE feed            {"chars":359, tail:"...МАСТЕР ... Смотрительница Лада просит проверить следы"}
NODE player_line_visible {"playerEcho": false}
consoleErrors: ["Failed to load resource: 409 (Conflict)", "CSP inline style"]
```

То есть оболочка правильная: панелей нет, навигации нет, видимая кнопка одна.
**Но реплика игрока не появляется в ленте: `POST /api/campaigns/{id}/play`
отвечает 409.**

Причина — в коде: `solo/playable_ui.py:129` считает «сервисными» только
маршруты `command-turn|commands|screen/commands|saves|restore/…|undo|notes|dice|master|gm|level-up`.
`play` в этом списке нет, поэтому гейт отвечает
`409 command_backend_required: «В этой кампании действия выполняются через
игровой экран Nat20. Прежний обработчик отключён.»`

**Правильный маршрут для чата уже существует:**
`POST /api/campaigns/{campaign}/command-turn` с телом
`{request_id, text, expected_version}` (`solo/app.py:114`). Именно так ходит
`desktop_observations.py:88`. Это Nat20-интерпретатор, а не второй движок.

## 1. ЦЕЛЬ

Игрок в режиме `?ui=chat` пишет фразу → она появляется в ленте → движок
считает ход → в ленте ответ мастера. Ни один запрос не получает 409.

## 2. ФАЙЛЫ

| Файл | Что делать |
|---|---|
| `web/play.js` | `sendAction()`: для кампании `backend === 'nat20'` слать в `/command-turn`, для остальных — как сейчас |
| `tests/test_chat_turn_reaches_engine.py` | **новый файл** (см. приёмку) |
| `reports/ENGINE_WORKSPACE_PROGRESS.md` | раздел r136 |

## 3. НЕ ТРОГАТЬ

- **`solo/playable_ui.py` — whitelist не расширять.** Добавить `play` в список
  значило бы вернуть второй (legacy) движок для nat20-кампаний. Запрет верный,
  чинить надо клиент, а не гейт.
- `solo/game.py` (`Nat20 использует command-turn`) — уже правильно.
- `vendor/**`, `PATCHES.json`, корпус, экранные команды.
- Чужие незакоммиченные файлы; никаких `git add .`, `git checkout --`,
  `reset --hard`, `git clean`.
- Полный прогон не запускать (2 vCPU). Точечные файлы через `nice -n 10`.

## 4. ЧТО ИМЕННО СДЕЛАТЬ

1. **Определять backend по состоянию кампании**, а не по флагу в браузере:
   `GET /api/campaigns/{id}` уже отдаёт `backend` (см. `web/play.js`,
   `current.backend` или аналогичное поле — проверить по коду).
2. **Тело запроса** — как ждёт `command-turn`: `request_id` (UUID одной
   попытки), `text`, `expected_version` (из текущего состояния). Не выдумывать
   новые поля.
3. **Ответ** — тем же путём, что сейчас: обновление состояния + сообщение в
   ленте. Если `command-turn` вернул событие/текст, показать его; при ошибке —
   человеческий текст, а не 409 в консоли.
4. **Остальные кампании не ломать:** если backend не nat20 — прежний `/play`.
5. **Повтор запроса:** `request_id` не перегенерировать на ретрае того же
   сообщения (idempotency), иначе двойной ход.

## 5. ПРИЁМКА

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 nice -n 10 .venv/bin/python -m pytest tests/test_chat_turn_reaches_engine.py -q
SOLO_TEST=1 nice -n 10 .venv/bin/python -m pytest tests/test_first_playable.py tests/test_chat_only_shell.py -q
```

`test_chat_turn_reaches_engine.py` обязан утверждать:

- nat20-кампания + `POST /api/campaigns/{id}/command-turn` с фразой →
  200 и в состоянии кампании появился ход/событие (не 409);
- `POST /api/campaigns/{id}/play` для той же кампании по-прежнему **409**
  `command_backend_required` (сторож: защита не сломана);
- клиентская половина: в `web/play.js` есть ветка по `backend`, и она выбирает
  `command-turn` (статический тест, как делали для двойного `/api` в r124).

Живой прогон в браузере делаю я сам после коммита:
`. ~/.local/chromium-libs/env.sh` + `browser_chat_path.py`, ожидание
`playerEcho: true` и отсутствия 409.

## 6. ОТЧЁТ (10 строк max)

```
МАРШРУТ: <какой путь выбирает клиент и почему>
ТЕСТЫ: <команда → числа>
409: <что вернул /play для nat20 после правки>
ЧТО ПРОВЕРИТЬ РУКАМИ: <осталось прорабу>
КОММИТ: <sha>
```
