"""The narrator may restate the packet, not vary it.

solo/narrative_grounding.py:104 _known() compares the whole reduced sentence
with a source sentence. Swapping that equality for 'the first word appears in
some source' left six tests green: each one invents a sentence that shares no
opening word with the packet, so both rules reject it.

A narrator drifting one noun from the source is the realistic failure - 'ключ'
becomes 'сундук' - and it must still be refused. Section 6.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.narrative_grounding import CanonViolation, verify

PACKET = {'atmosphere': 'В зале пахнет пылью.',
          'confirmed': ['На столе лежит ржавый ключ.'],
          'outcomes': [], 'npc': [], 'memory': [], 'object_states': []}


def _description(text):
    return {'description': text, 'dialogue': [], 'proposals': []}


def test_a_restatement_passes():
    verify(_description('На столе лежит ржавый ключ.'), PACKET)


def test_one_changed_word_is_refused():
    with pytest.raises(CanonViolation):
        verify(_description('На столе лежит ржавый сундук.'), PACKET)


def test_a_trailing_addition_is_refused():
    with pytest.raises(CanonViolation):
        verify(_description('На столе лежит ржавый ключ и свиток.'), PACKET)
