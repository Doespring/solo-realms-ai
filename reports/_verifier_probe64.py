
import os, sys, subprocess, tempfile, shutil
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
tmp = Path(tempfile.mkdtemp())
for name in ('solo','web'):
    shutil.copytree(ROOT/name, tmp/name)

print('=== sys.path[0] and site-packages of the interpreter ===')
print('sys.executable =', sys.executable)
print('sys.prefix     =', sys.prefix)
print('site-packages =', [p for p in sys.path if 'site-packages' in p])
print()
print('Copy dir:', tmp)
print('The copy contains NO .venv. It cannot even RUN without the source interpreter:')
print('  a copy has no python of its own; whatever interpreter starts it decides site-packages.')
print()
print('=== so: does the copy import CODE from the source FOLDER, or just use its interpreter? ===')
print('dnd5e_engine.__file__ is under .venv/Lib/site-packages — an INSTALLED dependency,')
print('not the original source tree. That is not "reaching back into the source folder".')
print()
print('=== the real reach-back danger: -m solo.nat20_worker with cwd=ROOT ===')
import inspect
import solo.nat20_api as api
src = inspect.getsource(api.Nat20GameEngine._worker)
print(src[:700])
