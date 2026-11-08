"""A committed natural turn must close the open question.

solo/storage.py:242 closes every open clarification once a natural turn is
committed. Replacing it with pass left 36 tests green: none commits a natural
turn while a clarification is still open.

The row is written by store.apply() when the action carries metrics
{"natural": True}; store.record_scenario() takes no metrics at all, which is
why the earlier attempt to reach this line failed.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero, Plan, Step
from solo.rulesets import BUILTINS
from solo.storage import Store


def _statuses(store, cid):
    with store.connection() as db:
        return [r['status'] for r in db.execute(
            "SELECT status FROM conversations WHERE campaign=? AND kind='clarification'",
            (cid,)).fetchall()]


def test_a_settled_turn_closes_the_open_question(tmp_path):
    store = Store(tmp_path / 'clarify.sqlite3')
    cid = store.create('Уточнение', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    store.converse(cid, 'ask-1', 'Открываю дверь', 'Какую дверь?', context={})
    assert 'open' in _statuses(store, cid), 'the question was never open'

    plan = Plan(summary="Осмотреться",
                steps=[Step(kind="observe", text="Осматриваюсь")],
                needs_confirmation=False)
    version = store.campaign(cid)['version']
    action = store.stage(cid, 'look-1', 'Осматриваюсь', plan, version,
                         {'natural': True})
    store.apply(action["id"])

    assert 'open' not in _statuses(store, cid), (
        'the settled turn left the old question open: %r' % _statuses(store, cid))