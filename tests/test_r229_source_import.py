"""r229: что делает загрузка с тремя файлами, которые приносит человек.

Фикстуры — три коротких своих фрагмента в ``tests/fixtures/books`` (запуск
``.venv/bin/python tests/fixtures/books/build_fixtures.py`` их пересоздаёт):

* PDF с текстовым слоем — текст обязан дойти до подготовки целиком;
* PDF-скан без текстового слоя — честный отказ, а не пустая страница в библиотеке;
* TXT в cp1251 (и cp866/KOI8-R) — русский текст, а не кракозябры.

Ни один тест не ходит в модель и не ходит в сеть: проверяется локальная половина
пути, которая решает, дойдёт ли содержимое до структурирования.
"""
import asyncio
import io
import json
import os
from pathlib import Path

import pytest
from fastapi import UploadFile

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import decode_text, extract_document, run
from solo.preparation_pipeline import PreparationPipeline
from solo.source_upload import receive_source

BOOKS = Path(__file__).resolve().parent / 'fixtures' / 'books'
RUSSIAN = 'Соляные ворота открываются на рассвете. Ветер несёт соль через гавань.'


def imported(tmp_path, name, raw=None, purpose='book'):
    """Положить файл в библиотеку так же, как его кладёт загрузка, и прочитать."""
    library = AdventureLibrary(tmp_path)
    bundle = library.create('r229 ' + name)['id']
    path = tmp_path / 'upload.bin'
    path.write_bytes(raw if raw is not None else (BOOKS / name).read_bytes())
    job = library.enqueue(bundle, path, name, purpose)
    return library, bundle, job


def text_of(library, document):
    with library.db() as db:
        return db.execute('SELECT content FROM pages WHERE document=? ORDER BY number', (document,)).fetchall()


def counts(library, document):
    with library.db() as db:
        return dict(
            pages=db.execute('SELECT count(*) FROM pages WHERE document=?', (document,)).fetchone()[0],
            materials=db.execute('SELECT count(*) FROM materials WHERE document=?', (document,)).fetchone()[0],
            candidates=db.execute('SELECT count(*) FROM candidates WHERE document=?', (document,)).fetchone()[0],
            quality=json.loads(db.execute('SELECT quality FROM documents WHERE id=?', (document,)).fetchone()[0]),
            status=db.execute('SELECT status FROM documents WHERE id=?', (document,)).fetchone()[0])


def sections(library, document):
    from types import SimpleNamespace
    batches = list(PreparationPipeline.source_batches(SimpleNamespace(library=library), {document}))
    return batches


def test_text_pdf_reaches_preparation_with_its_own_words(tmp_path):
    library, bundle, job = imported(tmp_path, 'text-layer.pdf')
    extract_document(library, job['document'], '.pdf', 'book', job['id'])
    found = counts(library, job['document'])
    assert found['status'] == 'extracted' and found['pages'] == 3
    pages = [row[0] for row in text_of(library, job['document'])]
    joined = '\n'.join(pages)
    assert 'The Salt Gate' in joined and 'Keeper Alva waits by the gate.' in joined
    assert found['quality']['text_characters'] == sum(len(page) for page in pages) > 300
    batches = sections(library, job['document'])
    assert len(batches) >= 1
    # Ни один знак не потерялся между извлечением и тем, что уходит в модель.
    assert sum(len(piece['text']) for batch, _ in batches for piece in batch) == found['quality']['text_characters']
    assert library.get(bundle)['status'] == 'documents_imported'


def test_scan_without_text_layer_is_refused_and_leaves_no_junk(tmp_path):
    library, bundle, job = imported(tmp_path, 'scan-only.pdf')
    with pytest.raises(ValueError, match='нет текстового слоя'):
        extract_document(library, job['document'], '.pdf', 'book', job['id'])
    found = counts(library, job['document'])
    assert found['status'] == 'failed' and found['pages'] == 0
    assert found['materials'] == 0 and found['candidates'] == 0
    assert 'OCR' in found['quality']['reason'] and 'TXT' in found['quality']['reason']
    assert not text_of(library, job['document'])
    # Комплект не делает вид, что источник годен.
    assert library.get(bundle)['status'] == 'import_incomplete'
    assert sections(library, job['document']) == []


def test_scan_refusal_reaches_the_job_message_with_its_reason(tmp_path):
    library, bundle, job = imported(tmp_path, 'scan-only.pdf')
    with pytest.raises(ValueError):
        run(tmp_path, job['id'])
    message = library.job(job['id'])['message']
    assert library.job(job['id'])['status'] == 'failed'
    assert 'нет текстового слоя' in message and 'OCR' in message
    assert 'Оригинал сохранён' in message


def test_cp1251_book_is_read_in_russian(tmp_path):
    library, bundle, job = imported(tmp_path, 'cp1251.txt')
    extract_document(library, job['document'], '.txt', 'book', job['id'])
    found = counts(library, job['document'])
    assert found['status'] == 'extracted' and found['quality']['text_encoding'] == 'cp1251'
    text = text_of(library, job['document'])[0][0]
    assert 'Глава 1. Соляные ворота' in text and 'Гавань: мокрый камень блестит под фонарём.' in text
    assert '\ufffd' not in text and not any(0x80 <= ord(c) < 0x300 for c in text[:40])
    assert len(sections(library, job['document'])) >= 1


@pytest.mark.parametrize('encoding', ['utf-8', 'cp1251', 'cp866', 'koi8-r', 'utf-16'])
def test_text_encoding_is_recognised_not_guessed(tmp_path, encoding):
    library, bundle, job = imported(tmp_path, 'book.txt', raw=RUSSIAN.encode(encoding))
    extract_document(library, job['document'], '.txt', 'book', job['id'])
    assert counts(library, job['document'])['quality']['text_encoding'] == encoding
    assert text_of(library, job['document'])[0][0] == RUSSIAN


def test_binary_file_named_txt_is_refused_with_a_reason(tmp_path):
    library, bundle, job = imported(tmp_path, 'book.txt', raw=os.urandom(4000))
    with pytest.raises(ValueError, match='не похож на текст'):
        run(tmp_path, job['id'])
    found = counts(library, job['document'])
    assert found['status'] == 'failed' and found['pages'] == 0 and not text_of(library, job['document'])
    assert 'не похож на текст' in library.job(job['id'])['message']


def test_oversized_upload_names_the_limit_and_what_was_read(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('r229 предел')['id']
    from solo.adventure_library import ImportJobs
    from unittest.mock import patch
    jobs = ImportJobs(library)
    try:
        with patch('solo.source_upload.MAX_FILE', 1024):
            with pytest.raises(ValueError) as raised:
                asyncio.run(receive_source(library, jobs, bundle,
                                           UploadFile(io.BytesIO(b'a' * 2048), filename='book.txt')))
    finally:
        jobs.close()
    message = str(raised.value)
    assert '384' in message and 'МиБ' in message and 'прочитано' in message
    assert any(character.isdigit() for character in message.split('прочитано')[1])
    assert not list(library.files.glob('.upload-*'))


def test_oversized_file_in_library_names_its_actual_size(tmp_path):
    from unittest.mock import patch
    library = AdventureLibrary(tmp_path)
    bundle = library.create('r229 размер')['id']
    path = tmp_path / 'book.txt'
    path.write_bytes(b'x' * 4096)
    with patch('solo.adventure_library.MAX_FILE', 1024):
        with pytest.raises(ValueError, match='384') as raised:
            library.enqueue(bundle, path, 'book.txt', 'book')
    assert 'МиБ' in str(raised.value) and any(character.isdigit() for character in str(raised.value))
    assert library.get(bundle)['documents'] == 0


def test_unsupported_format_names_the_extension(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('r229 формат')['id']
    path = tmp_path / 'book.exe'
    path.write_bytes(b'MZ')
    with pytest.raises(ValueError, match=r'\.exe'):
        library.enqueue(bundle, path, 'book.exe', 'book')


def test_successful_import_says_how_much_was_read(tmp_path):
    library, bundle, job = imported(tmp_path, 'text-layer.pdf')
    run(tmp_path, job['id'])
    message = library.job(job['id'])['message']
    assert library.job(job['id'])['status'] == 'complete'
    characters = counts(library, job['document'])['quality']['text_characters']
    assert 'знаков: ' + str(characters) in message


def test_decode_text_never_returns_mojibake():
    text, encoding = decode_text(RUSSIAN.encode('cp866'))
    assert text == RUSSIAN and encoding == 'cp866'
    with pytest.raises(ValueError):
        decode_text(os.urandom(4000))
