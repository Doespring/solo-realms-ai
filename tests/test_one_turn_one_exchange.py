"""One accepted conversational turn is one public exchange.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 3) showed that
one line of dialogue produced a journal event *and* a conversation row carrying the
same question and the same answer. The web timeline renders both collections, so the
player read their own turn twice. Making keys unique never catches this: the keys
differ, the content does not.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM


def _campaign(tmp_path):
    app=create_app(tmp_path/'solo.sqlite3',MockLLM())
    store=app.state.store
    cid=store.create('Диалог',Hero(name='Мирон',hp=12,max_hp=12))['id']
    return app,store,cid


def _public_pairs(state):
    """An exchange the player can see: one journal pair plus one conversation pair.

    A conversation row linked to a rendered journal event through journal_event is
    the same accepted turn, so it is not counted twice.
    """
    journals={str(row['id']) for row in state['history']}
    pairs=len(state['history'])
    for row in state.get('conversation',[]):
        link=(row.get('context') or {}).get('journal_id') if isinstance(row.get('context'),dict) else None
        if row.get('journal_event') or (link and str(link) in journals):
            continue
        pairs+=1
    return pairs


def test_linked_conversation_row_is_not_a_second_exchange(tmp_path):
    app,store,cid=_campaign(tmp_path)
    with TestClient(app) as client:
        url='/api/campaigns/'+cid
        before=len(client.get(url).json()['history'])
        journal=store.record_scenario(cid,'social-probe','Спрашиваю Дорна про мост',lambda state:[dict(type='scripted',kind='conversation',text='Дорн отвечает про мост.')])
        store.converse(cid,'social-probe','Спрашиваю Дорна про мост','Дорн отвечает про мост.','notice',
                       dict(journal_id=journal['id']))
        state=client.get(url).json()
        rows=[r for r in state['conversation'] if r['request_id']=='social-probe']
        assert len(rows)==1,'the turn is stored once per collection'
        assert rows[0].get('journal_event')==str(journal['id']),'the second record names the event it repeats'
        assert _public_pairs(state)==before+1,'one turn must show as one exchange, not two'


def test_two_identical_lines_stay_two_exchanges(tmp_path):
    app,store,cid=_campaign(tmp_path)
    with TestClient(app) as client:
        url='/api/campaigns/'+cid
        before=len(client.get(url).json()['history'])
        for i in range(2):
            journal=store.record_scenario(cid,'social-same-'+str(i),'Стучу в дверь',lambda state:[dict(type='scripted',kind='conversation',text='Изнутри не отвечают.')])
            store.converse(cid,'social-same-'+str(i),'Стучу в дверь','Изнутри не отвечают.','notice',dict(journal_id=journal['id']))
        state=client.get(url).json()
        assert _public_pairs(state)==before+2,'identical wording must not merge separate turns'


def test_unlinked_conversation_row_is_not_swallowed(tmp_path):
    app,store,cid=_campaign(tmp_path)
    with TestClient(app) as client:
        url='/api/campaigns/'+cid
        before=len(client.get(url).json()['history'])
        store.converse(cid,'clarify-only','Пробую','Что именно вы хотите сделать?','clarification')
        state=client.get(url).json()
        row=next(r for r in state['conversation'] if r['request_id']=='clarify-only')
        assert 'journal_event' not in row,'a standalone clarification has no event to repeat'
        assert _public_pairs(state)==before+1
