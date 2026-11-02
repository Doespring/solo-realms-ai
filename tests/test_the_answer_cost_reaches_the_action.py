"""The answer's own cost must land on the action that earned it.

solo/storage.py:320 merges the metrics passed to narrative() into the action
row so the ledger can be read back per turn. Replacing it with pass left 36
tests green: nothing reads the stored llm list after narrative() writes it.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero, Plan, Step
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_the_answer_metrics_reach_the_action(tmp_path):
    store = Store(tmp_path / 'metrics.sqlite3')
    cid = store.create('Расход', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    plan = Plan(summary="Осмотреться",
                steps=[Step(kind="observe", text="Осматриваюсь")],
                needs_confirmation=False)
    action = store.stage(cid, 'look-1', 'Осматриваюсь', plan,
                         store.campaign(cid)['version'], {'natural': True})
    store.apply(action['id'])
    event = store.event_for_action(action['id'])['id']

    store.narrative(event, 'Двор пуст.', 'complete', '',
                    metrics=[{'role': 'narrator', 'micros': 4242}])

    spent = store.action(action['id'])['metrics'].get('llm') or []
    assert spent, 'the answer cost was never written to the action'
    assert spent[-1]['micros'] == 4242, 'the wrong cost was recorded'