#!/usr/bin/env python3
"""r205: what the preparation screen reads from the import job.

The card says the screen always prints «глав 0, модулей 0» because
`GET /api/adventure/jobs/{id}` carries only id/status/done/total/message.
This probe walks the screen's own path — draft, upload through
`/api/preparation/drafts/{id}/sources`, poll the job — and prints, for the
same answer, (a) the keys the route returns, (b) the numbers the database
holds, (c) the exact line `web/play.js` builds from them.

Run from the tree root:
    .venv/bin/python scripts/probe_r205_job_counts.py [--outdir runtime/r205-probe]
"""
import argparse
import io
import json
import shutil
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def chapter_pdf(chapters=4, pages_per_chapter=3, padding=0):
    """A real PDF whose printed header names its chapter, as a book does."""
    writer = PdfWriter()
    for chapter in range(1, chapters + 1):
        for _ in range(pages_per_chapter):
            page = writer.add_blank_page(612, 792)
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject(
                {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 30 750 Td (Chapter %d. The Trial of Stone) Tj ET' % chapter).encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    if padding:
        filler = DecodedStreamObject(); filler.set_data(b'%' + b'x' * padding); writer._add_object(filler)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--outdir', default='runtime/r205-probe')
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    outdir = (ROOT / args.outdir).resolve()
    if outdir.exists():
        shutil.rmtree(outdir)
    data = outdir / 'profile'
    data.mkdir(parents=True)

    import os
    os.environ.setdefault('SOLO_TEST', '1')
    from solo.app import create_app
    from solo.preview_mock import PreviewMock

    raw = chapter_pdf(4, 3, 9 * 1024 * 1024)  # above the draft limit: the library path
    app = create_app(data / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True)
    with TestClient(app) as client:
        draft = client.post('/api/preparation/drafts', json={}).json()
        response = client.post('/api/preparation/drafts/' + draft['id'] + '/sources',
                               files={'file': ('Руководство.pdf', raw, 'application/pdf')})
        assert response.status_code == 200, response.text
        upload = response.json()
        print('upload response keys :', sorted(upload))
        print('upload path          :', upload.get('path'))
        print('upload job answer    :', upload.get('job') if isinstance(upload.get('job'), str) else
              json.dumps(upload.get('job'), ensure_ascii=False))
        job_id = upload['job'] if isinstance(upload['job'], str) else upload['job']['id']
        deadline = time.monotonic() + args.timeout
        answer = {}
        while time.monotonic() < deadline:
            answer = client.get('/api/adventure/jobs/' + job_id).json()
            if answer['status'] in ('complete', 'failed', 'cancelled'):
                break
            time.sleep(.2)
        print('job answer keys      :', sorted(answer))
        print('job answer           :', json.dumps(answer, ensure_ascii=False))

        library = app.state.adventures
        with library.db() as db:
            modules = db.execute('SELECT count(*) FROM adventure_modules WHERE bundle=?',
                                 (answer['bundle'],)).fetchone()[0]
            document = db.execute('SELECT document FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
        boundaries = library.chapters(document)
        print('database modules     :', modules)
        print('database boundaries  :', len(boundaries), [page for page, _ in boundaries])

        # Exactly what web/play.js:245 prints, from this same answer.
        reused = next((row for row in client.get('/api/adventure/library').json()
                       if row['id'] == answer['bundle']), None)
        pages = (reused and reused.get('pages')) or answer.get('done') or answer.get('total') or 0
        print('screen line          : Понято: страниц %s, глав %s, модулей %s.' % (
            pages, answer.get('chapters', 0), answer.get('modules', 0)))
        ok = answer.get('chapters') == len(boundaries) and answer.get('modules') == modules
        print('route == database    :', 'OK' if ok else 'MISMATCH')
        return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
