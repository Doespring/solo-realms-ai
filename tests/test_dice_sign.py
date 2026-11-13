"""r226: в интерфейсе стоит знак кубика дизайнера, а не временный рисунок r220.

Тест дешёвый и без приложения: он ловит расхождение копии символов на странице
с файлами `web/visual/dice/*.svg` и возврат временной объёмной фигуры.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / 'web'


def test_dice_sign_matches_designer_symbols():
    symbols = (WEB / 'visual/dice/symbols.svg').read_text(encoding='utf8').strip()
    flat = ''.join(line.strip() for line in symbols.splitlines())
    assert flat.count('<symbol') == 4
    script = (WEB / 'game-screen.js').read_text(encoding='utf8')
    assert flat in script, 'копия symbols.svg в game-screen.js разошлась с файлом дизайнера'
    for name in ('chip-24', 'chip-36', 'd20-64', 'd20-120'):
        assert (WEB / 'visual/dice' / f'{name}.svg').is_file(), name


def test_feed_chip_36_and_roll_view_d20_120():
    script = (WEB / 'game-screen.js').read_text(encoding='utf8')
    css = (WEB / 'game-screen.css').read_text(encoding='utf8')
    # Лента берёт чип 36, экран броска — гранёный d20 120; оба символа объявлены
    # на странице и подключены через <use href="#…">.
    assert 'id="sg-chip-36"' in script and 'id="sg-d20-120"' in script
    assert """<use href="#'+(big?'sg-d20-120':'sg-chip-36')""" in script
    assert 'roll-view' in script
    assert '</b> нужно' in script and '</b> выпало' in script and 'modifier' in script
    assert '.die--36{width:36px;height:36px}' in css
    assert '.die--120{width:120px;height:120px}' in css
    # Цвет исхода — токены темы, не литералы.
    assert '.o-hit{color:var(--moss)}' in css
    assert '.o-miss{color:var(--ember)}' in css
    assert '.o-crit{color:var(--candle)}' in css
    # Временный объёмный d20 из r220 больше не рисуется.
    assert 'die__face' not in css and 'DIE_SHAPE' not in script
