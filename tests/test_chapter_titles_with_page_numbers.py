"""A book that prints its folio in the running head still cuts into chapters.

«Глава 1 | Карнавал Сумеречного света 23» is the same chapter on page 24 as on
page 56; only the printed page number changes. The book that showed this
(Witchlight, 256 pages) imported with zero modules and had to be cut by hand,
because its five appendices are printed as «ПРИЛОЖЕНИЕ А» and
«Приложение А | Магические предметы 209» — a letter after the word, not a colon —
and because the player's own draft never asked the importer to propose ranges.

These tests pin the three shapes that must not become a chapter (a prose
sentence, a credits line, a table of contents), the ten boundaries the book
really has, and the whole import path through a real PDF.
"""
import hashlib, io, json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from solo.adventure_library import (AUTO_SPLIT_KEY, AdventureLibrary, chapter_key,
                                    chapter_starts, module_spans)
from solo.adventure_worker import run
from solo.creation import CreationService
from solo.preparation import Preparation
from solo.storage import Store

#: Where the ten parts of the book really begin, as its own printed heads say.
PARTS = [
    (24, 57, 'Глава 1 | Карнавал Сумеречного света'),
    (58, 99, 'Глава 2 | Тута'),
    (100, 133, 'Глава 3 | Здеся'),
    (134, 171, 'Глава 4 | Тама'),
    (172, 208, 'Глава 5 | Дворец сокровенных желаний'),
    (209, 215, 'Приложение А | Магические предметы'),
    (216, 229, 'Приложение B | Фракции'),
    (230, 242, 'Приложение C | Существа'),
    (243, 250, 'Приложение D | Карточки для отыгрыша'),
    (251, 256, 'Приложение E | Реплики Глоссофобия'),
]


def header_pdf(lines):
    """A real PDF whose pages print one header line each, as a book does."""
    writer = PdfWriter()
    for line in lines:
        page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject(
            {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 30 750 Td (' + line + ') Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class Jobs:
    def submit(self, id):
        return None


def book_rows():
    """The candidates the extractor writes for a book shaped like this one.

    The printed page number follows each head («… света 23» on page 24), so the
    title differs on every page while the chapter stays the same. The heading
    itself stands alone on the chapter's first page; the running head repeats on
    the even pages after it, which is where the folio is printed.
    """
    rows = [(4, 'chapter', 'Глава 1: Карнавал')]
    rows += [(4, 'chapter', 'Глава %d: %s%s' % (n, title.split('| ')[1], '.' * 20 + str(first + 30)))
             for n, (first, _last, title) in enumerate(PARTS[:5], 1)]
    for first, last, title in PARTS:
        head = title.split(' | ')[0].upper()
        rows.append((first, 'chapter', head))
        for page in range(first, last + 1):
            if page % 2 == 0 and page != first:
                rows.append((page, 'chapter', title + ' ' + str(page - 1)))
    # Prose on the way: a sentence that mentions an appendix, and a credits line.
    rows.append((18, 'chapter', 'приложение A). Персонаж вновь сможет улыбаться, когда впервые'))
    rows.append((43, 'chapter', 'приложение А) с изображением двух листочков,'))
    rows.append((203, 'chapter', 'приложение С)'))
    return sorted(rows)


def test_a_printed_page_number_in_the_head_does_not_cut_a_chapter():
    assert chapter_key('Глава 1 | Карнавал Сумеречного света 23') == ('глава', '1')
    assert chapter_key('Глава 1 | Карнавал Сумеречного света 55') == ('глава', '1')
    rows = [(page, 'chapter', 'Глава 1 | Карнавал Сумеречного света %d' % (page - 1))
            for page in (24, 26, 28, 30)]
    rows += [(58, 'chapter', 'Глава 2 | Тута 57')]
    starts = chapter_starts(rows)
    assert [page for page, _title in starts] == [24, 58]
    # The folio is stripped from the name a player sees, so the module is not
    # «Глава 1 | Карнавал Сумеречного света 29».
    assert starts[0][1] == 'Глава 1 | Карнавал Сумеречного света'


def test_a_prose_line_and_a_credits_line_are_not_chapter_headings():
    assert chapter_key('приложение A). Персонаж вновь сможет улыбаться') is None
    assert chapter_key('приложение С)') is None
    # A credits line names the translators who worked on the appendix.
    assert chapter_key('Приложение А - korfax, Kestel') is None
    # …while the heading itself, the running head and the colon form all name it.
    assert chapter_key('ПРИЛОЖЕНИЕ А') == ('приложение', 'а')
    assert chapter_key('Приложение А | Магические предметы 209') == ('приложение', 'а')
    assert chapter_key('Приложение А: Монстры') == ('приложение', 'а')


def test_a_prose_mention_alone_never_starts_a_chapter():
    rows = [(18, 'chapter', 'приложение A). Персонаж вновь сможет улыбаться, когда впервые'),
            (203, 'chapter', 'приложение С)'),
            (209, 'chapter', 'ПРИЛОЖЕНИЕ А'),
            (210, 'chapter', 'Приложение А | Магические предметы 209'),
            (212, 'chapter', 'Приложение А | Магические предметы 211')]
    assert chapter_starts(rows) == [(209, 'Приложение А | Магические предметы')]


def test_the_descriptive_head_names_an_appendix_even_when_the_folio_breaks_the_run():
    """Appendix E lasts five pages; its head is printed four pages apart.

    The run of its own pages is [251, 252], so both printed forms occur once in
    it, and the bare «ПРИЛОЖЕНИЕ E» is the shorter name. The module is still
    called by the head a player recognises.
    """
    rows = [(251, 'chapter', 'ПРИЛОЖЕНИЕ E'),
            (252, 'chapter', 'Приложение E | Реплики Глоссофобия 251'),
            (256, 'chapter', 'Приложение E | Реплики Глоссофобия 255')]
    assert chapter_starts(rows) == [(251, 'Приложение E | Реплики Глоссофобия')]


def test_a_table_of_contents_page_does_not_start_a_chapter():
    rows = [(4, 'chapter', 'Глава 1: Карнавал'),
            (4, 'chapter', 'Глава 2: Тута.............................57'),
            (4, 'chapter', 'Глава 3: Здеся...........................99'),
            (24, 'chapter', 'Глава 1 | Карнавал Сумеречного света 23'),
            (26, 'chapter', 'Глава 1 | Карнавал Сумеречного света 25'),
            (58, 'chapter', 'Глава 2 | Тута 57'),
            (60, 'chapter', 'Глава 2 | Тута 59')]
    assert [page for page, _title in chapter_starts(rows)] == [24, 58]


def test_the_book_has_ten_boundaries_and_ten_modules():
    starts = chapter_starts(book_rows())
    assert [page for page, _title in starts] == [first for first, _last, _t in PARTS]
    spans = module_spans(starts, 256)
    assert [(first, last) for first, last, _title in spans] == [(first, last) for first, last, _t in PARTS]
    assert [title for _f, _l, title in spans] == [title for _f, _l, title in PARTS]


def test_a_book_uploaded_into_a_player_draft_is_cut_without_a_person(tmp_path):
    """The player's own draft is a whole book: the importer proposes its ranges.

    This is the defect the card was written for — the bundle a draft creates
    carried no `auto_split`, so ``auto_split_book`` returned before touching the
    book and the player was left with «Материалы извлечены» and zero modules.
    """
    store = Store(tmp_path / 'solo.sqlite3')
    preparation = Preparation(tmp_path, store)
    library = AdventureLibrary(tmp_path)
    service = CreationService(preparation, library, Jobs())
    draft = service.new()
    bundle = preparation.get(draft['id'])['body']['adventure_bundle']
    assert library.get(bundle)['metadata'].get(AUTO_SPLIT_KEY) is True
    path = tmp_path / 'book.pdf'
    path.write_bytes(header_pdf(['Chapter %d. The Trial of Stone' % n
                                 for n in (1, 1, 1, 2, 2, 2, 3, 3, 3)]))
    job = library.enqueue(bundle, path, path.name, 'book')
    run(library.data, job['id'])
    modules = library.modules(bundle)
    assert [module['source_pages'] for module in modules] == [[[1, 3]], [[4, 6]], [[7, 9]]]
    assert all(module['ready'] for module in modules)
    assert library.job(job['id'])['message'].startswith('Извлечено страниц: 9. Модулей: 3.')


def test_a_hand_made_bundle_still_keeps_the_reviewed_split_path(tmp_path):
    """A bundle assembled in the library is cut by a person, not by the importer."""
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Сборник', {'edition': '2024'})
    path = tmp_path / 'book.pdf'
    path.write_bytes(header_pdf(['Chapter %d. The Trial of Stone' % n for n in (1, 1, 1, 2, 2, 2)]))
    job = library.enqueue(bundle['id'], path, path.name, 'book')
    run(library.data, job['id'])
    assert library.modules(bundle['id']) == []


#: The Rise of Tiamat prints eighteen pages of title, credits and contents before
#: its first episode; the contents page names every episode with its folio.
EPISODES = [(1, 'Совет Глубоководья', 19), (2, 'Море движущегося льда', 24),
            (3, 'Варрам Белый', 39), (4, 'Неронвейн', 48),
            (5, 'Культ наносит ответный удар', 55)]
TIAMAT_DOCUMENT = hashlib.sha256(b'rise-of-tiamat-fixture').hexdigest()


def contents_page():
    lines = ['Содержание']
    lines += ['Эпизод %d: %s%s%d' % (number, name, ' ' * 12, page - 2)
              for number, name, page in EPISODES]
    lines.append('Приложение А: Монстры' + ' ' * 16 + '89')
    return '\n'.join(lines)


def tiamat_pages():
    """What every page of the fixture prints, by page number."""
    pages = {page: '' for page in range(1, 19)}
    pages.update({1: 'The Rise of Tiamat', 2: 'The Rise of Tiamat',
                  3: 'Перевод: Александр Винтер', 4: 'ISBN 978-0-7869-6565-6',
                  5: contents_page()})
    prose = ('Союзники собираются в зале совета, и каждый из них говорит свою речь '
             'о том, что происходит в долине, пока герои ждут решения совета. ')
    pages[19] = '\n'.join(['Эпизод 1: Совет Глубоководья'] + [prose] * 6)
    for page in range(24, 39):
        pages[page] = '\n'.join(['Эпизод 2: Море движущегося льда %d' % (page - 1)] + [prose] * 6)
    return pages


def tiamat_rows():
    """The candidates the extractor writes for the same book."""
    rows = [(5, 'chapter', line) for line in contents_page().split('\n')[1:]]
    rows.append((19, 'chapter', 'Эпизод 1: Совет Глубоководья'))
    # The second episode's head repeats as a running head, the first one is
    # printed once: both open a chapter, on different evidence.
    rows += [(page, 'chapter', 'Эпизод 2: Море движущегося льда %d' % (page - 1))
             for page in (24, 25, 26)]
    return sorted(rows)


def tiamat_book(library):
    """A bundle holding the fixture book, exactly as the importer leaves it."""
    bundle = library.create('The Rise of Tiamat RUS', {AUTO_SPLIT_KEY: True})['id']
    pages = tiamat_pages()
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (TIAMAT_DOCUMENT, '.pdf', 1, 'extracted', max(pages)))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)',
                   (bundle, TIAMAT_DOCUMENT, 'The Rise of Tiamat RUS.pdf', 'book', ''))
        for number, text in pages.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (TIAMAT_DOCUMENT, number, str(number), text))
            db.execute('INSERT INTO materials(id,document,page,kind,status) VALUES(?,?,?,?,?)',
                       ('page-%d' % number, TIAMAT_DOCUMENT, number, 'page', 'page'))
        for number, kind, title in tiamat_rows():
            db.execute('INSERT INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                       (hashlib.sha256(('\x00'.join([str(number), title])).encode()).hexdigest()[:32],
                        TIAMAT_DOCUMENT, number, kind, title, '{}'))
    return bundle


def test_a_contents_page_names_chapters_without_opening_them():
    """The defect this card was written for: the contents page opened the book.

    Read without the page text, the heading «Эпизод 1» printed on the contents
    page is the first page of its run and becomes the first module — fourteen
    pages of title and contents a player could start. With the page text the
    contents page is seen for what it prints: several chapters in a row.
    """
    rows = tiamat_rows()
    unread = chapter_starts(rows)
    assert len(unread) == 6 and module_spans(unread, 38)[0][0] == 5
    valid = chapter_starts(rows, tiamat_pages())
    assert [page for page, _title in valid] == [19, 24]
    assert [title for _page, title in valid] == ['Эпизод 1: Совет Глубоководья',
                                                 'Эпизод 2: Море движущегося льда']


def test_a_heading_printed_once_needs_a_page_that_carries_text():
    """An appendix of two pages is printed once; a cover and a blank page are not.

    «Приложение B» stands alone on its first page in Icewind Dale, and the book
    prints it once — the page's own text is what tells the appendix from the
    title page and from the contents page that names it.
    """
    head = 'Приложение B: Тайны персонажей'
    rows = [(320, 'chapter', head), (1, 'chapter', 'The Rise of Tiamat')]
    texts = {320: head + '\n' + ('Отпрыск Алагондара требует, чтобы герой был человеком. ' * 12),
             1: 'The Rise of Tiamat'}
    assert [page for page, _title in chapter_starts(rows, texts)] == [320]


def test_the_title_page_joins_the_first_module_instead_of_being_one(tmp_path):
    """The book gives eleven modules; the front matter is the first one's material."""
    library = AdventureLibrary(tmp_path)
    bundle = tiamat_book(library)
    summary = library.auto_split(bundle, TIAMAT_DOCUMENT)
    assert [module['first_page'] for module in summary['modules']] == [19, 24]
    assert summary['front'] == [1, 18]
    modules = library.modules(bundle)
    assert len(modules) == 2
    # The module opens where the chapter opens — a campaign starts on page 19, not
    # on the cover — and the eighteen pages in front of it travel with it as
    # material, so the maps and handouts printed there stay readable.
    assert modules[0]['source_pages'] == [[19, 23], [1, 18]]
    assert modules[0]['title'] == 'Эпизод 1: Совет Глубоководья'
    assert modules[0]['scene']['pages'] == [19, 23]
    with library.db() as db:
        title_page = db.execute('SELECT page,kind FROM materials WHERE document=? AND page=?',
                                (TIAMAT_DOCUMENT, 5)).fetchone()
    assert tuple(title_page) == (5, 'page')


def test_a_draft_taken_from_the_library_re_cuts_its_modules(tmp_path):
    """A bundle cut by an older importer is cut again when a draft points at it.

    The live profile still held The Rise of Tiamat cut on its contents page,
    because `from_library` copied the files and the old modules with them. The
    same rule that cuts a fresh import now runs on the copy.
    """
    store = Store(tmp_path / 'solo.sqlite3')
    preparation = Preparation(tmp_path, store)
    library = AdventureLibrary(tmp_path)
    service = CreationService(preparation, library, Jobs())
    source = library.create('Книга', {AUTO_SPLIT_KEY: True})['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(header_pdf(['Chapter %d. The Trial of Stone' % n
                                 for n in (1, 1, 1, 2, 2, 2, 3, 3, 3)]))
    job = library.enqueue(source, path, path.name, 'book')
    run(library.data, job['id'])
    # What the older importer left behind: one module over the whole book.
    stale = dict(id='auto-' + job['document'][:8] + '-01', title='Весь документ', reviewed=True,
                 unsupported=[], ruleset='dnd2024', ruleset_version=1,
                 source_ranges=[dict(document=job['document'], first_page=1, last_page=9)])
    with library.db() as db:
        db.execute('DELETE FROM adventure_modules WHERE bundle=?', (source,))
        db.execute('DELETE FROM module_scenes WHERE bundle=?', (source,))
        db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)',
                   (source, stale['id'], json.dumps(stale)))
    draft = service.from_library(source)
    target = preparation.get(draft['id'])['body']['adventure_bundle']
    modules = library.modules(target)
    assert [module['source_pages'] for module in modules] == [[[1, 3]], [[4, 6]], [[7, 9]]]
    assert draft['modules'] == 3 and draft['module_failures'] == []
