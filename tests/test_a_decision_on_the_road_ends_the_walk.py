"""A road that asks for a decision stops the walk there."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_decision_on_the_road_ends_the_walk(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = travel.geography(state)
    roads = [
        {'from': 'tavern', 'to': 'tower', 'meters': 2000, 'minutes': 30,
         'description': 'лесная тропа', 'known': True, 'timing': 'scenario'},
        {'from': 'tower', 'to': 'river', 'meters': 1000, 'minutes': 20,
         'description': 'тропа от башни', 'known': True, 'timing': 'scenario',
         'decision': 'У развилки нужно выбрать сторону.'},
    ]
    result = travel.advance_path(state, roads, graph)
    assert result["arrived"] is False, ("the walk ignored a decision", result)
    assert "развилки" in str(result.get("text", "")), result