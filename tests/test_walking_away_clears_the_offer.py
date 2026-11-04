"""Walking away clears an uncommitted offer, and never invents a victory."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store
from solo.world import ensure


def test_walking_away_clears_an_uncommitted_offer(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = travel.geography(state)
    world = ensure(state)
    world['pending_encounter'] = {'id': 'first-blood'}
    result = travel.advance_path(state, [], graph)
    assert not ensure(state).get('pending_encounter'), (
        "an uncommitted offer survived the walk", ensure(state))
    assert result["arrived"] is True