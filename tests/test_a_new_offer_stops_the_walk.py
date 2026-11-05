"""A step that raises an offer stops the walk before the next step."""
import os
os.environ['SOLO_TEST']='1'

import solo.travel as road
import solo.world as world_module
from solo.contracts import Hero
from solo.storage import Store
from solo.world import ensure


def test_a_new_offer_stops_the_walk_at_the_first_step(tmp_path, monkeypatch):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = road.geography(state)
    roads = [
        {'from': 'tavern', 'to': 'tower', 'meters': 2000, 'minutes': 30,
         'description': 'лесная тропа', 'known': True, 'timing': 'scenario'},
        {'from': 'tower', 'to': 'river', 'meters': 1000, 'minutes': 20,
         'description': 'тропа от башни', 'known': True, 'timing': 'scenario'},
    ]
    calls = {"n": 0}
    real = world_module.tick

    def tick_that_offers(after_first_step):
        calls["n"] += 1
        messages, records = real(after_first_step)
        if calls["n"] == 2:
            ensure(after_first_step)['pending_encounter'] = {
                'id': 'on-the-road'}
        return messages, records

    monkeypatch.setattr(world_module, 'tick', tick_that_offers)
    result = road.advance_path(state, roads, graph)
    assert state['location'] == 'Старая башня', (
        "the walk went past the place where the offer came up", result)
    assert result["arrived"] is False