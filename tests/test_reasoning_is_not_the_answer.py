"""Reasoning blocks are never shown as the answer."""
from solo.visible_content import text


def test_a_reasoning_block_is_not_visible_text():
    answer = text([dict(type='reasoning', text='Сначала осмотрит колодец.'),
                   dict(type='text', text='Вы стоите у колодца.')])
    assert answer == 'Вы стоите у колодца.'


def test_only_the_answer_block_is_joined():
    answer = text([dict(type='text', text='A'), dict(type='output_text', text='B'),
                   dict(type='reasoning', text='C')])
    assert answer == 'AB'


def test_a_plain_string_passes_through():
    assert text('Просто текст.') == 'Просто текст.'