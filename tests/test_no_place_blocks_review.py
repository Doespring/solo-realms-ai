"""A book that yielded no place at all cannot be played.

solo/prepared_world.py:1575 marks a compile that produced no verified location
as essential, so reviewed becomes False at :1710-ish. Removing that line left
32 tests green: they compile material that always yields a location, or pass
complete=False, so the no-locations path never ran.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.adventure_library import AdventureLibrary
from solo.preparation_contracts import PreparationBatch
from solo.prepared_world import PreparedWorldValidator

DOC = 'd' * 64
TEXT = 'Здесь ничего не описано.'


def compile_without_places(tmp_path):
    library = AdventureLibrary(tmp_path)
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOC, '.txt', len(TEXT), 'ready', 1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                   (DOC, 1, '1', TEXT))
    batch = PreparationBatch(ruleset='dnd2014', start='', entities=[
        dict(key='n', kind='npc', origin='source', data=dict(name='Страж'),
             evidence=[dict(document=DOC, page=1, quote=TEXT)])])
    return PreparedWorldValidator(library, {DOC}).compile(
        [batch.model_dump()], 'dnd2014', complete=True)


def test_a_book_without_a_place_is_not_ready(tmp_path):
    result = compile_without_places(tmp_path)
    reasons = [q.get('reason', '') for q in result.get('quarantine', [])]
    assert result['ready'] is False, 'a book with no place was published'
    assert any('локаци' in r for r in reasons), reasons
