"""The health answer must say when this process took its identity.

Section 9.4 asks the backend stamp to be fixed at startup. `build_identity`
returns 'started' and the list of modules it covered, but /api/health dropped
them: a reader could see which bytes the process believed in, yet had no way to
tell a process started a minute ago from one running since before the edit.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from test_dialogue_paging_api import MockLLM


def health(tmp_path):
    api = TestClient(create_app(tmp_path/'health.sqlite3', MockLLM()))
    return api.get('/api/health').json()


def test_health_says_when_the_identity_was_taken(tmp_path):
    body = health(tmp_path)
    assert body.get('started'), 'the answer must carry the moment identity was fixed'


def test_health_names_the_scope_it_covered(tmp_path):
    body = health(tmp_path)
    # 'covered' lists the packages the stamp speaks for; 'modules' counts them.
    assert body.get('covered'), 'a stamp without its scope cannot be checked'
    assert isinstance(body['covered'], list)
    assert isinstance(body.get('modules'), int) and body['modules'] > 0
    assert body['modules'] == len(body['covered'])


def test_health_still_carries_the_contract(tmp_path):
    body = health(tmp_path)
    assert body['contract'] == 'solo-api-1'
    assert body['build'] and body['ui']
