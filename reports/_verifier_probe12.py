
import os, sys, json, time, tempfile, asyncio, inspect
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from solo.app import create_app
from solo.contracts import Hero
from test_dialogue_paging_api import MockLLM
tmp = Path(tempfile.mkdtemp())
app = create_app(tmp/'m.sqlite3', MockLLM())
game = app.state.game

src = inspect.getsource(type(game)._play)
i = src.index('operation_without(campaign)')
rest = src[i:]
import re
around = rest[:2200]
print("=== _play after operation_without ===")
for n, line in enumerate(around.splitlines()[:45]):
    print('   ', line)

print()
src2 = inspect.getsource(type(game).play_command)
j = src2.index('operation_without(campaign)')
print("=== play_command after operation_without ===")
for line in src2[j:j+1800].splitlines()[:30]:
    print('   ', line)
