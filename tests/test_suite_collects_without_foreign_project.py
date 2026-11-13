"""Политика: сбор тестов не падает из-за чужого проекта.

Два тест-файла движка импортировали `rules_data.runtime.*` и
`rules_data.testing.schema_validation` — модули, которых нет ни в этой папке,
ни в соседнем приложении: правила помечены в KNOWN_GAPS как «каталог
rules_data отсутствует». Из-за одного импорта рушилась СБОР ВСЕГО прогона,
а не только этих файлов.

Проверка держит достигнутое: каждый тест-файл обязан собираться без
`rules_data` в зависимостях, а весь каталог tests/ обязан собираться целиком.
"""
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FORBIDDEN = "from rules_data"
#: Файлы, которые сохраняют историю удалённой сборки: их импорт обёрнут
#: importorskip и собирается без падения. Они объявлены, а не спрятаны.
HISTORY = {
    "test_hybrid_rules_integration.py",
    "test_hybrid_rules_runtime.py",
}


def test_no_test_file_imports_a_missing_runtime():
    offenders = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name in HISTORY:
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "importorskip" in text, "%s импортирует мёртвый модуль без importorskip" % path.name
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "allow_module_level=True" in text or "importorskip" in text:
            # Импорт под защитой: без модуля файл пропускается, а не рушит сбор.
            continue
        for line in text.splitlines():
            if line.strip().startswith(FORBIDDEN):
                offenders.append("%s: %s" % (path.name, line.strip()))
    assert offenders == [], "мёртвый импорт чужого проекта: %s" % offenders


def test_the_whole_suite_collects():
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=TESTS.parent, capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "error during collection" not in done.stdout.lower(), done.stdout[-2000:]
