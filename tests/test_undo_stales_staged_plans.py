"""An undo must invalidate plans staged earlier, so they cannot be confirmed blind.

solo/storage.py:554 marks pending actions of the campaign as 'stale' after a rollback.
Replacing that line with pass left 16 tests green: they stage actions but never
undo while one is waiting. Section 11: nothing from the taken-back state may be
applied afterwards.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


class _Plan:
    def model_dump_json(self):
        return '{}'


def test_a_staged_plan_goes_stale_after_undo(tmp_path):
    store = Store(tmp_path / 'stale.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    version = store.campaign(cid)['version']
    staged = store.stage(cid, 'wait-1', 'Жду', _Plan(), version, {})
    assert staged['status'] == 'pending'

    store.record_scenario(cid, 'r1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    store.undo(cid)

    assert store.action(staged['id'])['status'] == 'stale', (
        'a plan from the undone state can still be confirmed')
