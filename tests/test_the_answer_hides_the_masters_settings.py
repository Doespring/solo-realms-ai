"""The campaign answer must not carry the master's own settings.

solo/app.py spelled the campaign record out with **public_campaign(current),
which copies the state only - but the record also has a 'master' column, and
dict(**...) passed it through whole. system_prompt, boundaries and
instructions are the owner's prose; boundaries name what must not be
described, so reading them is a hint at every secret. Section 4.

The table needs exactly one flag from them: whether mechanics are hidden.
"""
import os
os.environ['SOLO_TEST']='1'

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero, Master
from solo.llm import MockLLM

PROMPT = 'ЛИЧНАЯ СТРОКА МАСТЕРА'
BOUNDARY = 'НЕЛЬЗЯ ОПИСЫВАТЬ ТАЙНИК'


def _campaign(tmp_path, **settings):
    app = create_app(tmp_path / 'm.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Проба', Hero(name='Мирон', hp=12, max_hp=12))['id']
    if settings:
        store.master(cid, Master(**settings))
    return TestClient(app).get('/api/campaigns/%s' % cid).json()


def test_the_answer_hides_the_masters_prompt(tmp_path):
    body = _campaign(tmp_path, system_prompt=PROMPT)
    assert PROMPT not in str(body), 'the master prompt reached the player'


def test_the_answer_hides_the_boundaries(tmp_path):
    body = _campaign(tmp_path, boundaries=BOUNDARY)
    assert BOUNDARY not in str(body), 'the boundaries reached the player'


def test_the_campaign_list_hides_the_master_too(tmp_path):
    app = create_app(tmp_path / 'list.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Проба', Hero(name='Мирон', hp=12, max_hp=12))['id']
    store.master(cid, Master(system_prompt=PROMPT, boundaries=BOUNDARY))
    rows = TestClient(app).get('/api/campaigns').json()
    blob = str(rows)
    assert PROMPT not in blob, 'the campaign list carried the master prompt'
    assert BOUNDARY not in blob, 'the campaign list carried the boundaries'


def test_the_answer_keeps_the_one_flag_the_table_needs(tmp_path):
    body = _campaign(tmp_path, hide_mechanics=True)
    assert body['master'] == {'hide_mechanics': True}, body['master']
