"""r230: measure every screen at phone widths, then screenshot it.

Used twice: `--phase before` (base r193 layout) and `--phase after` (fixed CSS).
Measurements: JSON per pass. Screenshots: reports/r230/<screen>-<theme>-<width>.png
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, '/home/hermes/.hermes/kanban/workspaces/t_749e419f')
import cdp as C

APP = '/home/hermes/projects/SoloRealmsAI-app'
WORKSPACE = '/home/hermes/.hermes/kanban/workspaces/t_749e419f'
MEASURE = open(os.path.join(WORKSPACE, 'measure.js'), encoding='utf-8').read()

SCREENS = ['welcome', 'play', 'character', 'skills', 'inventory', 'world', 'journal',
           'adventure-handouts', 'saves', 'rulesets', 'master', 'campaigns', 'settings',
           'cloud', 'costs', 'performance', 'adventure-library', 'master-library']

CLICK = """(() => {
  const b = document.querySelector('[data-view="%s"]');
  if (!b) return 'missing';
  if (b.disabled) return 'disabled';
  b.click();
  return 'ok';
})()"""


def set_theme(c, name):
    c.eval("""(() => {
      const s = document.getElementById('theme');
      if (s) { s.value = %r; s.dispatchEvent(new Event('change', {bubbles: true})); }
      document.documentElement.dataset.theme = %r;
      localStorage.setItem('solo-theme', %r);
      return document.documentElement.dataset.theme;
    })()""" % (name, name, name))
    time.sleep(0.4)


def open_campaign(c):
    c.eval("""(() => { const b = document.querySelector('[data-view="campaigns"]'); if (b) b.click(); return 1; })()""")
    time.sleep(1.5)
    picked = c.eval("""(() => {
      const card = document.querySelector('#campaign-list article button');
      if (!card) return 'no-campaign';
      card.click();
      return 'ok';
    })()""")
    if picked == 'ok':
        C.wait_for(c, "document.querySelector('.view.active') && document.querySelector('.view.active').id === 'play'", timeout=40)
        C.wait_for(c, "typeof current !== 'undefined' && current && current.id === campaignId", timeout=40)
        time.sleep(3.0)
    return picked


def open_app(c, w, h, theme):
    c.viewport(w, h, 2)
    if not (c.eval("location.protocol") or '').startswith('http'):
        c.goto('http://127.0.0.1:19346/', wait=2.5)
    else:
        c.reload(wait=2.5)
    picked = open_campaign(c)
    set_theme(c, theme)
    c.eval("document.scrollingElement.scrollTop = 0")
    time.sleep(0.4)
    return picked


def goto_screen(c, screen, settle=1.3):
    def click():
        if screen == 'welcome':
            return c.eval("(() => { const b = document.getElementById('solo-home'); if (!b) return 'missing'; b.click(); return 'ok'; })()")
        if screen == 'drawer':
            return c.eval("(() => { const b = document.getElementById('solo-burger'); if (!b) return 'missing'; b.click(); return 'ok'; })()")
        return c.eval(CLICK % screen)

    nav = click()
    time.sleep(settle)
    if screen not in ('drawer',):
        try:
            C.wait_for(c, "document.querySelector('.view.active') && document.querySelector('.view.active').id === %r" % screen, timeout=20)
        except TimeoutError:
            # The game screen reloads its own state asynchronously; a click that
            # lands mid-render can be swallowed by the guard that sends campaign
            # screens back to the home screen. Reopen the campaign and retry once.
            open_campaign(c)
            time.sleep(1.0)
            nav = click()
            time.sleep(settle)
            try:
                C.wait_for(c, "document.querySelector('.view.active') && document.querySelector('.view.active').id === %r" % screen, timeout=20)
                nav = nav + '/retry'
            except TimeoutError:
                nav = nav + '/not-active'
    time.sleep(0.5)
    c.eval("document.scrollingElement.scrollTop = 0")
    time.sleep(0.2)
    return nav


def measure(c, screen):
    data = json.loads(c.eval(MEASURE, timeout=90))
    if screen == 'drawer':
        data['screen'] = 'drawer'
    return data


def close_drawer(c):
    c.eval("(() => { document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 1; })()")
    time.sleep(0.4)


def pass_screens(c, w, h, theme, phase, shots=False, screens=None):
    screens = screens or SCREENS
    picked = open_app(c, w, h, theme)
    rows = []
    for screen in screens + ['drawer']:
        nav = goto_screen(c, screen)
        data = measure(c, screen)
        data.update(nav=nav, phase=phase, width=w, height=h, theme=theme, campaign=picked)
        if screen == 'drawer':
            close_drawer(c)
        rows.append(data)
        if shots:
            out = os.path.join(APP, 'reports', 'r230', f'{screen}-{theme}-{w}.png')
            c.shot(out)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='before')
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--themes', default='obsidian,parchment')
    parser.add_argument('--widths', default='375,390')
    parser.add_argument('--screens', default='')
    args = parser.parse_args()

    heights = {375: 812, 390: 844}
    screens = [s for s in args.screens.split(',') if s] or None
    c = C.Chromium().start()
    try:
        for theme in args.themes.split(','):
            for width in [int(x) for x in args.widths.split(',')]:
                rows = pass_screens(c, width, heights[width], theme, args.phase,
                                    shots=args.shots, screens=screens)
                out = os.path.join(WORKSPACE, f'{args.phase}-{width}-{theme}.json')
                with open(out, 'w', encoding='utf-8') as fh:
                    json.dump(rows, fh, ensure_ascii=False, indent=1)
                print(json.dumps([(r['screen'], r['nav'], r['counts']['smallFontKinds'],
                                   r['counts']['smallTargets'], r['overflowCount'],
                                   r['counts']['lowContrast'], r['docOverflow']['scroll']) for r in rows],
                                 ensure_ascii=False))
                print('written', out, flush=True)
    finally:
        c.stop()


if __name__ == '__main__':
    main()
