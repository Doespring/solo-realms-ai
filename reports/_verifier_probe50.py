
import os
os.environ['SOLO_TEST']='1'
import json, sys
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from solo import game as G

cur = {'state': {'a': 1}, 'version': 3}

cases = [
  ('dict flat',            {'after_state': {'a': 1}}),
  ('dict other',           {'after_state': {'a': 2}}),
  ('None',                 {'after_state': None}),
  ('empty str',            {'after_state': ''}),
  ('json str flat',        {'after_state': '{"a": 1}'}),
  ('json str other',       {'after_state': '{"a": 2}'}),
  ('json str broken',      {'after_state': '{"a": 1'}),
  ('list',                 {'after_state': [1,2]}),
  ('json list str',        {'after_state': '[1,2]'}),
  ('nested state',         {'after_state': {'state': {'a': 1}}}),
  ('numbers',              {'after_state': 5}),
  ('truthy bad type',      {'after_state': True}),
  ('event None',           None),
  ('no key',               {}),
  ('empty dict {}',        {'after_state': {}}),
]

print('=== _state_after / _moved_on ===')
for name, ev in cases:
    try:
        after = G._state_after(ev)
        moved = G._moved_on(ev, cur)
        print(f'{name:18} after={str(after):28} moved={moved}')
    except Exception as e:
        print(f'{name:18} EXC {type(e).__name__}: {e}')

# What the CURRENT campaign state comparison does when the stored state is the
# real post-turn state (the honest case) -> must be False.
print()
print('=== OLD logic vs NEW on a realistic dict after_state ===')
ev = {'after_state': {'a': 1}}
try:
    old = json.loads(ev['after_state'] or '{}') != cur['state']
    old_out = old
except Exception as e:
    old_out = f'{type(e).__name__} -> caught -> False'
print('old:', old_out)
print('new:', G._moved_on(ev, cur))
