"""A locked door between two places stops the walk."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_locked_door_stops_the_walk(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = travel.geography(state)
    for place in state['locations']:
        if place['name'] == 'Старая башня':
            place['interactions'] = [dict(id='door-1', name='Дверь', kind='door', destination='Берег реки', accessible=True, lock='locked', opened=False)]
    road = {'from': 'tower', 'to': 'river', 'meters': 1000, 'minutes': 20,
            'description': 'тропа от башни', 'known': True, 'timing': 'scenario'}
    state['location'] = 'Старая башня'
    result = travel.advance_path(state, [road], graph)
    assert result["arrived"] is False, (
        "the walk went through a locked door", result)
    assert state['location'] == 'Старая башня', state['location']
    assert 'закрыт' in str(result.get('text', '')).lower(), result