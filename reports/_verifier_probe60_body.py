
import os
os.environ['SOLO_TEST']='1'
import sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, r"C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI")
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
