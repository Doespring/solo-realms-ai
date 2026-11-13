"""The narrator may choose its words; it may not choose the facts.

solo/narrative_grounding.py:verify compared sentences with ===, so 'Кузница
открыта!' was rejected while 'Кузница открыта.' passed. That is punctuation, not
content, and it turned working artistic answers into engine_fallback. The audit
(reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 2) recorded it as a
contract disagreement between the transport and the host.

The check now compares what a sentence claims, never how many words it shares:
a sentence either restates a confirmed source or it is rejected.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest
from solo.narrative_grounding import CanonViolation, verify

PACKET = dict(place='Кузница',
              confirmed=['Кузница открыта.', 'У входа стоит бочка с водой.'],
              npc=[], memory=[], player_words=[], outcomes=[])


def _verify(description, dialogue=None):
    return verify(dict(description=description, dialogue=dialogue or [], proposals=[]), PACKET)


@pytest.mark.parametrize('text', [
    'Кузница открыта.',
    'Кузница открыта!',
    'Кузница открыта',
    'Кузница открыта. У входа стоит бочка с водой.',
])
def test_delivery_marks_do_not_break_a_faithful_retelling(text):
    assert _verify(text)['description'] == text


@pytest.mark.parametrize('text', [
    'Кузница разрушена.',
    'Кузница открыта! Кузница сгорела.',
    'В бочке спрятан меч.',
    'У входа стоит бочка с водой, в ней золото.',
])
def test_a_new_claim_still_fails(text):
    with pytest.raises(CanonViolation):
        _verify(text)


def test_narrator_cannot_attach_a_proposal():
    with pytest.raises(CanonViolation):
        verify(dict(description='Кузница открыта.', dialogue=[], proposals=[dict(kind='fact')]), PACKET)


def test_npc_may_restate_its_own_knowledge_only():
    packet = dict(place='Кузница', confirmed=['Кузница открыта.'], memory=[], player_words=[],
                  npc=[dict(name='Дорн', confirmed_knowledge='Мост сломан.')])
    assert verify(dict(description='', dialogue=[dict(speaker='Дорн', text='Мост сломан!')], proposals=[]), packet)
    with pytest.raises(CanonViolation):
        verify(dict(description='', dialogue=[dict(speaker='Дорн', text='В башне спрятан ключ.')], proposals=[]), packet)
