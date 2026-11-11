
import os
os.environ['SOLO_TEST']='1'
import sys, asyncio, tempfile, json
from pathlib import Path
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from scripts.command_smoke import FixedDice, setup
from solo import game as G
from solo.world_service import WorldService
from solo import contracts

tmp = Path(tempfile.mkdtemp())
store, campaign, game = setup(tmp)
print('state keys:', sorted(store.campaign(campaign)['state'].keys()))

# WorldService.execute with kind='settings' needs a world dict; try settings first
ws = WorldService(store)
from solo.world_service import ensure
with store.connection(True) as c:
    cur = store.campaign(campaign, c)
    st = cur['state']; ensure(st)
    store_ok = True

# create a world event directly through engine_event using the same transition shape
with store.connection(True) as c:
    cur = store.campaign(campaign, c)
    def transition(state):
        ensure(state)
        state['location'] = state.get('location')
        return [dict(type='scripted', kind='world', text='Осмотрелся: ничего не изменилось.')]
    ev = store.engine_event(c, cur, 'req-world-1', 'Мир: observe', transition, 'sig-1')
print('created event', ev['id'], 'results=', ev['results'])
print('after_state type:', type(ev['after_state']).__name__)

# Now replay via game.play_command using the same request_id
v = store.campaign(campaign)['version']
print('current version', v)
calls=[]
orig = G._moved_on
def spy(event, current):
    r = orig(event, current); calls.append(r); return r
G._moved_on = spy
res = asyncio.run(game.play_command(campaign, 'req-world-1', 'Мир: observe', v, rng=FixedDice()))
G._moved_on = orig
print('replay status:', res.get('status'), 'replayed:', res.get('replayed'), 'moved_on:', res.get('moved_on'))
print('_moved_on called:', calls)
