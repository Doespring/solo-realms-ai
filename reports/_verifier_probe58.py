
import os
os.environ['SOLO_TEST']='1'
import sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from scripts.command_smoke import FixedDice, setup
from solo import game as G

calls = []
orig = G._moved_on
def spy(event, current):
    r = orig(event, current)
    stored = (event.get('results') or [{}])[0]
    calls.append(dict(game_command=bool(stored.get('game_command')),
                      has_text=bool(stored.get('text')), moved=r,
                      as_type=type(event.get('after_state')).__name__))
    return r

def play(game, campaign, rid, text):
    v = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, rid, text, v, rng=FixedDice()))

tmp = Path(tempfile.mkdtemp())
store, campaign, game = setup(tmp)
print('campaign has world in state:', 'world' in store.campaign(campaign)['state'])

# Find a "world"-flavoured verb that goes through WorldService, not mechanics.
for text in ['Иду в таверну', 'Осматриваюсь', 'Жду']:
    calls.clear(); G._moved_on = spy
    try:
        r = play(game, campaign, 'rid-'+str(abs(hash(text))), text)
        print(f'{text!r:22} status={r["status"]:12} moved={r.get("moved_on")} hints={calls}')
    except Exception as e:
        print(f'{text!r:22} EXC {type(e).__name__}: {e}')
G._moved_on = orig
