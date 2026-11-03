"""Travel time rounds up: no road is ever shorter than its distance."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store
from solo.rulesets import BUILTINS


def test_travel_minutes_round_up(tmp_path):
    store = Store(tmp_path / 'pace.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['ruleset'] = BUILTINS['dnd2024'].model_dump()
    hero = state['hero']
    hero['speed_ft'] = 30
    edge = {'from': 'a', 'to': 'b', 'meters': 1, 'minutes': 0,
            'description': 'шаг', 'known': True, 'timing': 'engine',
            'pace': 'normal'}
    minutes = travel.duration(edge, 30, state)
    assert minutes >= 1, ("a short road took no time", minutes)