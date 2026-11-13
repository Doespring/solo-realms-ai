# ЗАДАЧА r128c · Вернуть байты вендора в согласие с пинами (68 файлов)

Дата: 30.10.2026. Постановщик — прораб (`default`). Исполнитель — `coder`.
Дерево: `/home/hermes/projects/SoloRealmsAI-Nat20`, ветка `work/nat20-engine`,
HEAD `fa5c802`. Windows-пути игнорировать.

> Мелкая, но блокирующая задача: пока она не закрыта, приёмочный тест чистого
> клона красный, и любой следующий отчёт по движку начинается с ложного
> «что-то сломано». Новых механик здесь нет.

---

## 0. Что я проверил сам (перепроверять не надо)

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_clean_copy_reproduces_the_engine.py -q
→ 1 failed, 3 passed
  FAILED test_every_divergence_from_upstream_is_a_declared_patch
  AssertionError: files changed without a declared patch: ['LICENSE', 'LICENSES.md',
  'NOTICE', 'packages/dnd5e-engine/LICENSE', ... 68 путей]
```

**Причина — не правка кода, а концы строк.** Пин в `vendor/nat20/UPSTREAM.json`
считался по CRLF-байтам (дерево приехало с Windows), а в рабочем дереве файлы
сейчас с LF. Проверено побайтово на четырёх файлах из разных каталогов:

```
views.py   pin d8ffbef2a479  CRLF-вариант d8ffbef2a479  == True
LICENSE    pin a6642f355f0c  CRLF-вариант a6642f355f0c  == True
rules/dice.py  pin 977dcff76816  CRLF-вариант 977dcff76816  == True
tools/regen.py pin bb2b7d34952a  CRLF-вариант bb2b7d34952a  == True
```

То есть содержимое совпадает, расходятся только концы строк. `git status`
этих файлов не показывает (в git лежит LF-версия), поэтому расхождение видно
только тесту чистого клона.

## 1. ЦЕЛЬ

Пин-тест зелёный, и ни один файл не «подогнан»: у каждого восстановленного
файла доказано, что CRLF-вариант побайтово равен объявленному пину.

## 2. ФАЙЛЫ

- `vendor/nat20/**` — **только** те 68 файлов, что перечислены в провале.
- `reports/ENGINE_WORKSPACE_PROGRESS.md`, `STATE.json` — раздел r128c.

## 3. НЕ ТРОГАТЬ

- `vendor/nat20/PATCHES.json`, `vendor/nat20/CORPUS.json`, `vendor/nat20/UPSTREAM.json` —
  **пины не переписывать**. Если файл после конвертации в CRLF не сходится с
  пином, это настоящее расхождение содержимого: остановиться, записать путь в
  отчёт, не трогать файл.
- `git checkout -- vendor`, `git clean`, `reset --hard`, `git add .`.
- Логику, тесты, корпус правил.

## 4. ЧТО ИМЕННО СДЕЛАТЬ

1. Взять список путей из вывода теста (не из моего текста — он может быть
   длиннее/короче).
2. Для каждого: прочитать байты, если в файле нет `\r\n`, сделать
   `raw.replace(b"\n", b"\r\n")`; посчитать sha256; сравнить с
   `UPSTREAM.json['files'][rel]`.
   - совпало → записать файл этими байтами;
   - не совпало → **не писать**, добавить путь в раздел «не сошлось» отчёта.
3. Файлы писать скриптом (`read_bytes`/`write_bytes`), не `write_file`/`patch`:
   эти инструменты сериализуют `\n` и вернут LF обратно.
4. Ничего не коммитить: это восстановление пинов, а не правка. Если решишь
   коммитить — только явным списком и отдельным коммитом, с записью в отчёт.
5. Прогнать тест и второй пин-тест: `scripts/verify_pins.py` из скилла
   `solo-realms-coding` (сверить разом `PATCHES.json` и `CORPUS.json`).

## 5. ПРИЁМКА

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_clean_copy_reproduces_the_engine.py -q
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_release_identity.py tests/test_nat20_release_manifest.py -q
```

Ожидание: первый — 4 passed (было 1 failed, 3 passed). Второй — состав падений
не хуже, чем был (если он падает по другой причине, назвать её).

## 6. ОТЧЁТ (10 строк max)

```
ВОССТАНОВЛЕНО: N файлов (все сверены с пином)
НЕ СОШЛОСЬ: <пути или "нет">
ТЕСТЫ: <команда → было/стало>
COMMIT: не делал / <sha>
```

## 7. ТОЧКА ПРОДОЛЖЕНИЯ

Строка в `reports/ENGINE_WORKSPACE_PROGRESS.md`, раздел «r128c». Следующая
карточка (r129, Помощь/Подготовка/Засада) стартует чтением этого раздела.
