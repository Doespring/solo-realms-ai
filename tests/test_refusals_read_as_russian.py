"""A refusal must read as Russian, not as a hole where an identifier was.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 7.2) found
player text like 'с новым это действие'. The cause was solo/game.py's
player_text(): it strips engine identifiers, then put the literal words
'это действие' in their place. That produced text which was neither Russian nor
true - 'Сейчас нельзя: это действие', 'нет условий для это действие' - and left
the separator behind when the identifier followed a colon.

The substitution now names what the player experiences, and never decides the
case of a noun the surrounding sentence did not give.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.game import player_text

REFUSALS = [
    'Сейчас нельзя: narrative_or_unsupported_in_combat',
    'Сейчас нельзя: narrative_or_unsupported_in_combat.',
    'Ход не принят: attacks_remaining: 0; допустим только cast_spell',
    'Этот ход отменён: нет условий для move_token',
]


@pytest.mark.parametrize('text', REFUSALS)
def test_a_refusal_never_shows_a_hole(text):
    cleaned = player_text(text)
    assert cleaned, 'a refusal must still say something'
    for artefact in ('это действие', 'cast_spell', 'move_token', 'narrative_or', 'attacks_remaining'):
        assert artefact not in cleaned, f'{artefact!r} reached the player in {cleaned!r}'
    assert not cleaned.endswith((': ', ':', ';', ',')), f'dangling separator in {cleaned!r}'


@pytest.mark.parametrize('text', REFUSALS)
def test_a_refusal_does_not_repeat_itself(text):
    cleaned = player_text(text)
    lowered = cleaned.lower()
    for word in ('нельзя', 'недоступно', 'недопустим', 'невыполнимо'):
        assert lowered.count(word) <= 1, f'{word!r} said twice in {cleaned!r}'


def test_the_same_words_do_not_stand_in_for_every_identifier():
    """One phrase for all reasons would be the 'universal answer' defect again."""
    answers = {player_text(text) for text in REFUSALS}
    assert len(answers) >= 2, f'every refusal collapsed to one sentence: {answers}'


@pytest.mark.parametrize('text', [
    'Мост рухнул,',
    'Вы ударили: 7 урона.',
    'Кузница открыта. У входа бочка.',
    '— Нового ничего не заметно.',
])
def test_ordinary_narration_is_untouched(text):
    assert player_text(text) == text
