"""Messages about a taken request id must read as Russian.

An earlier repair replaced every service identifier with an adverb; that fixed
"Сейчас нельзя: это действие" but turned "Этот request_id уже использован" into
"Этот сейчас недоступно уже использован". Identifiers that name something the
player made keep their name.
"""
import os
os.environ['SOLO_TEST']='1'

import asyncio

from scripts.command_smoke import FixedDice, setup


def play(game, campaign, request_id, text):
    version = game.store.campaign(campaign)['version']
    return asyncio.run(game.play_command(campaign, request_id, text, version, rng=FixedDice()))


def test_taken_request_id_reads_as_russian(tmp_path):
    store, campaign, game = setup(tmp_path)
    assert play(game, campaign, 'req-1', 'Исследую следы')['status'] == 'resolved'
    clash = play(game, campaign, 'req-1', 'Добавляю базовый бой')
    assert clash['status'] == 'rejected', clash
    message = clash['narrative']
    assert 'request_id' not in message, message
    assert 'недоступно' not in message, 'a name is not a reason'
    assert 'запрос' in message, message


def test_the_reason_wording_still_holds(tmp_path):
    store, campaign, game = setup(tmp_path)
    fight = play(game, campaign, 'req-x', 'Добавляю базовый бой')
    message = fight.get('narrative') or ''
    assert 'это действие' not in message, 'the old mechanical substitute is gone'
    assert 'request_id' not in message
