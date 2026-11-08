
import os, sys, subprocess, tempfile, shutil
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
venv = ROOT/'.venv'

print('=== what makes the venv findable? ===')
for f in ['pyvenv.cfg', 'Lib/site-packages/dnd5e_engine']:
    print(f, '->', (venv/f).exists())
print()
print('pyvenv.cfg:'); print((venv/'pyvenv.cfg').read_text()[:300] if (venv/'pyvenv.cfg').exists() else 'none')
print()
print('=== .pth files in site-packages (these add paths at interpreter start) ===')
sp = venv/'Lib'/'site-packages'
for p in sorted(sp.glob('*.pth')):
    t = p.read_text(errors='replace').strip()
    if 'import' in t or 'SoloRealms' in t or 'site' in t.lower():
        print(' ', p.name, '->', t[:200].replace(chr(10),' | '))
print()
print('=== is dnd5e_engine an editable/develop install pointing at SOURCE? ===')
d = sp/'dnd5e_engine'
print('dnd5e_engine dir exists:', d.exists())
if d.exists(): print('  files:', [x.name for x in list(d.iterdir())[:10]])
for acc in sorted(sp.glob('*dnd5e*')) + sorted(sp.glob('*nat20*')) + sorted(sp.glob('*solo*')):
    print(' ', acc.name, '(dir)' if acc.is_dir() else '(file)')
