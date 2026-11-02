"""An old save without a stored map gets only the roads it can walk."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_an_old_save_gets_no_road_to_a_place_it_lacks(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state.pop('geography', None)
    state['locations'] = [p for p in state['locations']
                          if p['name'] == 'Таверна «Последний огонь»']
    state['location'] = 'Таверна «Последний огонь»'
    graph = travel.geography(state)
    targets = {r['to'] for r in graph['routes']}
    assert 'tower' not in targets and 'river' not in targets, (
        'an old save got a road to a place it does not have', graph['routes'])