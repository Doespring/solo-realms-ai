
import os
os.environ['SOLO_TEST']='1'
import sys
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from solo import game as G

print("CORRECTION: my earlier NOTE was wrong. Redo the None case against a NON-empty state.")
cur = {'state': {'location': 'Таверна', 'turn': 3}}
for name, ev in [('after_state None', {'after_state': None}),
                 ("after_state ''", {'after_state': ''}),
                 ('after_state {}', {'after_state': {}}),
                 ('after_state == current', {'after_state': {'location': 'Таверна', 'turn': 3}}),
                 ('event missing key', {}),
                 ('event is None', None)]:
    after = G._state_after(ev)
    print(f'{name:24} _state_after={str(after):45} moved={G._moved_on(ev, cur)}')

print()
print('SO: when the recorded state is None/"" and the campaign state is non-empty,')
print('_state_after returns {} (the "or" default), and {} != real state -> moved=True.')
print('That is a GUESS dressed as knowledge, not an error.')

print()
print('=== reachability: can after_state be None in a row the replay actually reads? ===')
print('decode_event (storage.py:253-255) does json.loads; column is TEXT NOT NULL.')
print('json.loads("null") ->', json.loads('null') if False else None)
import json
print('json.loads("null") ->', repr(json.loads('null')))
print('Would raise in the two branches where after_state is passed to dict()/stored_command:')
print('  game.py:576 command = stored_command(event[after_state], ...)')
print('  game.py:597 dict(state=event[after_state])')
print('=> a row with after_state=null cannot even reach _moved_on: it crashes earlier.')
