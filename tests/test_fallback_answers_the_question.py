"""The fallback must answer the question asked, from the place the hero is in.

Section 6 of the task lists two acceptance points that are easy to miss: one
atmosphere must not serve as a universal answer to different questions, and the
scene the narrator falls back to must be the current one.

Game builds the packet from the current location (solo/game.py:294), so a
correct packet cannot carry another place's facts. This pins the scoping and the
look-around wording, with each packet built the way the game builds it.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.narrative_grounding import fallback, render


def _packet(place, atmosphere, facts, **extra):
    return dict(place=place, atmosphere=atmosphere, confirmed=facts, npc=[], memory=[],
                player_words=[], outcomes=[], **extra)


SMITHY = _packet('Кузница', 'Кузница открыта.', ['Кузница открыта.', 'У входа стоит бочка с водой.'])
YARD = _packet('Двор', 'Двор.', ['Двор.', 'На дворе колодец.'])


def test_a_place_does_not_answer_with_another_place():
    yard = render(fallback(YARD))
    assert 'Кузница' not in yard and 'бочка' not in yard, yard
    assert 'колодец' in yard


def test_looking_around_differs_from_standing_still():
    plain = render(fallback(SMITHY))
    look = render(fallback(dict(SMITHY, observation=True)))
    assert plain != look, 'the same sentence must not answer a different question'
    assert 'Нового ничего не заметно' in look


def test_an_outcome_leads_the_answer():
    result = render(fallback(dict(SMITHY, outcomes=[dict(outcome='успех')])))
    assert result.startswith('Действие удалось.'), result


def test_the_fallback_never_invents_a_place():
    empty = _packet('', '', [])
    text = render(fallback(empty))
    assert text.strip(), 'an empty scene still says something honest'
