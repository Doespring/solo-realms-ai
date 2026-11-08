"""The control book, checked against facts fixed before the extractor ran.

The expected facts live in tests/fixtures/control-book-expected.json and no
production module reads that file: the assertions compare the extractor's own
output with a record written by hand. The book itself is built here, so the test
proves the pipeline on a source it has never seen, not on a stored result.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from solo.adventure_library import AdventureLibrary
from solo.pdf_pipeline import PDFPipeline

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures.control_book import build as build_control_book  # noqa: E402

EXPECTED = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'control-book-expected.json')
                      .read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def book(tmp_path_factory):
    from solo.adventure_worker import run

    root = tmp_path_factory.mktemp('control-book')
    path = root / 'lantern-quarry.pdf'
    build_control_book(path)
    library = AdventureLibrary(root)
    bundle = library.create('The Lantern Quarry')['id']
    job = library.enqueue(bundle, path, 'lantern-quarry.pdf', 'book')
    run(library.data, job['id'])
    assert library.job(job['id'])['status'] == 'complete', library.job(job['id'])
    document = job['document']
    PDFPipeline(library).index(document)
    return dict(root=root, library=library, document=document, path=path, bundle=bundle)


def pages(book):
    with book['library'].db() as db:
        return {row['number']: row['content'] for row in
                db.execute('SELECT number,content FROM pages WHERE document=?', (book['document'],))}


def flat(text):
    """Printed line breaks are layout, not wording: compare on single spaces."""
    return ' '.join(text.split())


def test_the_book_is_the_one_the_expectations_describe(book):
    text = pages(book)
    assert len(text) == EXPECTED['pages']
    for place in EXPECTED['places']:
        page = flat(text[place['page']])
        for fragment in place['must_contain']:
            assert flat(fragment) in page, (place['name'], fragment)


def test_the_scanned_page_has_no_text_and_is_reported_not_invented(book):
    text = pages(book)
    scan = EXPECTED['scan_page']
    assert text[scan['page']].strip() == ''
    summary = PDFPipeline(book['library']).summary(book['document'])
    assert scan['page'] in summary['ocr_pages']
    # The image exists; the pipeline says so instead of writing text it never read.
    page = PDFPipeline(book['library']).page(book['document'], scan['page'])
    assert page['images'] and page['needs_ocr']


def test_two_column_page_is_indexed_with_geometry_on_both_sides(book):
    layout = PDFPipeline(book['library']).page(book['document'], EXPECTED['columns']['page'])
    left = [r for r in layout['regions'] if r['bounds'] and r['bounds'][0][0] < 300]
    right = [r for r in layout['regions'] if r['bounds'] and r['bounds'][0][0] >= 300]
    assert left and right, 'both columns must carry coordinates'


def test_table_map_and_handout_are_recognised_as_such(book):
    pipeline = PDFPipeline(book['library'])
    for key in ('table', 'map', 'handout'):
        page = EXPECTED[key]['page']
        layout = pipeline.page(book['document'], page)
        kinds = [hint['kind'] for hint in layout['hints']]
        assert key in kinds, (key, kinds)


def test_cross_page_references_survive_as_printed(book):
    text = pages(book)
    for reference in EXPECTED['cross_references']:
        assert flat(reference['text']) in flat(text[reference['from_page']])


def test_no_source_internal_marker_reaches_the_reader(book):
    text = pages(book)
    joined = ' '.join(text.values())
    for forbidden in EXPECTED['forbidden']:
        assert forbidden not in joined
