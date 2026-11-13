"""r229: прогнать настоящий путь импорта на трёх файлах и напечатать числа.

На файл: сколько байт пришло, сколько знаков извлечено, сколько знаков уходит в
модель в размеченных цитатах, сколько секций уйдёт и на каком шаге отказ. Это
локальная половина пути — без вызова модели и без сети, потому что именно она
решает, дойдёт ли содержимое до структурирования.

    .venv/bin/python scripts/probe_r229_sources.py

Печатает журнал «файл → знаков → секций → результат» и сохраняет его в
``reports/r229/sources.json``.
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solo.adventure_library import AdventureLibrary  # noqa: E402
from solo.adventure_worker import extract_document  # noqa: E402
from solo.preparation_format import source_input  # noqa: E402
from solo.preparation_pipeline import PreparationPipeline  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures' / 'books'
CASES = [('text-layer.pdf', 'book'), ('scan-only.pdf', 'book'), ('cp1251.txt', 'book')]


def sections(library, document):
    """Продовый делитель секций, вызванный только с привязанной библиотекой."""
    batches = list(PreparationPipeline.source_batches(SimpleNamespace(library=library), {document}))
    return dict(batches=len(batches),
                characters=sum(len(p['text']) for pages, _ in batches for p in pages),
                readable=all(flag for _, flag in batches) if batches else None)


def wire(batches):
    """Что именно уходит модели: цитаты со ссылкой на страницу, знак в знак."""
    rendered = []
    for pages, _readable in batches:
        rendered, spans, _schema = source_input(pages)
        joined = '\n'.join(page.get('text') or '' for page in rendered)
        yield dict(spans=len(spans),
                   quote_characters=sum(len(span['quote']) for span in spans.values()),
                   marked=all('[' + name + ']' in joined for name in spans))


def probe(path, purpose):
    with tempfile.TemporaryDirectory() as folder:
        library = AdventureLibrary(folder)
        bundle = library.create('r229 ' + path.name)['id']
        job = library.enqueue(bundle, path, path.name, purpose)
        document = job['document']
        result = dict(file=path.name, bytes=path.stat().st_size, document=document)
        try:
            extract_document(library, document, Path(path.name).suffix.lower(), purpose, job['id'])
        except BaseException as error:  # noqa: BLE001 - сам отказ и есть измерение
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
        batches = list(PreparationPipeline.source_batches(SimpleNamespace(library=library), {document}))
        result['sections'] = dict(batches=len(batches),
                                  characters=sum(len(p['text']) for pages, _ in batches for p in pages),
                                  readable=all(flag for _, flag in batches) if batches else None)
        result['wire'] = list(wire(batches))
        result['bundle_status'] = library.get(bundle)['status']
        return result


def journal(row):
    """Одна строка журнала: файл → извлечено знаков → секций → результат."""
    quality = row['quality']
    outcome = ('отказ: ' + row['refused'].split(': ', 1)[-1]) if row['refused'] else 'извлечено, ' + str(row['document_pages']) + ' стр.'
    wire_quotes = sum(item['quote_characters'] for item in row['wire'])
    return ('%(file)s → извлечено %(chars)s знаков (%(encoding)s) → в модели %(quotes)s знаков '
            'в %(spans)s цитатах → %(batches)s секций → %(outcome)s' % dict(
                file=row['file'], chars=row['page_characters'],
                encoding=quality.get('text_encoding') or 'текстовый слой PDF',
                quotes=wire_quotes, spans=sum(item['spans'] for item in row['wire']),
                batches=row['sections']['batches'], outcome=outcome))


def main():
    rows = [probe(FIXTURES / name, purpose) for name, purpose in CASES]
    lines = []
    for row in rows:
        quality = row['quality']
        line = dict(
            file=row['file'], bytes=row['bytes'], document_status=row['document_status'],
            document_pages=row['document_pages'], page_characters=row['page_characters'],
            quality={k: quality.get(k) for k in ('text_characters', 'text_encoding', 'blank_pages',
                                                 'pictured_pages', 'ocr', 'reason')},
            candidates=row['candidates'], materials=row['materials'],
            sections=row['sections'], wire=row['wire'], refused=row['refused'],
            job_message=row.get('job_message'), bundle_status=row['bundle_status'])
        lines.append(line)
        print(journal(row))
        print(json.dumps(line, ensure_ascii=False))
    out = ROOT / 'reports' / 'r229' / 'sources.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding='utf8')
    return rows


if __name__ == '__main__':
    main()
