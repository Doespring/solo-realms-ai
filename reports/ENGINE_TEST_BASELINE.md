# Базовая линия набора движка (раунд 1)

Команда: `SoloRealmsAI-Nat20\.venv\Scripts\python.exe -m pytest -q --no-header --tb=no`.

## Числа

**1354 passed, 134 failed, 6 errors, 1 skipped** (31 мин 31 с).

Пять тестов исключены явным `--ignore`, потому что импортируют модуль `rules_data`,
удалённый при переработке движка (см. `PHB2024_ENGINE_BASELINE.md`, раздел 4):
`test_hybrid_rules_integration.py`, `test_hybrid_rules_runtime.py`, `test_phb_chapter_one.py`,
`test_schema_validator.py`, `test_nat20_playable_rules.py`.

## Группировка 134 провалов

| файл | провалов |
|---|---|
| `tests/test_hybrid_rules_production.py` | 48 |
| `tests/test_phb_chapter_one_production.py` | 41 |
| `tests/test_nat20_bless.py` | 6 |
| `tests/test_preview9_clarification.py` | 4 |
| остальные 33 файла | по 1 |

Восемьдесят девять провалов из 134 — это **одна причина**: гибридный слой правил PHB-01.

## Причина 89 провалов: каталог определений отсутствует

`solo/hybrid_rules.py` ссылается на

- `DEFINITION_ROOT = ROOT / "rules_data/v1/chapters/phb-01"` (строка 21),
- `CHAPTER_PATH = ROOT / "rules_data/v2/chapters/phb-01/01-core.rules.json"` (строка 28),

и пинит три определения (`advantage`, `disadvantage`, `cancellation`).

Каталога `rules_data` **нет ни в рабочем дереве, ни в истории git**:
`git log --all -- rules_data` пуст, `git ls-tree -r 9723a04 -- rules_data` пуст,
`.gitignore` его не упоминает. Поэтому `verified_pack()` не может прочитать ни одного
определения и эндпоинт `GET /api/hybrid-rules/verified` отвечает 409.

## Побочно найденный и исправленный дефект: пин движка зависел от перевода строк

`verified_pack()` сверял `hashlib.sha256((ROOT / "solo/rulesets.py").read_bytes())` с
`ENGINE_SHA256 = a91628e7…`, посчитанным по LF.

На Windows файл лежит с **CRLF** (180 строк), поэтому сырой SHA-256 равен `127d815a…`
и сверка падала с сообщением «Engine dependency changed; revalidation is required».
Это **ложное утверждение о коде**: `git diff 9723a04 HEAD -- solo/rulesets.py` пуст,
файл не менялся. Различие только в чекауте.

Исправлено: хеш считается по байтам с нормализованным переводом строк
(`.replace(b"\r\n", b"\n")`), и пин сходится без всякой правки кода правил.

## Что это значит для поручения

Гибридный слой 89 тестов проверяет **не механку Nat20**, а посредник между
`solo/rulesets.py` и определениями правил PHB-01. Механику считает движок
`dnd5e_engine`, и его собственные тесты в этой группе не участвуют.

## Следующая операция

Пересчитать базовую линию после исправления нормализации и отделить провалы, которые
зависят от отсутствующего `rules_data`, от тех, что относятся к самому движку.
