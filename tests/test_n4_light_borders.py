"""N4: границы контролов в светлой теме — токены, а не точечная покраска.

WCAG 1.4.11: граница элемента управления считается видимой при 3:1 к фону.
До N4 светлая `--line` (#D9CBAF) давала 1.21–1.47:1 на всех фонах приложения,
`--line-strong` (#B49F7D) — 1.94–2.36:1: рамка поля исчезала в заливке.

Тест читает `web/design-tokens.css` и считает ratio по WCAG для каждой пары
«линия / фон», на которых эти линии реально рисуются. Тёмная тема не
ужесточается: там границу держит заливка, и 1.2–1.4 — осознанное решение.
"""
import re
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[1] / 'web' / 'design-tokens.css'

# Фоны, на которых стоят рамки контролов и ячейки листа (значения светлой темы).
LIGHT_BACKGROUNDS = {
    'sheet-bg': '#FBF5E8',      # лист героя, карточки
    'surface2': '#F6EEDF',      # вкладки блокнота, кнопки
    'ink-850': '#EFE7D9',       # композер
    'bg': '#F3ECE0',            # стол
    'ink-800': '#E9DFCE',       # самая тёмная подложка — худший случай
}


def _block(selector: str) -> str:
    """Тело CSS-блока по точному селектору (`:root`, `[data-theme="light"]`)."""
    text = TOKENS.read_text(encoding='utf-8')
    m = re.search(re.escape(selector) + r'\{([^{}]*)\}', text, re.S)
    assert m, f'блок {selector} не найден'
    return m.group(1)


def _token(block: str, name: str) -> str:
    m = re.search(rf'{re.escape(name)}\s*:\s*(#[0-9A-Fa-f]{{6}})', block)
    assert m, f'токен {name} не найден'
    return m.group(1).upper()


def _luminance(color: str) -> float:
    color = color.lstrip('#')
    channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_light_line_reaches_3_to_1_on_every_background():
    block = _block('[data-theme="light"]')
    line = _token(block, '--line')
    table = {name: round(ratio(line, bg), 2) for name, bg in LIGHT_BACKGROUNDS.items()}
    worst_name = min(table, key=lambda k: table[k])
    assert table[worst_name] >= 3.0, (
        f'светлая --line {line} даёт {table[worst_name]}:1 на {worst_name} '
        f'(нужно ≥3:1 по WCAG 1.4.11); полная таблица: {table}')


def test_light_line_strong_reaches_3_to_1():
    block = _block('[data-theme="light"]')
    strong = _token(block, '--line-strong')
    table = {name: round(ratio(strong, bg), 2) for name, bg in LIGHT_BACKGROUNDS.items()}
    worst_name = min(table, key=lambda k: table[k])
    assert table[worst_name] >= 3.0, (
        f'светлая --line-strong {strong} даёт {table[worst_name]}:1 на {worst_name} '
        f'(рамка композера); таблица: {table}')


def test_light_lines_are_still_two_steps():
    """--line-strong остаётся сильнее --line, иначе пропадает «строгая» линия."""
    block = _block('[data-theme="light"]')
    assert _luminance(_token(block, '--line-strong')) < _luminance(_token(block, '--line'))


def test_dark_line_left_subtle():
    """Тёмную тему не ужесточаем: там границу держит заливка (1.2–1.6:1)."""
    block = _block(':root')
    line = _token(block, '--line')
    surface = _token(block, '--surface')
    value = round(ratio(line, surface), 2)
    assert 1.0 <= value <= 2.0, f'тёмная --line {line} на {surface} = {value}:1 — рамки стали грубыми'


def test_placeholder_uses_palette():
    """Браузерный #757575 давал 4.00:1 при 16 px — ниже AA. Нужен --text-3."""
    text = TOKENS.read_text(encoding='utf-8')
    assert '::placeholder{color:var(--text-3);opacity:1}' in text
