"""The user's real 36/38 stop: repair without a second paid call.

A booked PDF run validated 36 of 38 blocks and failed on two whose
``start``/``location`` came back as reference objects. The saved 36 must be
untouched and the 2 must be repaired from their stored raw answer, with zero
new model calls.
"""
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.playable_ai_readiness import mock_cloud
from solo.app import create_app
from test_cloud import sse

BAD = ('{"title":"Том","entries":[{"kind":"location","name":"Мост",'
       '"source_spans":["@S1@"],"start":{"name":"Мост"}},'
       '{"kind":"npc","name":"Ирина","source_spans":["@S2@"],"location":{"name":"Мост"}}]}')


def model(directory, bad_block=1):
    calls = []

    def respond(request):
        packet = json.loads(json.loads(request.content)['messages'][-1]['content'])
        index = packet['batch']
        calls.append(index)
        text = packet['pages'][0]['text']
        ids = re.findall(r'\[(S\d+)\]', text)
        if index == bad_block:
            value = BAD.replace('@S1@', ids[0]).replace('@S2@', ids[-1])
        else:
            entity = dict(kind='location', name='Мост',
                          source_spans=[ids[0]], start=True)
            value = json.dumps(dict(title='Том', entries=[entity]), ensure_ascii=False)
        return sse([dict(choices=[dict(delta=dict(content=value))]),
                    dict(choices=[], usage=dict(prompt_tokens=1000, completion_tokens=200))])

    llm, profile = mock_cloud(directory, respond)
    profile.context_tokens = 262144
    llm.settings.save(profile)
    return llm, calls


def terminal(client, run):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = client.get('/api/creation/runs/' + run).json()
        if value['status'] not in ('queued', 'running'):
            return value
        time.sleep(.02)
    raise AssertionError('preparation did not settle')


def upload(client, url, count=3):
    deadline = time.monotonic() + 30
    for index in range(count):
        raw = (f'Read aloud: Мост через реку {index}.\nИрина ждёт у моста.\n' * 200).encode()
        response = client.post(url + '/files', files={'file': (f'book{index}.txt', raw)})
        response.raise_for_status()
    while any(f['job']['status'] != 'complete' for f in client.get(url).json()['files']):
        assert time.monotonic() < deadline
        time.sleep(.02)


def test_reference_objects_no_longer_fail_a_block_at_all(tmp_path):
    """With the repair in place the bad shape never becomes a failed block."""
    llm, calls = model(tmp_path)
    with TestClient(create_app(tmp_path / 'inline.sqlite3', llm)) as client:
        created = client.post('/api/creation/drafts').json()
        did = created['id']; url = '/api/creation/drafts/' + did
        client.put(url, json=dict(description='Реальная книга', revision=created['revision'])).raise_for_status()
        upload(client, url)
        run = terminal(client, client.post(url + '/prepare', json=dict(
            request_id='real-book', ruleset='dnd2014', consent_to_send=True)).json()['id'])
        with client.app.state.preparation.connection() as db:
            failed = db.execute('SELECT id FROM creation_parts WHERE status="failed"').fetchall()
        assert run['status'] == 'ready', run
        assert run['done'] == run['total']
        assert not failed, 'a reference object still failed a block'


def test_a_run_saved_before_the_fix_is_repaired_without_new_calls(tmp_path):
    llm, calls = model(tmp_path)
    with TestClient(create_app(tmp_path / 'repair.sqlite3', llm)) as client:
        created = client.post('/api/creation/drafts').json()
        did = created['id']; url = '/api/creation/drafts/' + did
        client.put(url, json=dict(description='Реальная книга', revision=created['revision'])).raise_for_status()
        upload(client, url)
        # This regression is about the legacy compact run, where the stop happened, so the
        # mode is stated explicitly instead of taking the new world-structure default.
        run = terminal(client, client.post(url + '/prepare', json=dict(
            request_id='real-book', world_structure=False,
            ruleset='dnd2014', consent_to_send=True)).json()['id'])
        assert run['status'] == 'ready'

        # Recreate exactly the persisted state of the user's pre-fix stop:
        # one block failed with a stored raw answer that carries objects.
        with client.app.state.preparation.connection() as db:
            row = db.execute('SELECT id,raw FROM creation_parts WHERE status="validated" LIMIT 1').fetchone()
            good = {r['id']: r['body'] for r in
                    db.execute('SELECT id,body FROM creation_parts WHERE status="validated"')}
            ids = re.findall(r'\[(S\d+)\]', row['raw']) or []
            broken = BAD.replace('@S1@', ids[0] if ids else 'S001').replace('@S2@', ids[-1] if ids else 'S002')
            db.execute('UPDATE creation_parts SET status="failed",body=NULL,raw=?,diagnostic=? WHERE id=?',
                       (broken, json.dumps([dict(field='$', reason='invalid_campaign_preparation',
                                                 message='Validation failed')]), row['id']))
            db.execute('UPDATE creation_runs SET status="partial",result=NULL WHERE id=?', (run['id'],))
        assert row['id'] in good

        calls.clear()
        # This run's stored mode was deliberately lost (result=NULL), so silence is still
        # refused: an unknown mode needs one explicit choice. The repair itself is local.
        silent = client.post(url + '/prepare', json=dict(
            request_id='silent', ruleset='dnd2014', consent_to_send=True))
        assert silent.status_code == 409, silent.text
        again = client.post(url + '/prepare', json=dict(
            request_id='real-book-2', world_structure=False,
            ruleset='dnd2014', consent_to_send=True))
        assert again.status_code == 200, again.text
        resumed = terminal(client, again.json()['id'])

        with client.app.state.preparation.connection() as db:
            after = {r['id']: r['body'] for r in
                     db.execute('SELECT id,body FROM creation_parts WHERE status="validated"')}
            diagnostic = json.loads(db.execute(
                'SELECT diagnostic FROM creation_parts WHERE id=?', (row['id'],)).fetchone()['diagnostic'])
        for part, body in good.items():
            if part != row['id']:
                assert after[part] == body, 'a previously good block changed'
        assert after.get(row['id']), 'the failed block was not repaired'
        assert any(d['reason'] == 'local_repair' for d in diagnostic)
        assert not calls, 'local repair must not call the model again'
        assert resumed['done'] == resumed['total']

