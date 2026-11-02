
import subprocess, sys, os
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
orig = T.read_text(encoding='utf-8')

body = "\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n"
T.write_text(orig + body, encoding='utf-8')
print('after write: contains sys.path.insert ->', 'sys.path.insert' in T.read_text(encoding='utf-8'))
print('size grew:', len(orig), '->', len(T.read_text(encoding='utf-8')))

# run the ONE test, verbose, so we see if it is even collected/run
env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT))
r = subprocess.run([sys.executable,'-X','utf8','-B','-m','pytest',
    'tests/test_copy_does_not_reach_back.py::test_the_package_does_not_edit_sys_path',
    '-q','-p','no:cacheprovider','-rA'],
    capture_output=True, text=True, cwd=str(ROOT), env=env)
print('--- stdout ---'); print(r.stdout[-1500:])
print('--- stderr ---'); print(r.stderr[-500:])
T.write_text(orig, encoding='utf-8')
print('restored:', T.read_text(encoding='utf-8') == orig)
