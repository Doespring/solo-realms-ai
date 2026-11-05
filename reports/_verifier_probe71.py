
import subprocess, sys, os
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
orig = T.read_bytes()          # BYTES: no newline translation

def run():
    env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    r = subprocess.run([sys.executable,'-X','utf8','-B','-m','pytest',
        'tests/test_copy_does_not_reach_back.py','-q','-p','no:cacheprovider'],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    lines=[l for l in r.stdout.strip().splitlines() if l.strip()]
    return lines[-1] if lines else r.stdout[-200:]

M = {
 'baseline            ': b'',
 'A sys.path.insert   ': b"\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n",
 'B sys.path.append   ': b"\nimport sys as _s\n_s.path.append(r'C:/elsewhere')\n",
 'C .venv literal     ': b"\n_H = '.venv'\n",
 'D site-packages     ': b"\n_H = 'site-packages'\n",
 'E BYPASS path[0:0]= ': b"\nimport sys as _s\n_s.path[0:0] = [r'C:/elsewhere']\n",
 'F BYPASS path=+     ': b"\nimport sys as _s\n_s.path = [r'C:/elsewhere'] + _s.path\n",
 'G BYPASS extend     ': b"\nimport sys as _s\n_s.path.extend([r'C:/elsewhere'])\n",
 'H BYPASS intern     ': b"\n_H = '.ve' + 'nv'\n",
 'I BYPASS abs split  ': b"\n_P = 'C:' + '/Users/' + 'x'\n",
 'J BYPASS ~ home     ': b"\n_P = '~/SoloRealms'\n",
}
for name, body in M.items():
    try:
        T.write_bytes(orig + body)
        print(f'{name} -> {run()}')
    finally:
        T.write_bytes(orig)
print()
print('restored byte-identical:', T.read_bytes() == orig)
