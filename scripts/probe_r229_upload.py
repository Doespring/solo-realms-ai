"""r229: does an uploaded PDF/TXT actually reach the parsing model?

Runs the real path for the three book shapes a player can bring — a PDF with a
text layer, a scanned PDF without one, and a Russian TXT in cp1251 — and prints
numbers, not adjectives:

    file -> bytes -> pages -> extracted characters -> sections sent to the model
         -> result or the exact step where it stopped.

The model is a local fixture: nothing is paid, but the pipeline, the worker
subprocess, the database and the HTTP surface are the real ones.

    .venv/bin/python scripts/probe_r229_upload.py [--json reports/r229/probe.json]
"""
import argparse, asyncio, json, os, re, sys, time
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from solo.ai_roles import routerai_preset  # noqa: E402
from solo.app import create_app  # noqa: E402
from solo.llm import MockLLM  # noqa: E402

BOOKS = ROOT / 'tests' / 'fixtures' / 'books'
CASES = [('text-layer.pdf', 'PDF с текстовым слоем'),
         ('scan.pdf', 'PDF-скан без текста'),
         ('russian-cp1251.txt', 'TXT в cp1251')]


class CountingLLM(MockLLM):
    """Answers every section with a world whose quotes come from that section."""

    def __init__(self):
        super().__init__()
        self.payloads = []
        self.calls = []

    def configured(self):
        return routerai_preset(), 'fixture-only-never-sent'

    def scope(self, *args):
        return nullcontext()

    def contract_error(self, code):
        self.metrics.append(dict(status='invalid_contract', error=code))

    def answer(self, payload):
        """One place per printed page, named by that page's own heading.

        Names have to appear on the page they are cited from: a fixture with
        invented place names would be quarantined for the right reason and the
        probe would measure the fixture instead of the import path.
        """
        pages = payload.get('pages') or []
        entities = []
        for page in pages[:2]:
            lines = [re.sub(r'^\[S\d+\]\s*', '', line).strip() for line in page['text'].splitlines()]
            lines = [line for line in lines if len(line) > 3]
            if not lines:
                continue
            name = lines[0][:100]
            description = next((line for line in lines[1:] if len(line) > 20), lines[0])[:400]
            entities.append(dict(key='place-%d' % page['page'], kind='location',
                                 data=dict(name=name, description=description),
                                 evidence=[dict(document=page['document'], page=page['page'], quote=lines[0][:160])]))
        if not entities:
            raise AssertionError('секция пришла без страниц')
        return dict(title=entities[0]['data']['name'][:100], ruleset='dnd2014',
                    start=entities[0]['data']['name'], entities=entities)

    async def stream(self, system, user, **kwargs):
        payload = json.loads(user)
        self.payloads.append(payload)
        self.calls.append(payload)
        self.metrics.append(dict(mode=kwargs.get('mode'), mock=True))
        raw = json.dumps(self.answer(payload), ensure_ascii=False)
        for offset in range(0, len(raw), 37):
            yield raw[offset:offset + 37]
            await asyncio.sleep(0)


def wait_job(client, job):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = client.get('/api/adventure/jobs/' + job).json()
        if state['status'] not in ('queued', 'running'):
            return state
        time.sleep(.05)
    raise AssertionError('извлечение не завершилось за 60 с: ' + json.dumps(state, ensure_ascii=False))


def wait_run(client, run):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = client.get('/api/creation/runs/' + run).json()
        if state['status'] not in ('queued', 'running'):
            return state
        time.sleep(.05)
    raise AssertionError('подготовка не завершилась за 120 с: ' + json.dumps(state, ensure_ascii=False))


def probe_case(client, llm, name, workdir):
    path = BOOKS / name
    draft = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + draft['id']
    client.put(url, json={'description': 'Приключение по загруженному источнику', 'revision': draft['revision']})

    with path.open('rb') as stream:
        uploaded = client.post(url + '/files', files={'file': (name, stream)})
    uploaded.raise_for_status()
    job_id = uploaded.json()['files'][0]['job']['id']
    job = wait_job(client, job_id)
    after_upload = client.get(url).json()
    source = next(f for f in after_upload['files'] if f['name'] == name)
    quality = source.get('quality') or {}

    row = dict(file=name, bytes=path.stat().st_size, job_status=job['status'],
               job_message=job['message'], document_status=source.get('status'),
               extracted_characters=quality.get('text_characters'),
               pages=(library_leftovers(client, draft['id'])['documents'][0]['pages'] if library_leftovers(client, draft['id'])['documents'] else 0),
               encoding=quality.get('text_encoding'),
               library_reason=quality.get('reason'), draft=draft['id'])

    before = len(llm.calls)
    started = client.post(url + '/prepare', json=dict(request_id='r229-' + name, ruleset='dnd2014', consent_to_send=True))
    if started.status_code != 200:
        row.update(prepare_http=started.status_code, prepare_refusal=started.json().get('detail'), sections=0)
        return row

    status = wait_run(client, started.json()['id'])
    sent = llm.calls[before:]
    chars = sum(len(json.dumps(p, ensure_ascii=False)) for p in sent)
    pages = sum(len(p.get('pages') or []) for p in sent)
    row.update(prepare_http=200, prepare_status=status['status'], run_message=status.get('message'),
               sections=len(sent), pages_in_sections=pages, payload_characters=chars,
               locations=(status.get('summary') or {}).get('locations'),
               errors=status.get('errors'))
    return row


def library_leftovers(client, draft_id):
    """Nothing unusable may stay behind for a refused file — counted for THIS bundle."""
    library = client.app.state.adventures
    bundle = client.app.state.creation.owned(draft_id)['body']['adventure_bundle']
    with library.db() as db:
        documents = [dict(r) for r in db.execute(
            'SELECT d.id,d.format,d.status,d.pages,d.quality FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=?',
            (bundle,))]
        page_rows = db.execute('SELECT count(*) FROM pages WHERE document IN (SELECT document FROM bundle_files WHERE bundle=?)', (bundle,)).fetchone()[0]
        material_rows = db.execute('SELECT count(*) FROM materials WHERE document IN (SELECT document FROM bundle_files WHERE bundle=?)', (bundle,)).fetchone()[0]
    return dict(documents=documents, page_rows=page_rows, material_rows=material_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', default='')
    options = parser.parse_args()
    import tempfile
    root = Path(tempfile.mkdtemp(prefix='r229-probe-'))
    report = dict(workdir=str(root), cases=[])
    llm = CountingLLM()
    with TestClient(create_app(root / 'solo.sqlite3', llm)) as client:
        for name, title in CASES:
            row = probe_case(client, llm, name, root)
            row['title'] = title
            row['library'] = library_leftovers(client, row['draft'])
            report['cases'].append(row)
            print(f"{name}: {row['bytes']} байт -> извлечено {row['extracted_characters']} символов "
                  f"({row['encoding'] or 'pdf'}) -> статус {row['job_status']} -> секций {row['sections']} "
                  f"-> {row.get('prepare_status') or 'отказ подготовки'}")
            print('   журнал:', row['job_message'])
            if row.get('prepare_refusal'):
                print('   отказ:', row['prepare_refusal'])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if options.json:
        target = Path(options.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('записано', target)


if __name__ == '__main__':
    main()
