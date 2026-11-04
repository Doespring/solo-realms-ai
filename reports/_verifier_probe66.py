
import os, sys, tempfile, shutil
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
tmp = Path(tempfile.mkdtemp())
shutil.copytree(ROOT/'solo', tmp/'solo')

# Make the copy's solo distinguishable from the original's.
cp = tmp/'solo'/'__init__.py'
cp.write_text(cp.read_text(encoding='utf-8') + "\nCOPY_MARK = 'I am the copy'\n", encoding='utf-8')

def run(cwd, pythonpath, mode):
    code = ("import json,solo;"
            "print(json.dumps({'where': solo.__file__, "
            "mark: getattr(solo,'COPY_MARK',None), 'path0': __import__('sys').path[0]}))")
    if mode == 'c':
        argv = [sys.executable,'-X','utf8','-B','-c',code]
    else:
        argv = [sys.executable,'-X','utf8','-B','-m','solo.nat20_worker']  # real call shape
        code = None
    env = dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1')
    if pythonpath is None: env.pop('PYTHONPATH', None)
    else: env['PYTHONPATH'] = pythonpath
    import subprocess
    if mode == 'c':
        r = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(cwd))
        return r.stdout.strip() or ('ERR:'+r.stderr.strip()[-160:])
    return '(-m native worker not run here)'

print('A) cwd=copy, PYTHONPATH=SOURCE :', run(tmp, str(ROOT), 'c'))
print('B) cwd=copy, PYTHONPATH=None   :', run(tmp, None, 'c'))
print('C) cwd=SOURCE, PYTHONPATH=SOURCE:', run(ROOT, str(ROOT), 'c'))
print()
print('=> For -c, sys.path[0] is cwd. For -m, sys.path[0] is cwd too.')
print('   So the worker in a copy uses the copy if cwd=ROOT(copy). Check ROOT origin:')
print('   ROOT comes from nat20_content.py:15 = Path(__file__).resolve().parents[1]')
print('   => in a copy __file__ is the copy => correct. PYTHONPATH only matters if cwd is wrong.')
