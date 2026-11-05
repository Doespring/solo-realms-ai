"""The chronicle keeps only its recent tail."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.storage import Store
from solo.world import WorldSettings, ensure, observe, tick


def _state(tmp_path):
    store = Store(tmp_path / 'chronicle.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_the_chronicle_keeps_only_its_tail(tmp_path):
    state = _state(tmp_path)
    world = ensure(state)
    world['chronicle'] = [dict(minutes=i, text=f'Day {i}', source='seed')
                          for i in range(250)]
    tick(state)
    assert len(world['chronicle']) <= 200


def test_the_newest_records_survive_the_trim(tmp_path):
    state = _state(tmp_path)
    world = ensure(state)
    world['chronicle'] = [dict(minutes=i, text=f'Day {i}', source='seed')
                          for i in range(250)]
    tick(state)
    assert world['chronicle'][-1]['text'] == 'Day 249'