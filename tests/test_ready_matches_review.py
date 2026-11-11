"""A world that was not reviewed must not be reported as ready to launch.

solo/prepared_world.py:1717 returns ready=definition.reviewed. Setting ready=True
left two of the three quarantine tests green: they assert on reviewed, or on a
path that also passes when everything is ready. Section 8 asks that unreviewed
material never reach play as if it had been checked.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.adventure_library import AdventureLibrary
from solo.preparation_contracts import PreparationBatch
from solo.prepared_world import PreparedWorldValidator

DOC = 'e' * 64
TEXT = 'Зал. Здесь пусто.'


def _compile(tmp_path, *, complete, start='Зал'):
    library = AdventureLibrary(tmp_path)
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOC, '.txt', len(TEXT), 'ready', 1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                   (DOC, 1, '1', TEXT))
    batch = PreparationBatch(ruleset='dnd2014', start=start, entities=[
        dict(key='a', kind='location', origin='source',
             data=dict(name='Зал', description=TEXT),
             evidence=[dict(document=DOC, page=1, quote=TEXT)])])
    return PreparedWorldValidator(library, {DOC}).compile(
        [batch.model_dump()], 'dnd2014', complete=complete)


def test_unreviewed_material_is_not_ready(tmp_path):
    result = _compile(tmp_path, complete=False)
    assert result['ready'] is False, 'unreviewed material is offered for play'
    assert result['definition']['reviewed'] is False


def test_reviewed_material_is_ready(tmp_path):
    result = _compile(tmp_path, complete=True)
    assert result['ready'] is True, str(result.get('quarantine'))[:200]
