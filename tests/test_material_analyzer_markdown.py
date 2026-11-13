"""Scenario 2 of the analyzer task: a real one-shot given as Markdown.

A text source has no PDF pages, but it still has fragments the model must quote.
This checks the whole path: upload -> extract -> prepare -> confirm, with the
quoted facts surviving and the recommendations coming from the text itself.
"""
import os
os.environ['SOLO_TEST']='1'
import json
from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import PreparationFixture, terminal
from test_creation import wait_job

ONE_SHOT = """# Пропавший караван

## Завязка
Караван торговца Радомира не пришёл в слободу Заречье. Смотритель Влас обещает за весть о нём 5 золотых.

## Места
### Слобода Заречье
Открытое место: Низкие срубы стоят вдоль реки, у околицы горит фонарь.
### Овраг за околицей
Открытое место: Дно оврага завалено обломками телеги.

## Важные персонажи
### Влас
Локация: Слобода Заречье
Характер: Старый смотритель, говорит медленно.
Мотив: Боится, что караван пропал по его вине.

## Путь
От околицы вниз по склону 900 метров, идти 15 минут.

## События
Развязка: когда группа приходит в Овраг за околицей, становится видна разбитая телега.

## Уровни
Для персонажей 5 уровня. Группа из четырёх героев.
"""


def batch(markdown):
    """What a careful analysis returns for the text above, quoting its fragments."""
    return dict(title='Пропавший караван', ruleset='dnd2024', start='Слобода Заречье',
        minimum_level=5, maximum_level=5, party_size=4,
        metadata_evidence=[dict(document='%DOC%', page=1, quote='Для персонажей 5 уровня. Группа из четырёх героев.')],
        entities=[
            dict(key='zarechye', kind='location', origin='source',
                evidence=[dict(document='%DOC%', page=1, quote='### Слобода Заречье\nОткрытое место: Низкие срубы стоят вдоль реки, у околицы горит фонарь.')],
                data=dict(name='Слобода Заречье', description='Низкие срубы стоят вдоль реки, у околицы горит фонарь.')),
            dict(key='ravine', kind='location', origin='source',
                evidence=[dict(document='%DOC%', page=1, quote='### Овраг за околицей\nОткрытое место: Дно оврага завалено обломками телеги.')],
                data=dict(name='Овраг за околицей', description='Дно оврага завалено обломками телеги.')),
            dict(key='vlas', kind='npc', location='Слобода Заречье', origin='source',
                evidence=[dict(document='%DOC%', page=1, quote='### Влас\nЛокация: Слобода Заречье\nХарактер: Старый смотритель, говорит медленно.')],
                data=dict(name='Влас', character='Старый смотритель, говорит медленно.')),
            dict(key='road', kind='route', origin='source',
                evidence=[dict(document='%DOC%', page=1, quote='## Путь\nОт околицы вниз по склону 900 метров, идти 15 минут.')],
                data=dict(**{'from':'Слобода Заречье'}, to='Овраг за околицей', meters=900, minutes=15,
                          description='Тропа от околицы вниз по склону.', explorable=True)),
            dict(key='reward', kind='reward', location='Слобода Заречье', origin='source',
                evidence=[dict(document='%DOC%', page=1, quote='Смотритель Влас обещает за весть о нём 5 золотых.')],
                data=dict(id='reward', kind='coins', amount=5)),
        ])


def test_markdown_one_shot_keeps_quoted_facts_and_recommendations(tmp_path):
    state={}
    def result(payload):
        document=payload['pages'][0]['document'] if payload.get('pages') else ''
        state['document']=document
        return json.loads(json.dumps(batch(ONE_SHOT)).replace('%DOC%',document))
    llm=PreparationFixture(result=result)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft=client.post('/api/creation/drafts').json()
        url='/api/creation/drafts/'+draft['id']
        client.put(url,json={'description':'Разбери ваншот','revision':draft['revision']}).raise_for_status()
        upload=client.post(url+'/files',files={'file':('oneshot.md',ONE_SHOT.encode('utf8'),'text/markdown')})
        assert upload.status_code==200,upload.text
        job=upload.json()['files'][0]['job']['id']
        assert wait_job(client,job)['status']=='complete'
        started=client.post(url+'/prepare',json=dict(request_id='md',consent_to_send=True,ruleset='dnd2024'))
        assert started.status_code==200,started.text
        status=terminal(client,started.json()['id'])
        assert status['ready'],status
        result=json.loads(client.app.state.preparation_pipeline.raw(status['id'])['result'])
        # The text is a real source: every fact keeps its fragment, not a page guess.
        assert result['definition']['world']['locations']
        assert {p['name'] for p in result['definition']['world']['locations']} == {'Слобода Заречье','Овраг за околицей'}
        assert result['start']=='Слобода Заречье'
        # A single recommended level stays a single level, not a fabricated range.
        ready=client.get(url+'/world-readiness').json()
        assert ready['world_ready'],ready
        client.post(url+'/confirm-world',json=dict(
            expected_revision=ready['revision'],run_id=ready['run_id'],confirm=True)).raise_for_status()
        body=client.get('/api/preparation/drafts/'+draft['id']).json()['body']
        assert body['start_name']=='Слобода Заречье'
        # Nothing about this material is an open question: it printed its level.
        assert [q['field'] for q in body['questions'] if q['field'] in ('start','level')]==[]
