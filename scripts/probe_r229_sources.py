"""r229: run the real import path over the three upload fixtures and print numbers.

Per file: how many bytes arrived, how many characters were extracted, how many
sections the preparation would send to the model, and where the import refuses.

    .venv/bin/python scripts/probe_r229_sources.py

No model call, no network: this measures the local half of the path, which is
what decides whether a file reaches structuration at all.
"""
import json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solo.adventure_library import AdventureLibrary  # noqa: E402
from solo.adventure_worker import extract_document  # noqa: E402
from solo.preparation_pipeline import PreparationPipeline  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures' / 'books'
CASES = [('text-layer.pdf', 'book'), ('scan.pdf', 'book'), ('russian-cp1251.txt', 'book')]


def sections(library, document):
    """The production batching function, called with only a library attached."""
    batches = list(PreparationPipeline.source_batches(SimpleNamespace(library=library), {document}))
    return dict(batches=len(batches),
                parts=sum(len(pages) for pages, _ in batches),
                characters=sum(len(p['text']) for pages, _ in batches for p in pages),
                readable=all(flag for _, flag in batches) if batches else None)


def probe(path, purpose):
    with tempfile.TemporaryDirectory() as folder:
        library = AdventureLibrary(folder)
        bundle = library.create('r229 ' + path.name)['id']
        job = library.enqueue(bundle, path, path.name, purpose)
        document = job['document']
        result = dict(file=path.name, bytes=path.stat().st_size, document=document)
        try:
            extract_document(library, document, Path(path.name).suffix.lower(), purpose, job['id'])
        except BaseException as error:  # noqa: BLE001 - the refusal itself is the datum
            result['refused'] = type(error).__name__ + ': ' + str(error)
            result['job_message'] = library.job(job['id'])['message']
        else:
            result['refused'] = None
        with library.db() as db:
            row = dict(db.execute('SELECT status,pages,quality FROM documents WHERE id=?', (document,)).fetchone())
            result['document_status'] = row['status']
            result['document_pages'] = row['pages']
            result['quality'] = json.loads(row['quality'])
            result['page_rows'] = db.execute('SELECT count(*) FROM pages WHERE document=?', (document,)).fetchone()[0]
            result['candidates'] = db.execute('SELECT count(*) FROM candidates WHERE document=?', (document,)).fetchone()[0]
            result['materials'] = db.execute('SELECT count(*) FROM materials WHERE document=?', (document,)).fetchone()[0]
            result['page_characters'] = db.execute('SELECT sum(length(content)) FROM pages WHERE document=?', (document,)).fetchone()[0] or 0
        result['sections'] = sections(library, document)
        result['bundle_status'] = library.get(bundle)['status']
        return result


def main():
    rows = [probe(FIXTURES / name, purpose) for name, purpose in CASES]
    for row in rows:
        quality = row['quality']
        print(json.dumps(dict(
            file=row['file'], bytes=row['bytes'], document_status=row['document_status'],
            document_pages=row['document_pages'], page_characters=row['page_characters'],
            quality={k: quality.get(k) for k in ('text_characters', 'blank_pages', 'blank_pages_list',
                                                 'pictured_pages', 'encoding', 'ocr')},
            candidates=row['candidates'], materials=row['materials'],
            sections=row['sections'], refused=row['refused'],
            job_message=row.get('job_message'), bundle_status=row['bundle_status']),
            ensure_ascii=False))
    return rows


if __name__ == '__main__':
    main()
