"""A narrator sentence that names something the scene cannot support must not pass.

solo/narrative_grounding.py:_known decides whether a claim restates a confirmed
source. Replacing it with 'return True' left every narrator test green: they feed
hand-built structures to verify, and none plays a turn where the model says
something new. solo/game.py:750 calls verify and, on failure, replaces the text
with fallback (game.py:755-757).
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.narrative_grounding import CanonViolation, verify

# Shaped like the packet the narrator really receives (see sources()):
# 'confirmed' and 'atmosphere' are the only description evidence.
PACKET = dict(confirmed=['Комната пуста.'], atmosphere='', outcomes=[], npc=[],
              objects=[], object_states=[], place='Комната')


def test_a_new_claim_is_rejected():
    with pytest.raises(CanonViolation):
        verify(dict(description='Дракон спит в углу.', dialogue=[], proposals=[]), PACKET)


def test_a_restated_source_passes():
    verify(dict(description='Комната пуста.', dialogue=[], proposals=[]), PACKET)


def test_a_proposal_is_rejected():
    with pytest.raises(CanonViolation):
        verify(dict(description='Комната пуста.', dialogue=[], proposals=[{'kind': 'npc'}]), PACKET)
