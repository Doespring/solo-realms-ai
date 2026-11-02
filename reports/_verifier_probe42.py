
import os, sys, tempfile, shutil
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
from solo.build_identity import build_identity, _ui_files

print("ui files frozen at start:", len(_ui_files()))
web = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")/'web'
victim = web/'obsidian.css'
backup = Path(tempfile.mkdtemp())/'obsidian.css'
shutil.copy2(victim, backup)
# warm the cache once
print("first refresh ok:", build_identity(refresh=True)['ui_drift'])
victim.unlink()
try:
    print("after deleting a counted file, refresh raises:", end=' ')
    try:
        print(build_identity(refresh=True)['ui_drift'])
    except Exception as e:
        print(type(e).__name__ + ":", e)
finally:
    shutil.copy2(backup, victim)
print("restored, refresh:", build_identity(refresh=True)['ui_drift'])
