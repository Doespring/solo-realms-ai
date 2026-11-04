# Раунд 3 — воспроизводимое состояние

Дата: 27 октября 2026.

## Рабочая папка, ветка, HEAD

```
папка  C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI
ветка  feature/critical-player-ux
HEAD   943eb0b
```

Изменённых файлов в рабочем дереве: 1898 (свои правки этого потока и чужие незакоммиченные
данные других потоков). Ничего не закоммичено; чужие ветки не переключались.

## Окружение

```
Python 3.12.10  .venv\Scripts\python.exe
```

## Фактические пути импорта

```
dnd5e_engine   vendor\nat20\packages\dnd5e-engine\src\dnd5e_engine\__init__.py
dnd5e_srd_data vendor\nat20\packages\dnd5e-srd-data\src\dnd5e_srd_data\__init__.py
dist dnd5e-engine   0.7.0
dist dnd5e-srd-data 0.7.0
```

Оба пакета импортируются **напрямую из `vendor/`**, а не из копии в `.venv`. Копирования
исходников в `.venv` нет: подключение сделано `.pth`-файлом плюс синтезированные метаданные
(`tools/connect_nat20_sources.ps1`). Это режим разработки, а не проверка будущей упакованной
поставки.

## Команда проверок

```
Set-Location <папка проекта>
.venv\Scripts\python.exe -m pytest -q --no-header
```

Существенные настройки среды: `SOLO_TEST=1` ставится в `tests/conftest.py`; режим временных
каталогов печатается в stderr строкой `tests: temp root …; mode normal|restricted`.

## Граница ограничения песочницы (измерена)

| режим mkdir | запись | listdir | chmod | rmtree |
|---|---|---|---|---|
| 0o700 | отказ | отказ | отказ | отказ |
| 0o750 | ok | ok | ok | ok |
| 0o770 | ok | — | — | ok |
| 0o701 | ok | — | — | ok |
| 0o710 | ok | — | — | ok |
| 0o600 | ok | — | — | ok |

Ломается **ровно один режим — `0o700`**. Это воспроизводится скриптами
`runtime/_probeA.py` и `runtime/_probeB.py` и закреплено тестом
`tests/test_sandbox_temp_mode.py`.

Прежнее утверждение «записывать можно только в существовавшие до процесса каталоги»
**не подтвердилось**: каталог, созданный процессом с режимом 0o770, полностью пригоден.

## Итог прошлого согласованного прогона

Режим `restricted`. **1978 passed, 24 failed, 9 errors, 5 skipped** (41 минута).
Все 24 провала — браузерные, все 9 ошибок — `test_nat20_bless`.
