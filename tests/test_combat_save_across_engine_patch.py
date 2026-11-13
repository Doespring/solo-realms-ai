"""r182: a combat save made before a vendor patch still opens after it.

A durable fight carries the recipe, the per-command digests and, since r182, the
fingerprint of the rules that produced its numbers. The three cases are decided
by that fingerprint, not by the bare fact that a digest stopped matching:

* another engine revision  -> migrate, report the numbers that moved, re-bind;
* same revision, divergence -> a real fault, refused with the field named;
* no fingerprint (old save) -> "version unknown", never refused.

The old engine is simulated the only honest way available in a test: the save is
left holding the numbers and the digest that engine wrote. Nothing here patches
``vendor/``.
"""
from copy import deepcopy
import json

import pytest

from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import PATCHES, VENDOR, _reviewed, digest, engine_identity, read_json
from solo.storage import Store


@pytest.fixture
def fighter(tmp_path):
    store = Store(tmp_path / 'across-patch.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Save across a patch', seed=5)['id']
    return api, cid


def run(api, cid, identity, **action):
    from solo.game_commands import GameCommand
    return api.execute(cid, GameCommand(request_id=identity,
                                        expected_version=api.store.campaign(cid)['version'], action=action))


def in_combat(api, cid):
    return api.store.campaign(cid)['state']['nat20']['combat']


def rewrite(api, cid, mutate):
    """Change the saved fight as the older engine left it, nothing else."""
    state = deepcopy(api.store.campaign(cid)['state'])
    mutate(state['nat20']['combat'])
    with api.store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state, ensure_ascii=False), cid))


def test_identity_names_the_reviewed_patch_not_only_the_upstream_pin():
    identity = engine_identity()
    patches = read_json(PATCHES)
    assert identity['release'] == patches['version']
    assert identity['upstream'] == read_json(VENDOR / 'UPSTREAM.json')['commit']
    assert identity['rules'] == digest(_reviewed(read_json(VENDOR / 'UPSTREAM.json')))
    # The patch list is what may change the numbers; the pin alone must not be
    # mistaken for the whole answer, or every r177-style patch looks like a fault.
    assert any(entry['sha256'] for entry in patches['files'])


def test_fight_saved_on_another_engine_revision_migrates_and_is_journalled(fighter):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')

    def older_engine(combat):
        # What r-class patches do: same recipe, same pin, different numbers.
        combat['engine'] = dict(combat['engine'], release='0.9.0+solo.7', rules='0' * 64)
        combat['initial_digest'] = '1' * 64
        combat['view']['tracked_hp']['hero'] += 1

    rewrite(api, cid, older_engine)
    restored = api.restore(cid)
    migration = restored['engine_migration']
    assert migration['mode'] == 'migrated'
    assert migration['diverged'] is True
    assert 'прежней версии правил' in migration['text']
    assert any('tracked_hp.hero' in item for item in migration['differences']), migration['differences']
    # Restore is read-only: the stored numbers change only with the first
    # committed command, and that command says so in the journal.
    assert restored['runtime']['combat']['view']['tracked_hp']['hero'] == 13

    result = run(api, cid, 'after-patch', kind='end_turn')
    assert 'прежней версии правил' in result['event'].payload['outcome']
    assert result['result']['engine_migration']['mode'] == 'migrated'
    combat = in_combat(api, cid)
    assert combat['view']['tracked_hp']['hero'] == 12
    assert combat['engine'] == engine_identity()
    assert combat['initial_digest'] != '1' * 64
    assert combat['engine_migrations'][-1]['mode'] == 'migrated'
    # Re-bound, so the next turn replays strictly instead of migrating again.
    run(api, cid, 'after-patch-2', kind='end_turn')
    assert len(in_combat(api, cid)['engine_migrations']) == 1


def test_same_engine_with_a_broken_digest_refuses_and_names_the_field(fighter):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')

    def corrupt(combat):
        combat['initial_digest'] = '0' * 64
        combat['view']['tracked_hp']['goblin'] = 3

    rewrite(api, cid, corrupt)
    version = api.store.campaign(cid)['version']
    with pytest.raises(ValueError) as refused:
        api.restore(cid)
    message = str(refused.value)
    assert 'initial replay diverged' in message
    assert 'tracked_hp.goblin' in message, message
    with pytest.raises(ValueError) as refused_by_command:
        run(api, cid, 'broken', kind='end_turn')
    assert 'tracked_hp.goblin' in str(refused_by_command.value), str(refused_by_command.value)
    # No state committed by a refused command.
    assert api.store.campaign(cid)['version'] == version
    assert in_combat(api, cid)['view']['tracked_hp']['goblin'] == 3


def test_save_without_a_fingerprint_opens_as_version_unknown(fighter):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')
    rewrite(api, cid, lambda combat: combat.pop('engine'))
    restored = api.restore(cid)
    assert restored['engine_migration']['mode'] == 'unknown'
    assert 'неизвестна' in restored['engine_migration']['text']
    result = run(api, cid, 'unknown', kind='end_turn')
    assert 'Версия правил сохранения неизвестна' in result['event'].payload['outcome']
    assert in_combat(api, cid)['engine'] == engine_identity()


def test_save_without_a_fingerprint_and_a_stale_digest_is_not_refused(fighter):
    api, cid = fighter
    run(api, cid, 'start', kind='start_combat', encounter_id='basic')

    def ancient(combat):
        combat.pop('engine')
        combat['initial_digest'] = '2' * 64
        combat['view']['tracked_hp']['goblin'] += 4

    rewrite(api, cid, ancient)
    migration = api.restore(cid)['engine_migration']
    assert migration['mode'] == 'unknown'
    assert migration['differences'], migration
