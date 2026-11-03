"""An NPC may say only what that NPC knows, not what the scene knows.

solo/narrative_grounding.py:116 looks the speaker up in
allowed['dialogue_sources']. Replacing that lookup with the scene's
description_sources left nine tests green: they never put a fact in the packet
that one NPC knows and another does not.

This is the whole point of per-speaker knowledge: the tavern keeper has heard
the rumour, the guard has not, and the guard must not repeat it. Section 6.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.narrative_grounding import CanonViolation, verify

PACKET = {'atmosphere': 'В зале пахнет пылью.',
          'confirmed': ['На столе лежит ржавый ключ.'],
          'outcomes': [],
          'npc': [{'name': 'Кузнец', 'confirmed_knowledge': 'Хозяин уехал на закате.'},
                  {'name': 'Стражник', 'confirmed_knowledge': 'Ночью будет дождь.'}],
          'memory': [], 'object_states': []}


def _say(speaker, text):
    return {'description': '', 'dialogue': [{'speaker': speaker, 'text': text}],
            'proposals': []}


def test_an_npc_may_state_what_he_knows():
    verify(_say('Кузнец', 'Хозяин уехал на закате.'), PACKET)


def test_an_npc_may_not_borrow_another_npcs_fact():
    with pytest.raises(CanonViolation):
        verify(_say('Стражник', 'Хозяин уехал на закате.'), PACKET)


def test_an_unknown_speaker_is_refused():
    with pytest.raises(CanonViolation):
        verify(_say('Незнакомец', 'Ночью будет дождь.'), PACKET)
