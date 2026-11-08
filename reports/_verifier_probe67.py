
import subprocess, sys, os, shutil
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
TARGET = ROOT/'solo'/'chronicles.py'
orig = TARGET.read_text(encoding='utf-8')

def run():
    env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable,'-X','utf8','-B','-m','pytest',
        'tests/test_copy_does_not_reach_back.py','-q','-p','no:cacheprovider'],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    tail = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:]
    return tail[0] if tail else r.stdout[-200:]

MUTATIONS = {
 'baseline (no change)': None,
 'A sys.path.insert  ': "\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n",
 'B sys.path.append  ': "\nimport sys as _s\n_s.path.append(r'C:/elsewhere')\n",
 'C .venv literal    ': "\n_VENV_HINT = '.venv'\n",
 'D site-packages    ': "\n_SP_HINT = 'site-packages'\n",
 # bypasses the substrings: these do the same thing spelled differently
 'E BYPASS sys.path[0]= ': "\nimport sys as _s\n_s.path[0:0] = [r'C:/elsewhere']\n",
 'F BYPASS += path   ': "\nimport sys as _s\n_s.path = [r'C:/elsewhere'] + _s.path\n",
 'G BYPASS extend    ': "\nimport sys as _s\n_s.path.extend([r'C:/elsewhere'])\n",
 'H BYPASS venv split': "\n_HINT = '.' + 'venv'\n",
 'I BYPASS abs path  ': "\n_P = 'C:' + '/Users/' + 'someone/x'\n",
 'J BYPASS ~ home    ': "\n_P = '~/SoloRealms/data'\n",
}
for name, body in MUTATIONS.items():
    try:
        if body is None: TARGET.write_text(orig, encoding='utf-8')
        else: TARGET.write_text(orig + body, encoding='utf-8')
        print(f'{name:22s} -> {run()}')
    finally:
        TARGET.write_text(orig, encoding='utf-8')
print()
print('restored byte-identical:', TARGET.read_text(encoding='utf-8') == orig)
