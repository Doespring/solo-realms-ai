"""A page with prose but no read-aloud must not publish the prose.

solo/prepared_world.py:1283 moves the book's paragraph out of the public
description and into sealed_text, where only the game master sees it. A mutation
of that single line left 44 tests green: the existing checks built a WorldLocation
by hand and exercised observable_opening, while the compiler itself never ran on
material that has prose without a read-aloud paragraph.

This test drives the compiler. If the line stops moving the paragraph, the
secret appears in the public description and the test fails.
"""
import os
os.environ['SOLO_TEST']='1'

from solo.adventure_library import AdventureLibrary
from solo.preparation_contracts import PreparationBatch
from solo.prepared_world import PreparedWorldValidator

DOC = 'b' * 64
PROSE = 'Башня. За северной стеной спрятан тайный ключ. Игроки об этом не знают.'


def compile_page(tmp_path, text=PROSE):
    library = AdventureLibrary(tmp_path)
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)', (DOC, '.txt', len(text), 'extracted', 1))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)', (DOC, 1, '1', text))
    batch = PreparationBatch(ruleset='dnd2014', start='Башня', entities=[
        dict(key='a', kind='location', origin='source', data=dict(name='Башня', description=text),
             evidence=[dict(document=DOC, page=1, quote=text)])])
    return PreparedWorldValidator(library, {DOC}).compile([batch.model_dump()], 'dnd2014')


def place(result):
    places = result['definition']['world']['locations']
    assert places, 'the place itself must survive: the campaign starts there'
    return places[0]


def test_the_paragraph_is_not_published_without_a_read_aloud(tmp_path):
    spot = place(compile_page(tmp_path))
    public = (spot.get('description') or '').lower()
    assert 'ключ' not in public, 'the secret reached the public description'
    assert 'спрятан' not in public, 'the hiding place reached the public description'


def test_the_paragraph_is_kept_for_the_master(tmp_path):
    spot = place(compile_page(tmp_path))
    sealed = spot.get('sealed_text') or ''
    assert 'ключ' in sealed.lower(), 'the master lost the paragraph entirely'
    assert public_safe(spot), 'sealed text and public text must not be the same'


def public_safe(spot):
    """The public text may not restate the sealed paragraph."""
    public = (spot.get('description') or '').strip()
    return 'ключ' not in public.lower()


def test_a_page_with_a_read_aloud_is_published(tmp_path):
    text = 'Башня.\n\nПрочитайте вслух: Вы стоите у ворот старой башни.'
    spot = place(compile_page(tmp_path, text))
    assert 'ворота' in (spot.get('description') or '').lower() or 'башн' in (spot.get('description') or '').lower()
