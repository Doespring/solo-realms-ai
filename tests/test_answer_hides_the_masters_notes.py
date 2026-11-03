"""
The campaign answer must never carry what only the game master knows.

solo/prepared_world.py moves the book prose into sealed_text so the master
keeps it and the player does not. This checks the other end: the public campaign
answer, built by solo/world.py location_view(), names its fields explicitly and
must stay that way - a state copy would carry sealed_text with it.

The earlier search for the word for "key" was a false alarm: the master settings
say "klassicheskoe" and the word matched inside it. The check below looks for the
secret own phrase and for the field name.
"""
import os
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM

SECRET = 'За стеной спрятан тайный ключ.'


def campaign_with_a_secret(tmp_path):
    app = create_app(tmp_path / 's.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Проба', Hero(name='Мирон', hp=12, max_hp=12))['id']
    state = json.loads(json.dumps(store.campaign(cid)['state']))
    state['location'] = 'Башня'
    state['locations'] = [dict(name='Башня', description='Башня.', sealed_text=SECRET,
                               secrets=[SECRET], npc=[], objects=[], facts=[])]
    with store.connection(True) as c:
        c.execute('update campaigns set state=? where id=?', (json.dumps(state, ensure_ascii=False), cid))
    return TestClient(app), cid


def test_the_answer_does_not_repeat_the_masters_paragraph(tmp_path):
    api, cid = campaign_with_a_secret(tmp_path)
    body = api.get('/api/campaigns/' + cid).json()
    text = json.dumps(body, ensure_ascii=False)
    assert SECRET not in text, 'the sealed paragraph reached the player'
    assert 'спрятан' not in text


def test_the_field_itself_is_not_shipped(tmp_path):
    api, cid = campaign_with_a_secret(tmp_path)
    body = api.get('/api/campaigns/' + cid).json()
    assert 'sealed_text' not in json.dumps(body, ensure_ascii=False)
    for place in body.get('locations') or []:
        assert 'sealed_text' not in place
        assert 'secrets' not in place


def test_the_place_is_still_usable(tmp_path):
    api, cid = campaign_with_a_secret(tmp_path)
    body = api.get('/api/campaigns/' + cid).json()
    # public_campaign() nests the world under 'state'; the top level carries
    # only campaign bookkeeping. Looking at the top level finds nothing.
    places = (body.get('state') or {}).get('locations') or []
    names = [place.get('name') for place in places]
    assert 'Башня' in names, 'hiding the secret must not hide the place'
    tower = [place for place in places if place.get('name') == 'Башня'][0]
    assert tower.get('description') == 'Башня.', 'the public line stays, the prose does not'
    assert SECRET not in json.dumps(tower, ensure_ascii=False)
