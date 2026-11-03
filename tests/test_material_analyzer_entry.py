"""Scenario 1 of the analyzer task: pasted text alone must reach dialogue.

The player writes "Фаэрун, Побережье Мечей, Глубоководье" and attaches nothing.
The source names no opening place, no level and no party size. None of that may
close the table: the world is prepared, the questions are asked in dialogue, and
the answer is kept so the same question is not asked again.
"""
import os
os.environ['SOLO_TEST']='1'
import json
from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import PreparationFixture, terminal


def idea():
    """A pasted premise: two places, an NPC, no printed start, level or party."""
    return dict(title='Фаэрун', ruleset='dnd2024', start='', entities=[
        dict(key='waterdeep', kind='location', origin='original',
             data=dict(name='Глубоководье', description='Портовый город на Побережье Мечей: шум доков, стража у ворот.')),
        dict(key='docks', kind='location', origin='original',
             data=dict(name='Доки Глубоководья', description='Смолёные сваи, чайки и контрабандисты под фонарями.')),
        dict(key='harbourmaster', kind='npc', location='Доки Глубоководья', origin='original',
             data=dict(name='Барнабас', character='Хмурый смотритель причала.', motivation='Боится потерять место.')),
    ])


def prepared(client, text='Фаэрун, Побережье Мечей, Глубоководье'):
    draft=client.post('/api/creation/drafts').json()
    url='/api/creation/drafts/'+draft['id']
    client.put(url,json={'description':text,'revision':draft['revision']}).raise_for_status()
    started=client.post(url+'/prepare',json=dict(request_id='idea',consent_to_send=True,ruleset='dnd2024'))
    assert started.status_code==200,started.text
    status=terminal(client,started.json()['id'])
    assert status['ready'],status
    return url,'/api/preparation/drafts/'+draft['id'],status


def result_of(client,status):
    return json.loads(client.app.state.preparation_pipeline.raw(status['id'])['result'])


def confirmed(client,url):
    ready=client.get(url+'/world-readiness').json()
    response=client.post(url+'/confirm-world',json=dict(
        expected_revision=ready['revision'],run_id=ready['run_id'],confirm=True))
    assert response.status_code==200,response.text
    return ready


def test_pasted_text_alone_prepares_and_asks_instead_of_blocking(tmp_path):
    llm=PreparationFixture(result=idea())
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        url,prep,status=prepared(client)
        result=result_of(client,status)
        # The world exists and is complete, but it was never given a start.
        assert status['summary']['locations']==2 and status['summary']['npc']==1
        assert result['unknown_settings']==[
            dict(field='start',question='С какого места начинаем?',reason='Место начала не названо материалом')]
        assert [q['field'] for q in result['questions']][:3]==['start','level','party_size']
        # No blocking canon verdict, and no invented default start.
        assert not [q for q in result['quarantine'] if q.get('essential')]
        assert not result['start']
        # A text premise is prepared as a world, not as a compact name list.
        assert result['preparation_metadata']['preparation_mode']=='world_structure'


def test_agreed_start_is_recorded_and_never_asked_twice(tmp_path):
    llm=PreparationFixture(result=idea())
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        url,prep,_=prepared(client)
        ready=confirmed(client,url)
        assert ready['minimum_level'] is None and ready['recommended_party'] is None
        current=client.get(prep).json()
        assert current['body']['start_name']==''
        # A place the world does not contain is refused, not invented.
        bad=client.post(url+'/start-place',json=dict(
            expected_revision=current['revision'],name='Чёрная цитадель'))
        assert bad.status_code==409 and 'нет' in bad.json()['detail']
        calls=len(llm.payloads)
        settled=client.post(url+'/start-place',json=dict(
            expected_revision=current['revision'],name='Доки Глубоководья'))
        assert settled.status_code==200,settled.text
        body=client.get(prep).json()['body']
        assert body['start_name']=='Доки Глубоководья' and 'фонар' in body['opening']
        assert [q['field'] for q in body['questions']]==['level','party_size']
        # Local only: agreeing where we start never buys another analysis.
        assert len(llm.payloads)==calls
        again=client.post(url+'/start-place',json=dict(
            expected_revision=settled.json()['revision'],name='Доки Глубоководья'))
        assert again.status_code==200 and len(llm.payloads)==calls


def test_stale_revision_cannot_move_the_opening_place(tmp_path):
    llm=PreparationFixture(result=idea())
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        url,prep,_=prepared(client)
        confirmed(client,url)
        current=client.get(prep).json()
        stale=client.post(url+'/start-place',json=dict(
            expected_revision=current['revision']-1,name='Глубоководье'))
        assert stale.status_code==409
        assert client.get(prep).json()['body']['start_name']==''
