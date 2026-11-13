# r222 — что не хватает экрану боя для настоящей карты (спецификация передачи)

**Коротко.** Клиент боя готов рисовать сетку, но пакет экрана не несёт ни клеток,
ни позиций. `solo/nat20_screen.py` уже читает их для подписей команд и выбрасывает.
Нужна правка проекции на 8 строк — без единого расчёта правил. Карточка r222 не
имеет права править `solo/**`, поэтому правка описана здесь, а не сделана.

## Доказательство (прогон 03.11.2026, офлайн-мок, свой профиль)

```
$ SOLO_TEST=1 .venv/bin/python -m scripts.probe_r222_screen
кампания 8471e9cc-…
все команды: [… 'Начать столкновение: basic' …]
старт боя: 200
combat keys: ['active', 'actors', 'current_actor', 'round', 'terrain']
combat: {"active": true, "round": 1, "current_actor": "hero",
 "actors": [{"id": "hero", "name": "Проба карты", "team": "hero", "hp": 12, "max_hp": 12,
             "conditions": [], "initiative": 22},
            {"id": "goblin", "name": "Goblin Warrior", "team": "enemy", "hp": 10,
             "max_hp": 10, "conditions": [], "initiative": 11}],
 "terrain": null}
/combat: 200 null
кампания combat: null
```

То есть: у участников нет `position`, у боя нет `grid`; старый маршрут
`/api/campaigns/{id}/combat` для кампании Nat20 отдаёт `null` (он читает
`state['combat']`, а с r210 у Nat20-кампаний такого ключа нет).

При этом сервер эти данные **уже держит**: `store.campaign(id)` →
`state['nat20']['combat']['initial']['grid']`:

```
grid keys: ['blocked_cells','cell_size_ft','cover_cells','default_lighting',
            'difficult_terrain_cells','difficult_terrain_kinds','height','lighting',
            'obscurement_cells','sunlight','wall_segments','width']
grid: {"width": 7, "height": 5, "cell_size_ft": 5, "blocked_cells": [],
       "cover_cells": {}, "default_lighting": "bright", "sunlight": false}
view.actor_zone: {"hero": "1,1", "goblin": "4,1"}
```

`actor_zone` — 0-based, и это ровно те позиции, из которых `nat20_screen.py`
строит подписи `Перейти: 4, 2` (`x+1, y+1`). Клиент уже умеет обе формы
(`web/board.js`: `combat.map.cells` и `combat.grid` + `actors[].position`),
поэтому после правки карта появляется без единой строки в `web/**`.

## Правка (solo/nat20_screen.py, метод `Nat20Screen.project`, ветка `if runtime['combat']:`)

```python
            battle = dict(active=True, round=snapshot['combat']['round'],
                          current_actor=snapshot['combat']['turn'],
                          actors=[dict(id=a['id'], name=a['name'],
                                       team='hero' if a['id'] == 'hero' else 'enemy',
                                       hp=a['hp']['current'], max_hp=a['hp']['maximum'],
                                       conditions=a['conditions'], initiative=initiatives[a['id']],
                                       # r222: без позиции экран не может поставить токен.
                                       position=dict(x=a['position'][0], y=a['position'][1],
                                                     z=a['position'][2])) for a in snapshot['actors']],
                          # r222: сетка поля боя — те же данные, что уже читает terrain_label.
                          grid=dict(width=grid.get('width'), height=grid.get('height'),
                                    cell_size_ft=grid.get('cell_size_ft') or 5,
                                    blocked_cells=list(grid.get('blocked_cells') or ()),
                                    cover_cells=dict(grid.get('cover_cells') or {}),
                                    difficult_terrain_cells=list(grid.get('difficult_terrain_cells') or ()),
                                    default_lighting=grid.get('default_lighting') or 'bright'),
                          terrain=terrain_summary(grid))
```

`grid` в этой функции уже получен строкой `_, grid = _combat_view(runtime)` —
новых вызовов движка, воркера и предварительных расчётов не появляется.
`a['position']` есть у каждого актора снапшота (им же пользуется цикл
`for actor in snapshot['actors']` ниже, чтобы предложить `Перейти: x, y`).

## Что сделать тому, кто возьмёт карточку

1. Правка выше + тест в `tests/test_nat20_screen.py`: после `start_combat`
   `snapshot['combat']['grid']['width'] > 0`, у каждого актора есть `position`,
   и **ни один** ключ из `release_digest`, `native_events`, `rng_seed`,
   `engine_snapshot` не просочился (существующий тест это уже проверяет).
2. Отдельно проверить, что скрытые клетки (`blocked_cells`/`cover_cells`)
   не раскрывают `secret`: в Nat20 у сетки нет секретных клеток, но правило
   «экран не отдаёт больше, чем видит герой» должно остаться в силе —
   сравнивать с `terrain_summary(grid)` и `terrain_label(grid, cell)`.
3. Приёмка на экране: `#board` показывает сетку `7 × 5`, клетки ≥ 28 px на 375,
   страница не едет вбок, токены стоят на позициях `actor_zone`.

## Почему это не «правила в хосте»

Правка только копирует уже валидированное состояние движка в публичную проекцию.
Ни одного нового числа, порога или проверки не появляется: размеры, клетки, укрытия
и позиции приходят из `initial.grid` и `view.actor_zone` как есть.
