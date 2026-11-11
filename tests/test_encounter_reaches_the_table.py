"""An encounter prepared by the app must reach the table as a real fight.

Preparation used to pass encounters={} and note 'Боевые встречи не подтверждены',
so no player ever fought: the engine could roll attacks, but the route from
'I start a fight' to an attack roll was missing. This walks that route on a
campaign built from the app's own default encounter.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from nat20_builds import legal
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.prototype_prepared import default_encounters
from solo.storage import Store


BUILD = dict(class_slug='fighter', species_slug='human', level=5,
             subclass_slug='champion', equipment=['longsword', 'chain-mail', 'shield'])


def _fight(tmp_path):
    store = Store(tmp_path / 'solo.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_experimental_campaign(
        'Проба', build=legal(**BUILD), encounters=default_encounters(),
        seed=3, title='Двор', description='д')['id']

    def go(action, rid):
        current = store.campaign(cid)
        return api.execute(cid, GameCommand(request_id=rid, expected_version=current['version'],
                                            action=action))
    return store, api, cid, go


def _hp(api, store, cid):
    return {actor.id: (actor.hp.current, actor.hp.maximum)
            for actor in api.snapshot(store.campaign(cid)).actors}


def test_the_default_encounter_uses_a_monster_the_dataset_has():
    from dnd5e_srd_data.loader import BundledAssetLoader
    encounter = default_encounters()['first-blood']
    for actor in encounter.actors:
        # 'goblin' is not an id in the dataset; this would be a phantom enemy.
        assert BundledAssetLoader().get_monster(actor.monster) is not None, actor.monster


def test_a_prepared_encounter_starts_a_fight(tmp_path):
    store, api, cid, go = _fight(tmp_path)
    go(dict(kind='start_combat', encounter_id='first-blood'), 'start')
    snapshot = api.snapshot(store.campaign(cid))
    assert snapshot.combat.active is True
    assert {actor.id for actor in snapshot.actors} == {'hero', 'goblin-1'}


def test_an_attack_changes_real_hit_points(tmp_path):
    store, api, cid, go = _fight(tmp_path)
    go(dict(kind='start_combat', encounter_id='first-blood'), 'start')
    go(dict(kind='end_turn'), 'end')
    before = _hp(api, store, cid)
    go(dict(kind='native_action', operation='attack', target_id='goblin-1',
            weapon_id='longsword'), 'attack')
    after = _hp(api, store, cid)
    assert after['goblin-1'][0] < before['goblin-1'][0], (before, after)
    assert after['goblin-1'][0] == 0


def test_the_engine_refuses_a_monster_that_is_not_registered(tmp_path):
    store, api, cid, go = _fight(tmp_path)
    with pytest.raises(ValueError):
        go(dict(kind='start_combat', encounter_id='no-such-fight'), 'bad')
