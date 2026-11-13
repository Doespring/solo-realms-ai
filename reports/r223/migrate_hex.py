#!/usr/bin/env python3
"""r223: механическая замена `#hex` на токены в web/*.css.

Каждая замена объявлена с ожидаемым числом вхождений: если файл уехал вперёд
и строка не нашлась — скрипт падает, а не молча пропускает цвет.
"""
import sys
from pathlib import Path

WEB = Path('/home/hermes/projects/SoloRealmsAI-Nat20/web')

# file -> list of (old, new, expected_count)
EDITS = {
    'style.css': [
        (":root{color-scheme:dark;--bg:#101210;--surface:#181c19;--surface2:#1e2420;"
         "--text:#e2e6dc;--muted:#8e9b91;--line:#303b31;--gold:#d1ba82;"
         "--gold-dark:#2e3024;--input:#121613;--accent:#9baa88;"
         "--shadow:0 20px 70px #0005;font-family:'Segoe UI',sans-serif}",
         "/* r223: палитра переехала в design-tokens.css — здесь остаётся только шрифт. */\n"
         ":root{font-family:'Segoe UI',sans-serif}", 1),
        ("button.primary{background:var(--gold);color:#232619;",
         "button.primary{background:var(--gold);color:var(--on-gold);", 1),
        ("html[data-theme=parchment]{color-scheme:light;--bg:#eee8db;--surface:#f6f0e5;"
         "--surface2:#e8e0cf;--text:#3f4032;--muted:#7d7b68;--line:#d4cbbb;--gold:#917242;"
         "--gold-dark:#e4dbc7;--input:#fff9ee;--accent:#6b7754;--shadow:0 20px 70px #57452330}"
         "html[data-theme=parchment] button.primary{color:#fff9ee}"
         "html[data-theme=minimal]{color-scheme:light;--bg:#f6f7f8;--surface:#fff;"
         "--surface2:#eef0f2;--text:#243136;--muted:#7b878c;--line:#dde3e6;--gold:#4a6b70;"
         "--gold-dark:#e8eff0;--input:#f6f7f8;--accent:#577877;--shadow:0 20px 70px #20383f22}"
         "html[data-theme=minimal] button.primary{color:white}",
         "/* r223: темы parchment и minimal удалены — в языке «Стол и лист» их две: dark и light.\n"
         "   Значения светлой темы живут в design-tokens.css на [data-theme=\"light\"]. */", 1),
    ],
    'themes.css': [
        (".sidebar{background:#1d1f1c;color:#d9c9a6;--muted:#a3b0a6}",
         ".sidebar{background:var(--ink-900);color:var(--text-2);--muted:var(--text-3)}", 1),
        (".sidebar .brand{color:#efe1c0}", ".sidebar .brand{color:var(--text)}", 1),
        (".sidebar .nav,.sidebar summary,.sidebar small{color:#d9c9a6}",
         ".sidebar .nav,.sidebar summary,.sidebar small{color:var(--text-2)}", 1),
        ("html[data-theme=obsidian] .sidebar{--muted:#a3a094}\n"
         "html[data-theme=dark] .sidebar{--muted:#a3b0a6}\n"
         "html[data-theme=parchment] .sidebar{--muted:#a9a491}\n"
         "html[data-theme=minimal] .sidebar{--muted:#a6b0b5}\n", "", 1),
        (".sidebar .nav:hover,.sidebar .nav.active{color:#f6edda;background:#ffffff0c;"
         "border-color:#806845}",
         ".sidebar .nav:hover,.sidebar .nav.active{color:var(--text);"
         "background:color-mix(in srgb,var(--text) 6%,transparent);"
         "border-color:var(--line-strong)}", 1),
        (".sidebar .nav:focus-visible,.sidebar summary:focus-visible{outline:2px solid #d1ba82;",
         ".sidebar .nav:focus-visible,.sidebar summary:focus-visible{outline:2px solid var(--candle);", 1),
        (".sidebar button:not(.nav):not(.quartz):not(.primary){background:#1a1c19;"
         "border-color:#3a3f36;color:#d9c9a6}",
         ".sidebar button:not(.nav):not(.quartz):not(.primary){background:var(--ink-850);"
         "border-color:var(--line);color:var(--text-2)}", 1),
        (".sidebar button:not(.nav):not(.primary):hover{background:#262a23;border-color:#806845;"
         "color:#f6edda}",
         ".sidebar button:not(.nav):not(.primary):hover{background:var(--ink-800);"
         "border-color:var(--candle);color:var(--text)}", 1),
        (".sidebar button:not(.nav):not(.quartz):not(.primary):focus-visible{outline:2px solid #d1ba82;",
         ".sidebar button:not(.nav):not(.quartz):not(.primary):focus-visible{outline:2px solid var(--candle);", 1),
        (".sidebar input[type=checkbox],.sidebar input[type=radio]{accent-color:#d1ba82}",
         ".sidebar input[type=checkbox],.sidebar input[type=radio]{accent-color:var(--candle)}", 1),
        (".sidebar :focus-visible{outline-color:#d1ba82}",
         ".sidebar :focus-visible{outline-color:var(--candle)}", 1),
        (".scene-rail button:not(.nav):not(.quartz):not(.primary){background:#1a1c19;"
         "border-color:#3a3f36;color:#d9c9a6}",
         ".scene-rail button:not(.nav):not(.quartz):not(.primary){background:var(--ink-850);"
         "border-color:var(--line);color:var(--text-2)}", 1),
        (".scene-rail button:not(.nav):not(.primary):hover{background:#262a23;"
         "border-color:#806845;color:#f6edda}",
         ".scene-rail button:not(.nav):not(.primary):hover{background:var(--ink-800);"
         "border-color:var(--candle);color:var(--text)}", 1),
        (".scene-rail button:not(.nav):not(.quartz):not(.primary):focus-visible{outline:2px solid #d1ba82;",
         ".scene-rail button:not(.nav):not(.quartz):not(.primary):focus-visible{outline:2px solid var(--candle);", 1),
        ("  color:var(--muted,#8d8676);background:transparent;border:0;\r\n"
         "  border-bottom:1px solid var(--line,#2a2d26);padding:5px 12px;",
         "  color:var(--text-3);background:transparent;border:0;\r\n"
         "  border-bottom:1px solid var(--line);padding:5px 12px;", 1),
    ],
    'game-screen.css': [
        ("scrollbar-color:#3A3128 transparent", "scrollbar-color:var(--ink-600) transparent", 2),
        ("background:#2E2720", "background:var(--ink-700)", 1),
        ("color:#DCD0B8", "color:var(--sheet-ink)", 1),
        ("border-color:#5C4C39", "border-color:var(--line-strong)", 2),
        ("color:#191207", "color:var(--candle-ink)", 2),
        ("fill:#221C15", "fill:var(--sheet-bg-2)", 1),
        ("stroke:#C4AF8C", "stroke:var(--roll-num)", 1),
        ("fill:#F4EAD6", "fill:var(--text)", 1),
        ("color:#F0E5CF", "color:var(--text)", 1),
        ("color:#F4EAD6", "color:var(--text)", 1),
        ("border-color:#6B573C", "border-color:var(--line-strong)", 1),
        ("background:#14110DE8", "background:var(--ink-900)", 1),
        ("background:#131009", "background:var(--ink-900)", 1),
    ],
    'wizard.css': [
        ("background:#14110D", "background:var(--ink-900)", 2),
        ("background:#1B1711", "background:var(--roll-bg)", 2),
        ("background:#1D1812", "background:var(--roll-bg)", 2),
        ("#241E17", "var(--sheet-bg-2)", 1),
        ("#2A231B", "var(--ink-750)", 2),
    ],
    'design-table.css': [
        ("color:#191207", "color:var(--candle-ink)", 2),
        ("scrollbar-color:#3A3128 transparent", "scrollbar-color:var(--ink-600) transparent", 1),
        ("color:#170C07", "color:var(--out-miss-ink)", 1),
        ("color:#0F1408", "color:var(--out-hit-ink)", 1),
    ],
    'tabletop.css': [
        ("border-top:1px solid #ffffff20", "border-top:1px solid var(--line)", 1),
        ("border-top:1px solid #ffffff25", "border-top:1px solid var(--line)", 1),
        ("background:#ffffff08", "background:var(--surface2)", 1),
        ("border-color:#806845", "border-color:var(--line-strong)", 1),
        ("color:#d9c9a6", "color:var(--text-2)", 1),
        ("color:#efe1c0", "color:var(--text)", 1),
        ("background:#171a1eaa", "background:var(--overlay)", 1),
    ],
    'prototype.css': [
        ("color:#8c261b", "color:var(--sheet-bad)", 1),
        ("outline:2px solid #8c261b", "outline:2px solid var(--sheet-bad)", 1),
    ],
    'chronicles.css': [
        ("border:1px solid #9a815266", "border:1px solid var(--line)", 1),
        ("background:#fff3d22b", "background:var(--surface)", 1),
        ("background:#292329;color:#f7e8c3", "background:var(--surface2);color:var(--text)", 1),
    ],
    'creation.css': [
        ("background:var(--paper,#ecdfbd)", "background:var(--surface)", 1),
        ("border:2px dashed #9a8152", "border:2px dashed var(--gold)", 1),
        ("background:#fff3d226",
         "background:color-mix(in srgb,var(--gold) 10%,var(--surface))", 1),
        ("outline:3px solid #b68b45;background:#dac18a55",
         "outline:3px solid var(--gold);background:color-mix(in srgb,var(--gold) 30%,transparent)", 1),
        ("border-bottom:1px solid #97826366", "border-bottom:1px solid var(--line)", 1),
        ("border:1px solid #9a815266", "border:1px solid var(--line)", 1),
    ],
    'workspace.css': [
        ("--panel-shadow:0 6px 24px #00000010", "--panel-shadow:var(--shadow-soft)", 1),
    ],
}


def main() -> int:
    bad = 0
    for name, edits in EDITS.items():
        path = WEB / name
        text = path.read_text(encoding='utf-8', newline='')
        for old, new, expected in edits:
            found = text.count(old)
            if found != expected:
                print(f'FAIL {name}: ожидалось {expected}, найдено {found}: {old[:70]!r}')
                bad += 1
                continue
            text = text.replace(old, new, expected)
        path.write_text(text, encoding='utf-8', newline='')
    if bad:
        print(f'{bad} замен не применено — файлы записаны частично, проверь вывод')
        return 1
    print('все замены применены')
    return 0


if __name__ == '__main__':
    sys.exit(main())
