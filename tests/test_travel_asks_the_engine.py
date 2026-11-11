"""Movement must obey the engine that actually runs the hero.

When Nat20 runs a campaign, HP, conditions and exhaustion change inside the
engine; state['hero'] is only a copy taken at the last projection. The travel
guard read that copy, so a hero exhausted to the point of death in the engine
still walked at full speed.

The guard now asks the engine for its own projection of the hero.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.nat20_api import Nat20GameEngine
from solo.storage import Store
from solo.world_service import travel_guard


@pytest.fixture
def campaign(tmp_path):
    api = Nat20GameEngine(Store(tmp_path/'travel.sqlite3'))
    cid = api.create_campaign('Странник', seed=7)['id']
    return api, cid


def state_of(api, cid, **nat20):
    state = api.store.campaign(cid)['state']
    if nat20:
        state = dict(state)
        engine = dict(state['nat20'])
        engine.update(nat20)
        state['nat20'] = engine
    return state


def test_a_healthy_hero_walks(campaign):
    api, cid = campaign
    assert travel_guard(state_of(api, cid)) > 0


def test_exhaustion_the_engine_knows_stops_the_walk(campaign):
    api, cid = campaign
    # Six levels of exhaustion in 2024 rules is mechanical death. The engine
    # records it; the legacy copy of the hero does not see it at all.
    with pytest.raises(ValueError):
        travel_guard(state_of(api, cid, exhaustion=6))


def test_the_legacy_copy_is_not_consulted_when_the_engine_runs(campaign):
    api, cid = campaign
    state = state_of(api, cid, exhaustion=6)
    # A forged healthy copy must not buy a walk: the engine is the authority.
    state = dict(state)
    state['hero'] = dict(state['hero'], exhaustion=0, hp=state['hero']['max_hp'])
    with pytest.raises(ValueError):
        travel_guard(state)
