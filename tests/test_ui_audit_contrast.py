"""Самопроверка замера контраста в `tools/ui_audit.py` (N5).

Замер обязан находить на фикстуре то, что положено: текст 1.37:1, текст на
полупрозрачной панели 2.82:1, границу поля 1.16:1, плейсхолдер 2.17:1,
границу-заливку композера и рамку через inset-тень — и обязан честно говорить
«не посчитать» там, где фон градиент или холст страницы не определён.

Смысл файла: «0 нарушений контраста» в отчёте должно означать измерение, а не
пустое поле. До N5 `contrastBad` был пуст во всех 40 кадрах n16 при живых
нарушениях: замер не видел ни прозрачности, ни границ, ни плейсхолдеров.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ui_audit  # noqa: E402

CHROME = Path(ui_audit.CHROME)
pytestmark = pytest.mark.skipif(
    not CHROME.exists(), reason=f'нет Chromium по пути {CHROME}: замер контраста '
                                'снимается только с живого браузера')

FIXTURE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
html, body { margin:0; padding:0; background:#ffffff; font:16px/1.4 sans-serif; }
.ok    { color:#111111; }                 /* 18.9:1 — не нарушение */
.bad   { color:#dcdcdc; }                 /* 1.37:1 */
.panel { background:rgba(0,0,0,0.8); }    /* фактический фон под текстом — 51,51,51 */
.inner { color:#777777; }                 /* 2.82:1, а не 4.69:1 «по чёрному» */
.field { border:1px solid #eeeeee; background:#ffffff; color:#111111; }  /* 1.16:1 */
.btn   { border:3px solid #888888; background:#ffffff; color:#111111; }  /* 3.55:1 — норма */
.hint::placeholder { color:#b0b0b0; }     /* 2.17:1 */
.score-cell { border:1px solid #f0f0f0; background:#ffffff; color:#111111; }  /* 1.09:1 */
.grad  { background:linear-gradient(#ffffff,#000000); color:#666666; }
.dark  { background:#1a1a1a; }
.card  { background:#ffffff; opacity:0.5; color:#555555; }  /* группа .5: фон 140, текст 55 */
.shadowed { border:0; background:#ffffff; color:#111111; box-shadow:inset 0 0 0 1px #eeeeee; }
.composer { border:0; background:#fbf8f2; color:#111111; }  /* граница-заливка 1.05:1 */
.deco  { border:1px solid #f0f0f0; }      /* role=presentation — не контроль */
.off:disabled { color:#eeeeee; background:#fafafa; }
</style></head><body>
<div class="ok">хороший текст</div>
<div class="bad">очень бледный текст</div>
<div class="panel"><span class="inner">текст на полупрозрачной панели</span></div>
<input class="field" placeholder="поле">
<button class="btn">кнопка</button>
<input class="hint" placeholder="подсказка поля">
<div class="score-cell">ячейка листа</div>
<div class="grad">на градиенте</div>
<div class="dark"><div class="card">текст в полупрозрачной карточке</div></div>
<input class="shadowed" value="с тенью">
<textarea class="composer">композер</textarea>
<div class="deco" role="presentation">декоративная рамка</div>
<button class="off" disabled>недоступная кнопка</button>
</body></html>"""

# Холст страницы: html и body прозрачные, фон — отдельным fixed-слоем;
# из дерева такое не посчитать, и это должно быть пропуском, а не «белым».
NO_CANVAS = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
html, body { background:transparent; margin:0; font:16px/1.4 sans-serif; }
.backdrop { position:fixed; inset:0; background:#141414; }
.content { position:relative; color:#cccccc; }
</style></head><body>
<div class="backdrop"></div><div class="content">текст на отдельной подложке</div>
</body></html>"""


@pytest.fixture(scope='module')
def browser(tmp_path_factory):
    work = tmp_path_factory.mktemp('n5-contrast')
    chrome, port = ui_audit.start_chrome(work / 'chrome-profile')
    cdp = ui_audit.CDP(ui_audit.page_target(port))
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    cdp.call('Emulation.setDeviceMetricsOverride', width=900, height=700,
             deviceScaleFactor=1, mobile=False)
    state = {'n': 0}

    def measure(html: str) -> dict:
        state['n'] += 1
        page = work / f'fixture{state["n"]}.html'
        page.write_text(html, encoding='utf-8')
        cdp.call('Page.navigate', url=page.as_uri())
        time.sleep(0.8)
        return cdp.eval(ui_audit.MEASURE_JS) or {}

    yield measure
    try:
        cdp.close()
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()


@pytest.fixture(scope='module')
def measure(browser):
    data = browser(FIXTURE)
    assert data, 'замер не вернул данных'
    return data


def _find(items, needle):
    for item in items:
        if needle in item.get('sel', ''):
            return item
    return None


def test_bad_text_is_found(measure):
    hit = _find(measure['contrastBad'], 'div.bad')
    assert hit is not None, 'текст 1.37:1 не найден: ' + str(measure['contrastBad'])
    assert hit['ratio'] == pytest.approx(1.37, abs=0.05)
    assert hit['kind'] == 'текст'
    assert measure['contrastMin'] == pytest.approx(min(t['ratio'] for t in measure['contrastBad']))


def test_good_text_is_not_reported(measure):
    assert _find(measure['contrastBad'], 'div.ok') is None


def test_transparent_background_is_composited(measure):
    """rgba(0,0,0,.8) на белом — фон 51,51,51, а не чёрный."""
    hit = _find(measure['contrastBad'], 'span.inner')
    assert hit is not None, 'текст на полупрозрачной панели не найден'
    assert hit['ratio'] == pytest.approx(2.82, abs=0.05)
    assert hit['bg'] == 'rgb(51,51,51)'


def test_opacity_group_is_composited(measure):
    """opacity .5 гасит и фон, и текст: 3.53:1 по пикселям, а не 7.5:1 «по белому»."""
    hit = _find(measure['contrastBad'], 'div.card')
    assert hit is not None, 'текст в карточке opacity .5 не найден: ' + str(measure['contrastBad'])
    assert hit['bg'] == 'rgb(141,141,141)'
    assert hit['fg'] == 'rgb(56,56,56)'
    assert hit['ratio'] == pytest.approx(3.53, abs=0.1)


def test_control_borders_are_measured(measure):
    """WCAG 1.4.11: рамка поля 1.16:1 — нарушение, рамка кнопки 3.55:1 — норма."""
    field = _find(measure['borderBad'], 'input.field')
    assert field is not None, 'граница поля 1.16:1 не найдена: ' + str(measure['borderBad'])
    assert field['ratio'] == pytest.approx(1.16, abs=0.05)
    assert field['kind'] == 'граница'
    assert _find(measure['borderBad'], 'button.btn') is None, '3.55:1 — выше порога 3:1'
    cell = _find(measure['borderBad'], 'div.score-cell')
    assert cell is not None, 'граница ячейки листа 1.09:1 не найдена'
    assert cell['kind'] == 'ячейка'
    assert measure['borderMin'] == pytest.approx(min(t['ratio'] for t in measure['borderBad']))
    assert measure['controlChecked'] >= 3 and measure['cellChecked'] >= 1


def test_border_without_border_property(measure):
    """Рамка через inset-тень и граница-заливка — тоже граница контрола."""
    shadow = _find(measure['borderBad'], 'input.shadowed')
    assert shadow is not None, 'рамка через inset-тень не найдена: ' + str(measure['borderBad'])
    assert shadow['what'] == 'внутренняя-тень'
    composer = _find(measure['borderBad'], 'textarea.composer')
    assert composer is not None, 'граница-заливка композера 1.05:1 не найдена'
    assert composer['what'] == 'заливка'


def test_decorative_role_is_not_a_control(measure):
    assert _find(measure['borderBad'], 'div.deco') is None
    assert all('div.deco' not in t['sel'] for t in measure['borderBad'])


def test_inactive_control_is_skipped_not_counted(measure):
    assert _find(measure['contrastBad'], 'button.off') is None
    skipped = measure['contrastSkipped']
    assert any('button.off' in s['sel'] and s['reason'] == 'неактивный-элемент'
               for s in skipped), skipped


def test_placeholder_is_measured(measure):
    assert measure['placeholderChecked'] >= 1
    hit = _find(measure['contrastBad'], 'input.hint')
    assert hit is not None, 'плейсхолдер 2.17:1 не найден: ' + str(measure['contrastBad'])
    assert hit['kind'] == 'плейсхолдер'
    assert hit['ratio'] == pytest.approx(2.17, abs=0.05)


def test_unmeasurable_is_reported_not_zeroed(measure):
    """Градиент посчитать нельзя — он в `contrastSkipped`, а не проглочен."""
    hit = _find(measure['contrastSkipped'], 'div.grad')
    assert hit is not None, 'градиент не попал в contrastSkipped: ' + str(measure['contrastSkipped'])
    assert hit['reason'] == 'фон-картинка'
    assert measure['contrastSkippedCount'] >= 1


def test_unknown_canvas_is_a_skip_not_white(browser):
    """Подложка отдельным fixed-слоем: не «белый по умолчанию», а пропуск."""
    data = browser(NO_CANVAS)
    assert _find(data['contrastBad'], 'div.content') is None
    reasons = [s['reason'] for s in data['contrastSkipped']]
    assert 'фон-страницы-не-определён' in reasons, reasons


def test_report_prints_border_line(measure):
    row = dict(measure)
    row.update({'state': 'home', 'theme': 'light', 'width': 1440, 'height': 900})
    report = ui_audit.build_report([row], {'commit': 'test', 'frames': 1, 'base': 'http://x',
                                           'data_dir': '/tmp', 'tag': 'n5'})
    assert 'границ < 3:1:' in report
    assert '| границ < 3:1 |' in report
    assert 'контролов' in report and 'ячеек листа' in report
    assert 'contrastSkipped' in report


def test_report_on_old_measures_says_not_measured():
    """Старый measures без новых полей не должен выдавать «0 из 0» за измерение."""
    row = {'state': 'home', 'theme': 'light', 'width': 1440, 'height': 900,
           'overflowX': 0, 'fontMin': 14, 'tapsBadCount': 0, 'tapCount': 3, 'tapsBad': [],
           'contrastBadCount': 0, 'contrastMin': None, 'contrastBad': [],
           'interactiveVisible': 3, 'fontDistinct': 2, 'clippedCount': 0, 'clipped': []}
    report = ui_audit.build_report([row], {'commit': 'old', 'frames': 1, 'base': 'http://x',
                                           'data_dir': '/tmp', 'tag': 'old'})
    assert 'не снимались' in report


def test_failed_frame_is_not_counted_as_measured():
    """Упавший кадр не измерен: «0 px во всех N» не имеет права его считать.

    n16: `prep-log-light-1440` упал по таймауту, а строка отчёта говорила
    «0 px во всех 40 кадрах» — тот же пустой ноль, только про перелив.
    """
    good = {'state': 'home', 'theme': 'light', 'width': 1440, 'height': 900,
            'overflowX': 0, 'scrollWidth': 1440, 'fontMin': 14, 'tapsBadCount': 0,
            'tapCount': 3, 'tapsBad': [], 'contrastBadCount': 0, 'contrastBad': [],
            'contrastMin': None, 'interactiveVisible': 3, 'fontDistinct': 2,
            'clippedCount': 0, 'clipped': []}
    failed = {'state': 'prep-log', 'theme': 'light', 'width': 1440,
              'error': 'ready timeout (две навигации)'}
    report = ui_audit.build_report([good, failed], {'commit': 'test', 'frames': 1,
                                                    'base': 'http://x', 'data_dir': '/tmp',
                                                    'tag': 'n5'})
    assert 'во всех 1 снятых кадрах' in report, report
    assert 'не снято 1' in report
    assert 'во всех 2 кадрах' not in report
