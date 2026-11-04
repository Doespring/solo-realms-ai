"""A book with an unreadable page must not be published as reviewed.

solo/prepared_world.py:1568 marks a source with a silent page as an essential
quarantine entry, and :1710 turns any essential entry into reviewed=False. The
first mutation - removing only :1568 - left 32 tests green, because the pipeline
tests check the 'complete' flag rather than the compiler's verdict on it.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.adventure_library import AdventureLibrary
from solo.preparation_contracts import PreparationBatch
from solo.prepared_world import PreparedWorldValidator

DOC = 'c' * 64
TEXT = 'Башня. Внутри пусто.'


def compile_with(tmp_path, *, complete):
    library = AdventureLibrary(tmp_path)
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOC, '.txt', len(TEXT), 'ready', 1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                   (DOC, 1, '1', TEXT))
    batch = PreparationBatch(ruleset='dnd2014', start='Башня', entities=[
        dict(key='a', kind='location', origin='source',
             data=dict(name='Башня', description=TEXT),
             evidence=[dict(document=DOC, page=1, quote=TEXT)])])
    return PreparedWorldValidator(library, {DOC}).compile(
        [batch.model_dump()], 'dnd2014', complete=complete)


def test_a_silent_page_blocks_review(tmp_path):
    result = compile_with(tmp_path, complete=False)
    assert result['ready'] is False, 'a book with a silent page was published'


def test_a_complete_source_still_passes(tmp_path):
    result = compile_with(tmp_path, complete=True)
    assert result['ready'] is True, str(result.get('quarantine'))[:200]
