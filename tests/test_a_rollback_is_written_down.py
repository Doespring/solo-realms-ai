"""A rollback must leave a trace of itself in the operations log."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero, Plan, Step
from solo.rulesets import BUILTINS
from solo.storage import Store


def _turns(store, cid):
    plan = Plan(summary='Осмотреться',
                steps=[Step(kind='observe', text='Осматриваюсь')],
                needs_confirmation=False)
    for index in ('one', 'two'):
        version = store.campaign(cid)['version']
        action = store.stage(cid, index, 'Осматриваюсь', plan, version,
                             {'natural': True})
        store.apply(action['id'])


def _undo_rows(store, cid):
    with store.connection() as db:
        return db.execute('SELECT * FROM state_operations'
                          ' WHERE campaign=?', (cid,)).fetchall()


def test_a_rollback_is_written_down(tmp_path):
    store = Store(tmp_path / 'undo-log.sqlite3')
    cid = store.create('Откат', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    _turns(store, cid)

    store.undo(cid)

    kind = 'undo'
    rows = [r for r in _undo_rows(store, cid) if r[2] == kind]
    assert len(rows) == 1, 'the rollback left no trace: %r' % _undo_rows(store, cid)