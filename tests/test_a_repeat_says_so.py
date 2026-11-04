"""A repeated turn must look like a repeat, not like a second action.

Section 7.2 of the task: when the answer is lost and the player repeats the move,
the same result comes back and the action is not performed twice. The screen can
only say so if the reply identifies the turn - the same event id.

The critic noticed that 'replayed' and 'moved_on' are not read by web/ at all.
The reply on this path carries no replayed flag, so the screen detects the
repeat by the event id it already showed. These tests pin the server side of
that contract.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from fastapi.testclient import TestClient
from command_smoke import setup as make_campaign

from solo.app import create_app
from test_dialogue_paging_api import MockLLM


def client(tmp_path):
    store, campaign, _game = make_campaign(tmp_path)
    return TestClient(create_app(store.path, MockLLM())), campaign


def play(api, campaign, request_id, text='осматриваюсь'):
    return api.post(f'/api/campaigns/{campaign}/play',
                    json={'request_id': request_id, 'text': text, 'selection': None})


def test_a_repeated_turn_comes_back_as_the_same_event(tmp_path):
    api, campaign = client(tmp_path)
    first = play(api, campaign, 'look-1')
    assert first.status_code == 200, first.text
    again = play(api, campaign, 'look-1')
    assert again.status_code == 200, again.text
    one, two = first.json(), again.json()
    assert one['event']['id'] == two['event']['id'], 'the repeat must point at the turn already played'


def test_a_repeat_adds_no_second_turn(tmp_path):
    api, campaign = client(tmp_path)
    play(api, campaign, 'look-2')
    before = len(api.get(f'/api/campaigns/{campaign}/conversation').json()['conversation'])
    play(api, campaign, 'look-2')
    after = len(api.get(f'/api/campaigns/{campaign}/conversation').json()['conversation'])
    assert after == before, 'a repeat must not append a turn'


def test_a_different_request_is_a_new_turn(tmp_path):
    api, campaign = client(tmp_path)
    one = play(api, campaign, 'look-3').json()
    two = play(api, campaign, 'look-4').json()
    assert one['event']['id'] != two['event']['id'], 'a new request is a new turn'
