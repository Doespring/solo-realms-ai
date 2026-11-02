
import os
os.environ['SOLO_TEST']='1'
import sys
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from solo import game as G

print('=== _moved_on with current=None (would happen if campaign vanished) ===')
for ev in [{'after_state': {'a':1}}, {'after_state': None}]:
    try:
        print(ev, '->', G._moved_on(ev, None))
    except Exception as e:
        print(ev, '-> EXC', type(e).__name__, e)

print()
print('=== the None/'' == {} <=> False distinction ===')
cur = {'state': {}}
for name, ev in [('after_state None', {'after_state': None}), ("after_state ''", {'after_state': ''}),
                 ('after_state {} (real empty state)', {'after_state': {}})]:
    print(f'{name:36} _state_after={G._state_after(ev)}  _moved_on vs state={{}} = {G._moved_on(ev, cur)}')
print("NOTE: for a campaign whose real state is {} the honest answer is moved_on=False,")
print("but after_state=None gives _state_after={} -> moved=True: a missing state reads as 'moved'.")

print()
print('=== analytic: moved_on=True costs what? second branch snapshot is CURRENT state ===')
import inspect
src = inspect.getsource(G.Game.play_command)
i = src.find("moved = _moved_on(event, replay_current)")
print(src[i-120:i+700])
