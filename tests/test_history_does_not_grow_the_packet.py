"""The whole conversation must not be sent to the model on every turn.

Section 9.3 of the task: 'do not send the whole history to the model on every
turn'. A long campaign shows whether that holds: after fifty turns the packet
must look the same size as after one, and must still describe the current scene.

These tests read what the interpreter actually builds, not what the prompt says.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from command_smoke import setup as make_campaign


def packet_after(turns):
    store, campaign, game = make_campaign(Path(__import__('tempfile').mkdtemp()))
    for i in range(turns):
        store.converse(campaign, 'old-%d' % i, 'длинная старая реплика номер %d' % i,
                       'длинный старый ответ номер %d' % i, 'turn')
    return game.compiler.interpreter(store.campaign(campaign), 'осматриваюсь')


def test_the_packet_does_not_grow_with_history():
    one = packet_after(1)
    fifty = packet_after(50)
    assert set(one) == set(fifty), 'the same keys, whatever the history length'
    assert len(str(fifty)) < len(str(one)) * 2, 'fifty turns must not double the packet'


def test_old_turns_are_not_in_the_packet():
    packet = packet_after(50)
    text = str(packet)
    assert 'длинная старая реплика' not in text
    assert 'длинный старый ответ' not in text


def test_the_packet_still_says_where_the_hero_is():
    packet = packet_after(3)
    assert packet.get('location'), 'the model must still be told the place'
    assert 'destinations' in packet, 'and where the hero may go'
