"""A page with no words and no picture is a gap, not a blank.

solo/preparation_pipeline.py leaves a wordless page out of every batch, so
nothing on it can be quoted. Two things never happened before: the page was not
counted anywhere, and 'complete' stayed True even when the unreadable page could
have held the very scene the adventure needs.

A page that carries a map or a handout is different: the picture explains the
silence, and the material is not missing.
"""
import os
os.environ['SOLO_TEST'] = '1'

import json
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from solo.adventure_library import AdventureLibrary
from solo.preparation_pipeline import PreparationPipeline

COMMON = dict(mechanics='requires_review', media_scope='gm', ocr=False, partial_pages=[])


@contextmanager
def pipeline_with(quality):
    """Run the real source_batches against a real one-document library.

    An earlier version of these tests re-implemented the rule in Python and
    checked that copy. That proves nothing about the pipeline: a wrong edit in
    solo/preparation_pipeline.py would leave the copy passing. The pipeline is
    what is under test here, so the pipeline is what runs.
    """
    folder = tempfile.mkdtemp()
    library = AdventureLibrary(folder)
    with library.db() as db:
        db.execute("INSERT INTO documents(id,format,bytes,status,quality) VALUES('doc','text',1,'ready',?)",
                   (json.dumps(quality),))
        for number, text in ((1, 'Кузница открыта.'), (2, ''), (3, 'Двор чист.')):
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       ('doc', number, str(number), text))
    pipeline = PreparationPipeline.__new__(PreparationPipeline)
    pipeline.library = library
    try:
        yield list(PreparationPipeline.source_batches.__get__(pipeline, PreparationPipeline)({'doc'}))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def verdict(quality):
    with pipeline_with(quality) as batches:
        return all(done for _pages, done in batches)


def test_a_silent_page_makes_the_book_incomplete():
    quality = dict(COMMON, blank_pages_list=[2], pictured_pages=[])
    assert verdict(quality) is False


def test_a_page_with_a_picture_explains_its_silence():
    quality = dict(COMMON, blank_pages_list=[2], pictured_pages=[2])
    assert verdict(quality) is True


def test_only_unexplained_pages_count():
    quality = dict(COMMON, blank_pages_list=[2, 5], pictured_pages=[2])
    assert verdict(quality) is False, 'page 5 is still silent'


def test_a_readable_book_stays_complete():
    assert verdict(dict(COMMON, blank_pages_list=[], pictured_pages=[])) is True


def test_the_worker_records_which_pages_are_silent():
    source = (Path(__file__).resolve().parents[1] / 'solo' / 'adventure_worker.py').read_text(encoding='utf-8')
    assert 'blank_pages.append(index+1)' in source
    assert 'pictured_pages.append(index+1)' in source
    assert 'blank_pages_list=blank_pages' in source.replace(' ', '')
