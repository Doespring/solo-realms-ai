"""A fallen hero may not act through the plain action route."""
import os
os.environ['SOLO_TEST']='1'

import asyncio
import json
import sys
from pathlib import Path

import pytest

from solo.app import ActionRequest
from solo.contracts import Plan, Step

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def _route(game, cid, request_id):
    plan = Plan(summary='Осмотреться', steps=[Step(kind='observe', text='Исследую следы')],
                needs_confirmation=False)
    game.llm.plan = plan
    return asyncio.run(game.route(cid, ActionRequest(request_id=request_id, text="Исследую следы")))


def test_a_fallen_hero_cannot_take_a_turn(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'fallen')
    state = store.campaign(cid)['state']
    state['hero']['hp'] = 0
    state['hero_death'] = True
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (json.dumps(state, ensure_ascii=False), cid))

    try:
        _route(game, cid, 'dead-1')
    except ValueError as exc:
        assert 'не может действовать' in str(exc), 'refused for another reason: %s' % exc
    else:
        raise AssertionError('a fallen hero took a turn')