"""The map handed out is a copy, not the campaign state itself."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_editing_the_map_does_not_edit_the_campaign(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['geography'] = {'nodes': [{'id': 'a', 'name': 'Двор',
                                     'coordinates': [0, 0]}],
                         'routes': []}
    graph = travel.geography(state)
    graph['nodes'].append({'id': 'b', 'name': 'Лишний узел',
                           'coordinates': [1, 1]})
    assert len(state['geography']['nodes']) == 1, (
        'editing the map changed the campaign state')