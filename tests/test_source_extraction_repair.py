"""The compact source-extract path must repair reference shape itself.

A user's real book run stopped at 36/38 blocks because ``start`` and
``entities[].location`` arrived as objects. The pipeline decodes that path
through ``source_extraction.parse``, so the repair has to live there too — a
fix only in ``PreparationBatch`` is never reached for PDF runs.
"""
import json

import pytest

from solo.preparation_format import source_input
from solo.source_extraction import parse


def spans_of(text='Мост через реку.\nИрина ждёт у моста.\n' * 40):
    """Return the real span map plus its ordered identifiers."""
    rendered, spans, _ = source_input([dict(document='a' * 64, page=1, text=text)])
    return spans, list(spans)


def run(payload, spans):
    return parse(json.dumps(payload, ensure_ascii=False), spans, 'dnd2014')


def test_object_location_becomes_the_exact_matching_name():
    spans, ids = spans_of()
    batch, _ = run(dict(title='Книга', entries=[
        dict(kind='location', name='Мост', source_spans=[ids[0]], start=True),
        dict(kind='npc', name='Ирина', source_spans=[ids[1]], location={'name': 'Мост'})]), spans)
    assert batch.entities[1].location == 'Мост'


@pytest.mark.parametrize('reference', [{'name': 'Мост'}, {'key': 'Мост'}, {'id': 'Мост'}])
def test_start_given_as_a_reference_marks_the_matching_entry(reference):
    spans, ids = spans_of()
    batch, _ = run(dict(title='Книга', entries=[
        dict(kind='location', name='Мост', source_spans=[ids[0]], start=reference)]), spans)
    assert batch.start == 'Мост'


def test_null_location_is_an_absent_link_not_a_type_error():
    spans, ids = spans_of()
    batch, _ = run(dict(title='Книга', entries=[
        dict(kind='location', name='Мост', source_spans=[ids[0]], start=True),
        dict(kind='npc', name='Ирина', source_spans=[ids[1]], location=None)]), spans)
    assert batch.entities[1].location == ''


def test_an_unresolvable_reference_stays_a_visible_failure():
    from pydantic import ValidationError
    spans, ids = spans_of()
    with pytest.raises(ValidationError, match='не определена однозначно'):
        run(dict(title='Книга', entries=[
            dict(kind='location', name='Мост', source_spans=[ids[0]], start=True),
            dict(kind='npc', name='Ирина', source_spans=[ids[1]], location={'name': 'Нигде'})]), spans)


def test_plain_string_references_are_unchanged():
    spans, ids = spans_of()
    batch, changes = run(dict(title='Книга', entries=[
        dict(kind='location', name='Мост', source_spans=[ids[0]], start=True),
        dict(kind='npc', name='Ирина', source_spans=[ids[1]], location='Мост')]), spans)
    assert batch.start == 'Мост' and batch.entities[1].location == 'Мост'
    assert 'compact_source_contract' in changes


def test_no_hallucinated_entity_is_created_by_the_repair():
    spans, ids = spans_of()
    batch, _ = run(dict(title='Книга', entries=[
        dict(kind='location', name='Мост', source_spans=[ids[0]], start={'name': 'Мост'}),
        dict(kind='npc', name='Ирина', source_spans=[ids[1]], location={'name': 'Мост'})]), spans)
    assert [e.data['name'] for e in batch.entities] == ['Мост', 'Ирина']

