"""r230: measure the LIVE app (19215) at phone widths — read-only, no campaign opening."""
import json
import os
import sys
import time

sys.path.insert(0, '/home/hermes/.hermes/kanban/workspaces/t_749e419f')
import cdp as C

MEASURE = open('/home/hermes/.hermes/kanban/workspaces/t_749e419f/measure.js', encoding='utf-8').read()
PORT = int(os.environ.get("SOLO_PORT", "19215"))
SHOTS = os.environ.get("SOLO_SHOTS") == "1"
SHOT_DIR = os.environ.get("SOLO_SHOT_DIR", "/home/hermes/projects/SoloRealmsAI-Nat20/reports/r230")
SCREENS = ['play', 'journal', 'campaigns', 'saves', 'master', 'cloud', 'settings',
           'character', 'inventory', 'skills', 'world', 'rulesets', 'costs',
           'performance', 'adventure-library', 'master-library', 'adventure-handouts']


def set_theme(c, theme):
    c.eval("(() => { document.documentElement.dataset.theme='%s'; const s=document.getElementById('theme'); if(s){s.value='%s';s.dispatchEvent(new Event('change',{bubbles:true}));} localStorage.setItem('solo-theme','%s'); return 1; })()" % (theme, theme, theme))
    time.sleep(0.3)


def main():
    theme = sys.argv[1] if len(sys.argv) > 1 else 'obsidian'
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 375
    height = 812 if width == 375 else 844
    c = C.Chromium().start()
    rows = []
    try:
        c.viewport(width, height, 2)
        c.goto('http://127.0.0.1:%d/' % PORT, wait=3.0)
        set_theme(c, theme)
        active = c.eval("(() => { const a=document.querySelector('section.view.active'); return a ? a.id : null; })()")
        rows.append(dict(json.loads(c.eval(MEASURE, timeout=90)), screen='default:' + str(active),
                         width=width, theme=theme))
        if SHOTS:
            c.shot(os.path.join(SHOT_DIR, 'welcome-%s-%d.png' % (theme, width)))
        for screen in SCREENS:
            nav = c.eval('(() => { const b=document.querySelector(\'.nav[data-view="%s"]\'); if(!b) return "missing"; if(b.disabled) return "disabled"; b.click(); return "ok"; })()' % screen)
            time.sleep(1.4)
            shown = c.eval("(() => { const a=document.querySelector('section.view.active'); return a ? a.id : null; })()")
            data = dict(json.loads(c.eval(MEASURE, timeout=90)), screen=screen, shown=shown,
                        nav=nav, width=width, theme=theme)
            rows.append(data)
            if SHOTS:
                c.shot(os.path.join(SHOT_DIR, '%s-%s-%d.png' % (screen, theme, width)))
        # drawer / burger menu
        c.eval("(() => { const b=document.getElementById('mobile-burger') || document.getElementById('solo-burger'); if(b) b.click(); return 1; })()")
        time.sleep(0.8)
        rows.append(dict(json.loads(c.eval(MEASURE, timeout=90)), screen='drawer', shown='drawer',
                         nav='ok', width=width, theme=theme))
        if SHOTS:
            c.shot(os.path.join(SHOT_DIR, 'drawer-%s-%d.png' % (theme, width)))
    finally:
        c.stop()
    out = '/home/hermes/.hermes/kanban/workspaces/t_749e419f/live-%d-%s.json' % (width, theme)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    for r in rows:
        c_ = r.get('counts', {})
        print('%-22s %-14s fonts=%-3s kinds=%-2s tgt=%-3s gap=%-2s bleed=%-2s cont=%-2s lowC=%-2s docΔ=%-3s' % (
            r['screen'], r.get('shown'), c_.get('smallFonts', 0), c_.get('smallFontKinds', 0),
            c_.get('smallTargets', 0), c_.get('tightGaps', 0), r.get('overflowCount', 0),
            c_.get('contained', 0), c_.get('lowContrast', 0), (r.get('docOverflow') or {}).get('delta', 0)))
    print('written', out)


if __name__ == '__main__':
    main()
