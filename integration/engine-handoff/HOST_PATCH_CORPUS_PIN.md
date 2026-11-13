# Доказательство для фикса пина корпуса

## Что именно ломается

`solo/nat20_prototype.py:120-133` (`bundled_release`):

```python
lock = read_json(ROOT/'vendor/nat20/UPSTREAM.json')          # строка 124
expected = {name.removeprefix(prefix): sha
            for name, sha in lock['files'].items()
            if name.startswith(prefix) and name.endswith('.json')}   # 126
if manifest['files'] != expected:
    raise ValueError('Experimental corpus manifest differs from upstream pin')  # 127-128
verify_tree(DATA, expected, suffix='.json')                  # 129
```

`expected` строится **только** из `UPSTREAM.json`. `PATCHES.json` не применяется,
поэтому ожидаемый набор расходится с фактическим корпусом.

## Измеренное расхождение

Сверка всех записей `UPSTREAM.json` с рабочим деревом (`vendor/nat20`),
SHA-256 по сырым байтам:

| Категория | Файлов |
|---|---|
| Совпадает с эталоном | 1742 |
| Расхождение, объявленное в `PATCHES.json` | 18 |
| **Расхождение, не объявленное нигде** | **1** |
| Отсутствует | 0 |

Итого 1761 запись (`UPSTREAM.json`).

## Единственный виновник

`packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/spells/fireball.json`

| | Значение |
|---|---|
| Ожидает `UPSTREAM.json` | `309ce863e9b0e72287478926436026b666460a8d7da7ca59fe7ef1f95b349ab0` |
| В рабочем дереве | `68f49e47ee1223ba0ac202b5a0331ef188898bb4b19fd950bd1bc4e762b91c5f` |
| Размер | 4021 байт в обоих случаях |
| Переводы строк | LF (не CRLF) — версия из CRLF здесь не поможет |

## Почему это нельзя разрешить заменой отпечатка

Коммит эталона `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa` **отсутствует в этом
репозитории** (`git cat-file -t` возвращает ошибку). `UPSTREAM.json` — снимок
внешнего состояния, а не локальная ревизия. Поэтому сравнить «какая из двух
версий верна» по истории невозможно.

Единственный файл в истории репозитория, касающийся этого пути, — `d464693`,
который **добавляет** файл (165 строк, все с `+`). Версии `309ce863…` в репозитории нет.

## Проверка смысла механики (п.10: не обновлять отпечаток без проверки смысла)

Содержимое `fireball.json` в рабочем дереве сверено с SRD 5.2:

| Свойство | Значение в файле | SRD 5.2 |
|---|---|---|
| Уровень | 3 | 3 |
| Школа | `evo` | Evocation |
| Дальность | 150 ft | 150 feet |
| Урон | 8d6 fire | 8d6 Fire |
| При спасброске | `on_save: half` | half damage on a success |
| Усиление | +1d6 за уровень | +1d6 per level above 3rd |
| Происхождение | `965ad2d0cf5d063dac675ba078b5bd3c3c0dd449` | тот же коммит Foundry |

Механика верна. Расхождение — не дефект правил.

## Что должен сделать интегратор

В `bundled_release()` применять `PATCHES.json` к `expected` перед сравнением,
как это уже делает `solo/nat20_content.py:97-183` для отпечатка релиза:

```python
reviewed = {x['path']: x['sha256'] for x in read_json(PATCHES)['files']}
prefix = 'packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/'
for name, sha in reviewed.items():
    if name.startswith(prefix) and name.endswith('.json'):
        expected[name.removeprefix(prefix)] = sha
```

Для `fireball.json` это означает: либо объявить его в `PATCHES.json` (что запрещено —
`solo/nat20_content.py:89` требует `.py`), либо восстановить версию `309ce863…` из
внешнего источника, либо исключить файл из сравнения с явным обоснованием.
Третье — единственное, что можно сделать без внешнего источника, но оно требует
решения владельца: это ослабление контроля целостности.

Пока ни один из вариантов не выбран, `test_nat20_playable_rules.py` и 5 других
файлов не собираются, а `rules_data` не подключён.

## Масштаб ущерба (измерено, раунд 33)

Один файл блокирует не «несколько тестов», а большую часть хост-проверок. Замеры:

| Набор | Отказов | Из них `fireball.json` |
|---|---|---|
| `test_nat20_wizard.py` + `levelup` + `import` + `prototype` | 51 | **51** |
| `test_hybrid_rules_production.py` + `phb_chapter_one_production.py` + `kit` + `bless` | 95 + 11 err | 11 |
| Полный прогон 56 файлов | 263 failed, 24 errors | см. ниже |

`test_nat20_wizard.py` — **26 из 29** отказов от этой строки:

```
E   ValueError: Content differs from pinned SHA: spells/fireball.json
    solo/content_verification_cache.py:154
```

### Важно: дефект НЕ внесён правками движка
`test_nat20_wizard.py` не менялся с коммита `9723a04` (исходный снимок). Прогон
на версии `orchestrator.py` из `3cf99e7` (до всех правок раундов 32–33) даёт те же
**26 failed, 3 passed**. Причина не в ветке `work/nat20-engine`.

### Что это значит для приоритета
Фикс пина освобождает больше проверок, чем любая отдельная механическая доработка
движка. Пока он не сделан, значительная часть приёмочных тестов хоста не исполняется.
