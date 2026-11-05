"""During a fight the plain action route must refuse, exactly as it says."""
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


def test_a_fight_takes_its_own_route(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'fight')
    state = store.campaign(cid)['state']
    state['combat'] = {'active': True}
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (json.dumps(state, ensure_ascii=False), cid))

    game.llm.plan = Plan(summary='Осмотреться',
                         steps=[Step(kind='observe', text='Исследую следы')],
                         needs_confirmation=False)
    with pytest.raises(ValueError) as caught:
        asyncio.run(game.route(cid, ActionRequest(
            request_id="fight-1", text="Исследую следы")))
    assert 'В бою' in str(caught.value), 'refused for another reason: %s' % caught.value