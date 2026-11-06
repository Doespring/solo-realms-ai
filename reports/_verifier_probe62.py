
import os, sys, subprocess, tempfile, shutil
from pathlib import Path
os.environ['SOLO_TEST']='1'
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")

# Simulate a copy: copy only solo/ + web/ into a temp dir, NO .venv there.
tmp = Path(tempfile.mkdtemp())
for name in ('solo','web'):
    shutil.copytree(ROOT/name, tmp/name)
print('copy at', tmp)
print('has .venv?', (tmp/'.venv').exists())

env = dict(os.environ)
env.pop('PYTHONPATH', None)   # a clean copy would not inherit the original PYTHONPATH
env['SOLO_TEST']='1'
code = ("import sys; sys.path.insert(0, r'%s');"
        "import dnd5e_engine, solo.nat20_content as nc;"
        "print('cwd      :', __import__('os').getcwd());"
        "print('ROOT     :', nc.ROOT);"
        "print('dnd5e    :', dnd5e_engine.__file__)") % str(tmp)
r = subprocess.run([sys.executable, '-X','utf8','-B','-c',code], capture_output=True, text=True,
                   env=env, cwd=str(tmp))
print('--- run with cwd=copy, PYTHONPATH cleared ---')
print(r.stdout.strip())
print('ERR:', r.stderr.strip()[-500:])
