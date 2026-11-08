"""An untrusted NPC-location relation must not close a valid adventure."""
import os
os.environ['SOLO_TEST']='1'

import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.adventure_library import AdventureLibrary
from solo.prepared_world import PreparedWorldValidator
from test_material_analyzer_entry import idea, prepared, result_of, confirmed
from test_preparation_pipeline import PreparationFixture


@pytest.mark.parametrize('essential',[False,True])
def test_unresolved_original_npc_link_does_not_block_confirmation(tmp_path,essential):
    payload=idea()
    npc=payload['entities'][-1]
    npc.update(location='Неподтверждённый двор',essential=essential)
    llm=PreparationFixture(result=payload)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        url,_,status=prepared(client)
        result=result_of(client,status)
        assert result['ready']
        problems=[q for q in result['quarantine'] if q.get('key')==npc['key']]
        assert problems and all(not q.get('essential') for q in problems)
        places=result['definition']['world']['locations']
        assert len(places)==2
        assert not any(p['name']==npc['location'] for p in places)
        assert not any(n['name']=='Барнабас' for p in places for n in p['npc'])
        proof=result['provenance'][npc['key']]
        assert proof['location_status']=='unresolved'
        assert proof['location_claim_rejected'] is True
        calls=len(llm.payloads)
        assert confirmed(client,url)['world_ready']
        assert len(llm.payloads)==calls


def test_source_npc_invalid_link_is_quarantined_without_inventing_location(tmp_path):
    library=AdventureLibrary(tmp_path)
    document='a'*64
    text='Башня. На пороге слышен ветер. Ирина — смотрительница башни.'
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
            (document,'.txt',len(text.encode()),'extracted',1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
            (document,1,'1',text))
    proof=[dict(document=document,page=1,quote=text)]
    payload=dict(ruleset='dnd2014',start='Башня',entities=[
        dict(key='tower',kind='location',data=dict(name='Башня',description='На пороге слышен ветер.'),evidence=proof),
        dict(key='keeper',kind='npc',location='Несуществующий дворец',
            data=dict(name='Ирина'),evidence=proof,essential=True)])
    result=PreparedWorldValidator(library,{document}).compile([payload],'dnd2024')
    assert result['ready'],result['quarantine']
    assert result['provenance']['keeper']['location_claim_rejected'] is True
    assert [p['name'] for p in result['definition']['world']['locations']]==['Башня']
    assert result['definition']['world']['locations'][0]['npc']==[]
