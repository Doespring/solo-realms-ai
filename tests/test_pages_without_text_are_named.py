"""A page with no text must be reported, not silently skipped or sent empty.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 8) noted that
solo/preparation_pipeline.py, still the ordinary path from creation.py, skips an
empty page without saying so. Passing an empty span to the model asks it to find
evidence in nothing, and the incompleteness of the material never surfaces.

The correction keeps the pages that do have text, leaves the empty ones out of
the request, and names them so the caller can report the gap.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.preparation_format import source_input

PAGES = [
    dict(document='книга', page=1, text='Кузница открыта.'),
    dict(document='книга', page=2, text=''),
    dict(document='книга', page=3, text='   \n  '),
    dict(document='книга', page=4, text='У входа стоит бочка с водой.'),
]


def test_only_pages_with_text_reach_the_model():
    rendered, spans, _ = source_input(PAGES)
    assert [p['page'] for p in rendered] == [1, 4]
    assert len(spans) == 2
    quotes = ' '.join(span['quote'] for span in spans.values())
    assert 'бочка' in quotes


def test_the_skipped_pages_are_named():
    rendered, _, _ = source_input(PAGES)
    skipped = rendered[0]['skipped_pages']
    assert [item['page'] for item in skipped] == [2, 3]
    assert all(item['reason'] for item in skipped), 'a gap without a reason is a silent gap'


def test_a_fully_readable_document_loses_nothing():
    rendered, spans, _ = source_input([dict(document='d', page=i, text='строка %d' % i) for i in range(1, 6)])
    assert len(rendered) == 5 and len(spans) == 5
    assert rendered[0]['skipped_pages'] == []


def test_everything_empty_still_yields_an_honest_answer():
    rendered, spans, _ = source_input([dict(document='d', page=1, text='')])
    assert rendered == [] and spans == {}


def test_a_long_page_is_split_without_losing_text():
    long = ('предложение. ' * 400).strip()
    rendered, spans, _ = source_input([dict(document='d', page=1, text=long)])
    assert len(spans) > 1, 'a long page is split into spans'
    joined = ''.join(span['quote'] for span in spans.values())
    assert joined == long, 'splitting must not drop characters'
