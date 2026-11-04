
import os, sys, subprocess, tempfile, shutil
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
tmp = Path(tempfile.mkdtemp())
shutil.copytree(ROOT/'solo', tmp/'solo')

# Reproduce _worker's invocation EXACTLY: cwd=ROOT(copy), env inherits PYTHONPATH pointing at SOURCE
worker = "import sys, json; import solo.nat20_content as nc; print(json.dumps({'solo': __import__('solo').__file__, 'root': str(nc.ROOT)}))"
env = dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1')
env['PYTHONPATH'] = str(ROOT)          # <-- the source folder, inherited
r = subprocess.run([sys.executable,'-X','utf8','-B','-c',worker], capture_output=True, text=True, env=env, cwd=str(tmp))
print('cwd=copy, PYTHONPATH=SOURCE  ->', r.stdout.strip(), r.stderr.strip()[-300:])

env2 = dict(os.environ, SOLO_TEST='1', PYTHONDONTWRITEBYTECODE='1')
env2.pop('PYTHONPATH', None)
r2 = subprocess.run([sys.executable,'-X','utf8','-B','-c',worker], capture_output=True, text=True, env=env2, cwd=str(tmp))
print('cwd=copy, PYTHONPATH cleared ->', r2.stdout.strip(), r2.stderr.strip()[-300:])
print()
print('KEY: with PYTHONPATH=SOURCE the worker imports solo/ from the ORIGINAL folder,')
print('not from the copy, even though cwd is the copy. _worker (game.py side) inherits it.')
