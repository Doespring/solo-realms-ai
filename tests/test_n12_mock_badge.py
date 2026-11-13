"""n12 — метка «Тестовые данные · mock» не должна попадать в обычную сборку.

Проверка без браузера: смотрим на то, что реально отдаётся игроку — HTML и
`play.js`. Метка живёт в `health()` и ставится только при явном тестовом флаге
(`?audit=1` или `?mock-badge=1`); без флага тестовый адаптер показывается как
неподключённый Мастер.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BADGE = 'Тестовые данные · mock'


def test_shell_has_no_test_data_markup():
    html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    assert 'Тестовые данные' not in html
    assert 'mock' not in html.lower()


def test_play_js_badge_is_behind_explicit_flag():
    source = (ROOT / 'web/play.js').read_text(encoding='utf-8')
    assert BADGE in source, 'метка должна существовать для тестового запуска'
    # Каждое вхождение метки — либо комментарий, либо статус Мастера с проверкой
    # флага. Любая другая установка текста (и правка условия) валит тест.
    for line in source.splitlines():
        if BADGE not in line:
            continue
        assert line.lstrip().startswith('//') or 'mock&&mockBadge()?' in line, line[:120]
    assert "q.get('audit')==='1'" in source and "q.get('mock-badge')==='1'" in source
