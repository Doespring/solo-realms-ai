"""r229: does an uploaded PDF/TXT reach the model, and does a refusal say why?

The three fixtures in ``tests/fixtures/books`` are written for this test (see
``make_books.py``) and hold no book text: a three-page PDF with a text layer, a
scanned PDF with no text at all, and a Russian TXT saved in cp1251.

Every check below is a number or an exact message. The model is a local fixture,
so the run costs nothing while the worker subprocess, the database and the HTTP
surface are the real ones.
"""
import asyncio, json, re, time
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo import adventure_worker, upload_limit
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import decode_text, run
from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.llm import MockLLM

BOOKS = Path(__file__).parent / 'fixtures' / 'books'
SCAN_REFUSAL = 'В этом PDF нет текстового слоя'


class HeadingFixture(MockLLM):
    """One place per printed page, named by that page's own heading."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def configured(self):
        return routerai_preset(), 'fixture-only-never-sent'

    def scope(self, *args):
        return nullcontext()

    def contract_error(self, code):
        self.metrics.append(dict(status='invalid_contract', error=code))

    def answer(self, payload):
        pages = payload.get('pages') or []
        entities = []
        for page in pages[:2]:
            lines = [re.sub(r'^\[S\d+\]\s*', '', line).strip() for line in page['text'].splitlines()]
            lines = [line for line in lines if len(line) > 3]
            if not lines:
                continue
            entities.append(dict(
                key='place-%d' % page['page'], kind='location',
                data=dict(name=lines[0][:100], description=next((l for l in lines[1:] if len(l) > 20), lines[0])[:400]),
                evidence=[dict(document=page['document'], page=page['page'], quote=lines[0][:160])]))
        assert entities, 'a section arrived with no pages'
        return dict(title=entities[0]['data']['name'][:100], ruleset='dnd2014',
                    start=entities[0]['data']['name'], entities=entities)

    async def stream(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        self.metrics.append(dict(mode=kwargs.get('mode'), mock=True))
        raw = json.dumps(self.answer(payload), ensure_ascii=False)
        for offset in range(0, len(raw), 37):
            yield raw[offset:offset + 37]
            await asyncio.sleep(0)


def wait(client, url, done=('complete', 'failed', 'cancelled', 'ready', 'blocked'), seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = client.get(url).json()
        if value['status'] in done:
            return value
        time.sleep(.03)
    raise AssertionError('не дождались ' + url + ': ' + json.dumps(value, ensure_ascii=False))


def draft(client):
    body = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + body['id']
    client.put(url, json={'description': 'Приключение по загруженному источнику', 'revision': body['revision']})
    return url


def upload(client, url, name):
    with (BOOKS / name).open('rb') as stream:
        response = client.post(url + '/files', files={'file': (name, stream)})
    response.raise_for_status()
    source = response.json()['files'][0]
    job = wait(client, '/api/adventure/jobs/' + source['job']['id'], done=('complete', 'failed', 'cancelled'))
    return job, client.get(url).json()['files'][0]


def counts(client, url):
    bundle = client.app.state.creation.owned(url.rsplit('/', 1)[1])['body']['adventure_bundle']
    with client.app.state.adventures.db() as db:
        rows = db.execute('SELECT (SELECT count(*) FROM pages WHERE document IN (SELECT document FROM bundle_files WHERE bundle=?)),'
                          ' (SELECT count(*) FROM materials WHERE document IN (SELECT document FROM bundle_files WHERE bundle=?))',
                          (bundle, bundle)).fetchone()
    return dict(page_rows=rows[0], material_rows=rows[1])


def test_pdf_with_a_text_layer_reaches_the_model(tmp_path):
    llm = HeadingFixture()
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        url = draft(client)
        job, source = upload(client, url, 'text-layer.pdf')
        quality = source['quality']
        assert job['status'] == 'complete' and job['message'].startswith('Материалы извлечены')
        # Журнал называет числа, а не только «готово»: страницы и символы.
        assert '3 страниц, 1 076 символов' in job['message']
        assert source['status'] == 'extracted' and source['name'] == 'text-layer.pdf'
        assert 1000 <= quality['text_characters'] <= 1200, quality
        assert quality['blank_pages'] == 0 and quality['ocr'] is False
        assert counts(client, url)['page_rows'] == 3

        started = client.post(url + '/prepare', json=dict(request_id='pdf-text', ruleset='dnd2014', consent_to_send=True))
        assert started.status_code == 200, started.text
        status = wait(client, '/api/creation/runs/' + started.json()['id'])
        assert status['ready'], status
        assert len(llm.calls) == 1
        assert len(llm.calls[0]['pages']) == 3
        assert status['summary']['locations'] == 2


def test_a_scanned_pdf_is_refused_with_a_reason_and_leaves_nothing_behind(tmp_path):
    llm = HeadingFixture()
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        url = draft(client)
        job, source = upload(client, url, 'scan.pdf')
        # The reason reaches the job log, the file row and the library — three
        # places the player can look, none of them saying «не удалось».
        assert job['status'] == 'failed'
        assert SCAN_REFUSAL in job['message'] and '(ValueError)' not in job['message']
        assert source['status'] == 'failed'
        assert SCAN_REFUSAL in source['quality']['reason']
        assert counts(client, url) == dict(page_rows=0, material_rows=0)
        with client.app.state.adventures.db() as db:
            assert db.execute('SELECT status,pages FROM documents').fetchone()[:2] == ('failed', 0)

        refused = client.post(url + '/prepare', json=dict(request_id='scan', ruleset='dnd2014', consent_to_send=True))
        assert refused.status_code in (400, 409), refused.text
        assert 'scan.pdf' in refused.json()['detail'] and SCAN_REFUSAL in refused.json()['detail']
        assert llm.calls == []  # nothing was ever sent to the model


def test_cp1251_text_is_read_as_russian_not_mojibake(tmp_path):
    llm = HeadingFixture()
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        url = draft(client)
        job, source = upload(client, url, 'russian-cp1251.txt')
        quality = source['quality']
        assert job['status'] == 'complete'
        assert '1 страница, 539 символов' in job['message']
        assert quality['text_encoding'] == 'cp1251'
        assert quality['text_characters'] == len((BOOKS / 'russian-cp1251.txt').read_bytes().decode('cp1251'))
        with client.app.state.adventures.db() as db:
            content = db.execute('SELECT content FROM pages').fetchone()[0]
        assert content.startswith('Соляной тракт у Холлинского брода')
        for mojibake in ('Ð', 'Ñ', 'Ã', '\ufffd'):
            assert mojibake not in content

        started = client.post(url + '/prepare', json=dict(request_id='txt', ruleset='dnd2014', consent_to_send=True))
        status = wait(client, '/api/creation/runs/' + started.json()['id'])
        assert status['ready'], status
        assert 'Соляной тракт' in json.dumps(llm.calls[0], ensure_ascii=False)


@pytest.mark.parametrize('encoding', ['utf-8', 'cp1251', 'cp866', 'koi8-r'])
def test_decode_text_picks_the_code_page_by_content(encoding):
    text = 'Смотрительница брода ведёт счёт каждой телеге, но не говорит о заставе.'
    decoded, chosen = decode_text(text.encode(encoding))
    assert decoded == text
    assert chosen == ('utf-8-sig' if encoding == 'utf-8' else encoding)


def test_too_large_text_names_the_limit_and_the_actual_size(tmp_path, monkeypatch):
    monkeypatch.setattr(adventure_worker, 'TEXT_LIMIT', 1000)
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Большой текст')
    path = tmp_path / 'big.txt'
    path.write_bytes(b'\xd2\xe5\xea\xf1\xf2' * 20000)  # 100 000 bytes of cp1251
    job = library.enqueue(bundle['id'], path, 'big.txt', 'book')
    with pytest.raises(ValueError) as error:
        run(tmp_path, job['id'])
    message = str(error.value)
    assert 'предел' in message and '97,7 КиБ' in message
    assert library.job(job['id'])['status'] == 'failed'
    assert '97,7 КиБ' in library.job(job['id'])['message']  # и в журнале, не только в исключении


def test_upload_limit_names_the_limit_and_the_request_size(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_limit, 'MAX_FILE', 1024 * 1024)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', HeadingFixture())) as client:
        url = draft(client)
        oversized = b'%PDF-1.4\n' + b'x' * (3 * 1024 * 1024)
        response = client.post(url + '/files', files={'file': ('huge.pdf', oversized)})
        assert response.status_code == 413, response.text
        detail = response.json()['detail']
        assert 'предел' in detail and 'запрос весит' in detail and detail.count('МиБ') >= 2
