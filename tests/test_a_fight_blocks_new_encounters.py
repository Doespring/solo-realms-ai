"""No new encounter is staged while a fight is on or one already waits."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.storage import Store
from solo.world import WorldSettings, ensure, observe, tick


def _world_with_event(tmp_path, name):
    store = Store(tmp_path / name)
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    world = ensure(state)
    world['events'].append(dict(id='enc1', kind='encounter', after_minutes=0,
                                location='Yard', encounter=None))
    return state, world


def test_no_encounter_while_a_fight_is_on(tmp_path):
    state, world = _world_with_event(tmp_path, 'fight.sqlite3')
    state['combat'] = dict(active=True)
    tick(state)
    assert world.get('pending_encounter') is None


def test_no_second_encounter_while_one_waits(tmp_path):
    state, world = _world_with_event(tmp_path, 'waiting.sqlite3')
    world['pending_encounter'] = dict(id='old', location='Yard', text='An old foe.')
    tick(state)
    assert world['pending_encounter']['id'] == 'old'