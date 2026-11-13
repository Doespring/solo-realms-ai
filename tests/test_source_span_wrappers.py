import json

import pytest

from solo.preparation_format import SourceReferenceError
from solo.source_extraction import parse


def extract(refs, spans):
    return parse(json.dumps({'entries': [{'kind': 'location', 'name': 'Мост',
        'source_spans': refs, 'start': True}]}), spans, 'dnd2024')


def test_display_wrapper_recovers_exact_source_without_changing_facts():
    source = {'document': 'a' * 64, 'page': 7, 'quote': 'Мост у реки.'}
    result, changes = extract(['[S001]', 'S001', '[S001]'], {'S001': source})
    assert result.start == 'Мост'
    assert result.entities[0].data == {'name': 'Мост'}
    assert [item.model_dump() for item in result.entities[0].evidence] == [source]
    assert changes.count('source_span_brackets_removed') == 1


def test_canonical_reference_needs_no_wrapper_repair():
    _, changes = extract(['S001'], {'S001': {'document': 'a' * 64,
        'page': 7, 'quote': 'Мост у реки.'}})
    assert 'source_span_brackets_removed' not in changes


@pytest.mark.parametrize('ref', ['[S999]', '[S1]', ' [S001]', '[[S001]]',
    '[S001] text', 'S999', '[s001]'])
def test_wrapper_never_accepts_unknown_or_malformed_ids(ref):
    with pytest.raises(SourceReferenceError):
        extract([ref], {'S001': {'document': 'a' * 64,
            'page': 7, 'quote': 'Мост у реки.'}})


def test_valid_wrapper_does_not_hide_another_unknown_reference():
    with pytest.raises(SourceReferenceError):
        extract(['[S001]', '[S002]'], {'S001': {'document': 'a' * 64,
            'page': 7, 'quote': 'Мост у реки.'}})


def test_wrapper_with_known_missing_delimiter_is_repaired_locally():
    raw = '{"entries":[{"kind":"location","name":"Мост","source_spans":["[S001]","location":""}]}'
    spans = {'S001': {'document': 'a' * 64, 'page': 7, 'quote': 'Мост у реки.'}}
    result, changes = parse(raw, spans, 'dnd2024')
    assert result.entities[0].evidence[0].quote == spans['S001']['quote']
    assert 'misclosed_source_spans_array' in changes
    with pytest.raises(SourceReferenceError):
        parse(raw.replace('[S001]', '[S999]'), spans, 'dnd2024')
    with pytest.raises(ValueError):
        parse(raw.replace('"location":""', '"execute":"code"'), spans, 'dnd2024')


def test_repeated_known_delimiter_error_still_validates_every_entry():
    entry = '{"kind":"location","name":"Мост","source_spans":["[S001]","location":""}'
    raw = '{"entries":[' + entry + ',' + entry + ']}'
    spans = {'S001': {'document': 'a' * 64, 'page': 7, 'quote': 'Мост у реки.'}}
    result, _ = parse(raw, spans, 'dnd2024')
    assert len(result.entities) == 2
    with pytest.raises(ValueError):
        parse(raw.replace('"location":""', '"location":"","location":""'), spans, 'dnd2024')
