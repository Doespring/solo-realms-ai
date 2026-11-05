
import os, re, subprocess
from pathlib import Path
os.environ['SOLO_TEST']='1'
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")

scenario = r'''
import os
os.environ['SOLO_TEST']='1'
import sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, r"ROOTDIR")
from scripts.command_smoke import FixedDice, setup
from solo import game as G
from solo.world_service import ensure

tmp = Path(tempfile.mkdtemp())
store, campaign, game = setup(tmp)
with store.connection(True) as c:
    cur = store.campaign(campaign, c)
    def transition(state):
        ensure(state)
        return [dict(type='scripted', kind='world', text='Осмотрелся: ничего не изменилось.')]
    store.engine_event(c, cur, 'req-world-1', 'Мир: observe', transition, 'sig-1')

def play(rid, text):
    v = store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, rid, text, v, rng=FixedDice()))

a = play('req-world-1', 'Мир: observe')
print('immediate replay -> moved_on =', a.get('moved_on'))
b = play('req-walk2', 'Исследую следы')
print('second turn      ->', b.get('status'))
c2 = play('req-world-1', 'Мир: observe')
print('later replay     -> moved_on =', c2.get('moved_on'), '| replayed =', c2.get('replayed'))
'''.replace('ROOTDIR', str(ROOT))

p = ROOT/'solo'/'game.py'
orig = p.read_bytes()
text = orig.decode('utf-8')
nl = '\r\n' if '\r\n' in text else '\n'
pat = re.compile(r"def _moved_on\(event, current\):(.*?)(?=\n\S)", re.S)
new_fn = ("def _moved_on(event, current):\n"
          "    import json as _json\n"
          "    try:\n"
          "        return _json.loads(event['after_state'] or '{}') != current.get('state')\n"
          "    except (ValueError, TypeError):\n"
          "        return False\n").replace('\n', nl)

env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT))
PY = str(ROOT/'.venv'/'Scripts'/'python.exe')
sc = ROOT/'reports'/'_verifier_probe60_body.py'
sc.write_text(scenario, encoding='utf-8')

print('=== CURRENT code ===')
r = subprocess.run([PY,'-X','utf8','-B',str(sc)], capture_output=True, text=True, env=env, cwd=str(ROOT))
print(r.stdout.strip()); print('ERR', r.stderr[-500:] if r.returncode else '')
try:
    p.write_bytes(pat.sub(new_fn, text, count=1).encode('utf-8'))
    print()
    print('=== OLD _moved_on (mutated) ===')
    r = subprocess.run([PY,'-X','utf8','-B',str(sc)], capture_output=True, text=True, env=env, cwd=str(ROOT))
    print(r.stdout.strip()); print('ERR', r.stderr[-500:] if r.returncode else '')
finally:
    p.write_bytes(orig)
print('\nrestored:', p.read_bytes()==orig)
