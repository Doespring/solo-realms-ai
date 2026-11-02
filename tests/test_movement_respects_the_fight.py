"""Movement must respect the fight the mechanics actually run.

The independent audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 4)
started a real Nat20 encounter and found travel_guard still answering "allowed,
speed 30": it read the legacy state['combat'] map, which the Nat20 backend never
writes. The host keeps the geography; the backend decides whether walking is
mechanically possible right now.
"""
import os
os.environ['SOLO_TEST']='1'

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from nat20_builds import legal
from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.prototype_prepared import default_encounters
from solo.storage import Store
from solo.world_service import travel_guard

BUILD = dict(class_slug='fighter', species_slug='human', level=5,
             subclass_slug='champion', equipment=['longsword', 'chain-mail', 'shield'])


def _campaign(tmp_path):
    store = Store(tmp_path / 'solo.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_experimental_campaign(
        'Проба', build=legal(**BUILD), encounters=default_encounters(),
        seed=3, title='Двор', description='д')['id']

    def go(action, rid):
        current = store.campaign(cid)
        return api.execute(cid, GameCommand(request_id=rid, expected_version=current['version'], action=action))
    return store, api, cid, go


def test_active_nat20_fight_forbids_walking_away(tmp_path):
    store, api, cid, go = _campaign(tmp_path)
    before = store.campaign(cid)['state']
    assert not before.get('combat'), 'the legacy combat map is not where Nat20 keeps the fight'
    travel_guard(before)  # out of combat the hero may walk; this must not raise
    fight = go(dict(kind='start_combat', encounter_id='first-blood'), 'fight')
    assert fight['journal'], fight
    state = store.campaign(cid)['state']
    assert state['nat20'].get('combat'), 'the fight is live in the backend'
    assert not state.get('combat'), 'and still absent from the legacy map'
    with __import__('pytest').raises(ValueError):
        travel_guard(state)


def test_walking_is_allowed_again_once_the_backend_reports_no_fight(tmp_path):

    # A fight cannot be ended while hostiles live (the engine refuses that, which

    # is its own safeguard), so the state after a finished fight is expressed the

    # way the backend leaves it: nat20 present, combat cleared.

    store, api, cid, go = _campaign(tmp_path)

    state = store.campaign(cid)['state']

    state['nat20']['combat'] = None

    speed = travel_guard(state)

    assert isinstance(speed, int) and speed > 0



def test_legacy_combat_map_still_blocks_old_saves(tmp_path):
    store, api, cid, go = _campaign(tmp_path)
    state = store.campaign(cid)['state']
    state['combat'] = dict(active=True)
    with __import__('pytest').raises(ValueError):
        travel_guard(state)
