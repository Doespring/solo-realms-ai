"""The player must not receive the engine's own bookkeeping.

solo/combat_view.py:62-63 drops engine_snapshot and nat20 from the public state.
Removing those two lines left 11 tests green: every campaign they build runs on
the simplified calculator, so the engine subtree is simply absent and nothing
had to be removed. Section 4: server-side state is not the player's business.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.combat_view import public_campaign


def _campaign(**extra):
    state = {'location': 'Зал', 'locations': [], 'history': []}
    state.update(extra)
    return {'id': 'c1', 'state': state}


def test_the_engine_recipe_stays_server_side():
    seen = public_campaign(_campaign(nat20={'seed': 12345, 'recipe': 'internal'}))
    assert 'nat20' not in seen['state'], 'the engine recipe reached the player'


def test_the_engine_snapshot_stays_server_side():
    seen = public_campaign(_campaign(engine_snapshot={'secret': 1}))
    assert 'engine_snapshot' not in seen['state'], 'the snapshot reached the player'


def test_ordinary_state_survives_the_projection():
    seen = public_campaign(_campaign())
    assert seen['state']['location'] == 'Зал', 'the projection dropped the scene'
