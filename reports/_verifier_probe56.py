
import os
os.environ['SOLO_TEST']='1'
import sys, asyncio, tempfile
from pathlib import Path
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from scripts.command_smoke import FixedDice, setup
from solo import game as G

# Instrument: which branch answered, and what stored keys were present?
calls = []
orig = G._moved_on
def spy(event, current):
    r = orig(event, current)
    stored = (event.get('results') or [{}])[0]
    calls.append(dict(game_command=bool(stored.get('game_command')),
                      has_narrative=bool(event.get('narrative')),
                      has_text=bool(stored.get('text')), moved=r,
                      as_type=type(event.get('after_state')).__name__))
    return r
G._moved_on = spy

def play(game, campaign, rid, text):
    v = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, rid, text, v, rng=FixedDice()))

tmp = Path(tempfile.mkdtemp())
store, campaign, game = setup(tmp)
print('walk:', play(game, campaign, 'req-walk', 'Исследую следы')['status'])
print('fight:', play(game, campaign, 'req-fight', 'Добавляю базовый бой')['status'])
print('repeat:', play(game, campaign, 'req-walk', 'Исследую следы')['status'])
print()
print('branch(es) exercised by the new test:')
for c in calls: print('  ', c)
G._moved_on = orig
