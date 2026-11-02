"""A rejected proposal must stay rejected.

solo/storage.py:346 records the refusal in narrative_data. Replacing that
statement with a bare return None left 10 tests green: none of them rejects a
proposal and then asks for it again.

Without the write the row still reads status pending, so the same proposal can
be approved later - a fact the player already turned down lands in the world.
"""
import os
os.environ['SOLO_TEST']='1'

import json

import pytest

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store


def _event_with_proposal(store, cid):
    store.record_scenario(cid, 'look-1', 'Осматриваюсь',
                          lambda state: [dict(type='scripted', text='Тихо.')])
    event = store.history(cid)[-1]['id']
    store.narrative(event, 'На столе лежит ржавый ключ.', 'complete', '',
                    structure={'description': 'На столе лежит ржавый ключ.',
                               'dialogue': [],
                               'proposals': [{'text': 'На столе лежит ржавый ключ.',
                                                'category': 'discovery'}]})
    return event


def test_a_refused_proposal_cannot_be_approved_later(tmp_path):
    store = Store(tmp_path / 'proposal.sqlite3')
    cid = store.create('Предложение', Hero(name='Герой'), BUILTINS['dnd2024'])['id']
    event = _event_with_proposal(store, cid)
    data = store.event(event)['narrative_data']
    data = data if isinstance(data, dict) else json.loads(data)
    pid = data['proposals'][0]['id']

    store.decide_proposal(event, pid, False)

    with pytest.raises(ValueError):
        store.decide_proposal(event, pid, True)