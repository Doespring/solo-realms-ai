"""Engine identifiers never reach the player, and the line is tidy."""
import os
os.environ['SOLO_TEST']='1'

from solo.game import player_text


def test_a_stripped_identifier_leaves_no_double_space():
    shown = player_text('Сейчас нельзя: cast_spell.')
    assert '  ' not in shown
    assert shown == shown.strip()


def test_the_line_is_trimmed_at_the_edges():
    assert player_text('   Мост рухнул.   ') == 'Мост рухнул.'


def test_a_final_comma_survives():
    assert player_text('Мост рухнул,') == 'Мост рухнул,'