"""A material that names no level asks; it does not refuse the table.

Section 8: an unknown level, party size or opening must become a question in
dialogue, not a blanket veto. The rule already exists; these tests keep it from
being "simplified" back into a refusal.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.open_settings import next_question, question_text


def _prep(questions, answers=None):
    return {'preparation': {'open_questions': questions, 'answers': answers or {}}}


def test_a_missing_level_becomes_a_question():
    state = _prep([{'field': 'level', 'question': 'С какого уровня начинаем?'}])
    entry = next_question(state)
    assert entry and entry['field'] == 'level'
    assert 'уровня' in question_text(entry)


def test_an_answered_question_is_not_asked_again():
    state = _prep([{'field': 'level', 'question': 'С какого уровня начинаем?'}], {'level': 3})
    assert next_question(state) is None


def test_several_gaps_are_asked_one_at_a_time():
    state = _prep([{'field': 'level', 'question': 'С какого уровня начинаем?'},
                   {'field': 'party_size', 'question': 'Сколько героев за столом?'}])
    first = next_question(state)
    assert first['field'] == 'level', 'questions must not all arrive at once'
    state['preparation']['answers']['level'] = 1
    assert next_question(state)['field'] == 'party_size'


def test_nothing_missing_means_no_prompt():
    assert next_question(_prep([])) is None
