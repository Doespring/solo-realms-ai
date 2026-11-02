"""The narrator's packet carries the scene, never the place's secrets.

solo/game.py:341 builds 'atmosphere' from place['description'], where place is
the output of world.location_view() (game.py:307) - a copy that names its
fields, so secrets and sealed_text never arrive. That is two layers: the packet
builder reads only the described fields, and the view never offers the others.

Both are checked here, because either one alone still keeps the secret.
Section 4: what the party must not know stays out of the narrator's context.
"""
import os
os.environ['SOLO_TEST']='1'

import json

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM
from solo.world import location_view

SECRET = 'За стеной спрятан тайный ключ.'


def _place():
    return dict(name='Двор', description='Пустой базальтовый двор.',
                sealed_text=SECRET, secrets=[SECRET],
                npc=[], objects=[], facts=[])


def _app_and_campaign(tmp_path):
    app = create_app(tmp_path / 'p.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Пакет', Hero(name='Мирон', hp=12, max_hp=12))['id']
    state = json.loads(json.dumps(store.campaign(cid)['state']))
    state['location'] = 'Двор'
    state['locations'] = [_place()]
    with store.connection(True) as c:
        c.execute('update campaigns set state=? where id=?',
                  (json.dumps(state, ensure_ascii=False), cid))
    store.record_scenario(cid, 'look-1', 'Осматриваюсь',
                          lambda scene: [dict(type='scripted', text='Тихо.')])
    return app, store, cid


def test_the_location_view_offers_no_secret():
    view = location_view(_place())
    assert 'secrets' not in view, 'the view offered the secrets field'
    assert 'sealed_text' not in view, 'the view offered the sealed paragraph'
    assert view['description'] == 'Пустой базальтовый двор.'


def test_the_packet_does_not_carry_a_place_secret(tmp_path):
    app, store, cid = _app_and_campaign(tmp_path)
    event = store.event(store.history(cid)[-1]['id'])
    campaign = store.campaign(cid)
    campaign['state'] = event['after_state']
    campaign['version'] = 'event:' + event['id']
    _system, packet, _master = app.state.game.compiler.narrator(campaign, event)
    assert 'тайный ключ' not in packet, 'a place secret reached the narrator packet'
    assert 'базальтовый' in packet, 'the packet lost the scene itself'


def test_the_public_answer_does_not_carry_it(tmp_path):
    app, store, cid = _app_and_campaign(tmp_path)
    body = TestClient(app).get('/api/campaigns/' + cid).text
    assert 'тайный ключ' not in body
