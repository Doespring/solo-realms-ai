"""r229: сквозной прогон загрузки и разбора трёх файлов — до модели и обратно.

Владелец сказал «анализ пдф и тхт не работает». Проверка идёт по настоящему
пути игрока, а не по догадке: черновик создания → `POST /files` (тот же
`receive_source`) → воркер извлечения в отдельном процессе → `POST /prepare`
(настоящий `PreparationPipeline`) → ответ модели. Модель подменена офлайн-моком,
который отвечает по переданному блоку: цитата берётся из самих `[S\\d+]`, поэтому
доказательство в ответе настоящее, а денег прогон не стоит.

Числа, которые печатаются на каждый файл: сколько символов извлечено, сколько
секций ушло в модель, сколько символов в самой большой секции, на каком шаге
отказ и что видит человек. Журнал — `reports/r229/report.json`.

    .venv/bin/python scripts/probe_r229_upload.py
    .venv/bin/python scripts/probe_r229_upload.py --keep
"""
import argparse, asyncio, json, re, shutil, sys, time
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from solo.adventure_library import AdventureLibrary
from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.llm import MockLLM

FIXTURES = ROOT / 'tests' / 'fixtures' / 'books'


#: Строки, где двоеточие — не название места, а пометка «читать вслух».
NOT_A_PLACE = {'read aloud', 'прочитайте вслух', 'прочитать вслух'}


def printed_place(payload):
    """Место, которое блок печатает сам: имя, описание и номер фрагмента.

    Мок обязан отвечать так, как ответила бы модель по этой странице: название
    места стоит в тексте, описание — его же слова, цитата — тот самый фрагмент.
    Иначе прогон проверяет не путь файла, а строгость сборки к выдуманному миру.
    """
    printed = ''.join(page.get('text') or '' for page in payload.get('pages') or [])
    for span, body in re.findall(r'\[(S\d+)\]\s*([^\[]*)', printed):
        for line in body.splitlines():
            match = re.match(r'\s*([^:]{2,60}?):\s*(\S.{0,160})$', line)
            if match and match.group(1).strip().lower() not in NOT_A_PLACE:
                return dict(name=match.group(1).strip(), description=match.group(2).strip(), span=span)
        first = next((line.strip() for line in body.splitlines() if line.strip()), '')
        if first:
            return dict(name=first[:60], description=first, span=span)
    return dict(name='Причал', description='Причал.', span='')


class SpanAnswer(MockLLM):
    """Ответ «модели» на переданный блок: сущности с настоящей цитатой."""

    def __init__(self):
        super().__init__()
        self.payloads = []
        self.sizes = []
        self.calls = []

    def configured(self):
        return routerai_preset(), 'probe-only-never-sent'

    def scope(self, *args):
        return nullcontext()

    def contract_error(self, code):
        self.metrics.append(dict(status='invalid_contract', error=code))

    async def stream(self, system, user, **kwargs):
        payload = json.loads(user)
        self.payloads.append(payload)
        pages = payload.get('pages') or []
        self.sizes.append(sum(len(page.get('text') or '') for page in pages))
        self.calls.append(dict(task=payload.get('task') or payload.get('mode') or '',
                               pages=len(pages), characters=sum(len(page.get('text') or '') for page in pages),
                               numbers=[page.get('page') for page in pages],
                               documents=sorted({page.get('document') for page in pages})))
        self.metrics.append(dict(mode=kwargs.get('mode'), mock=True, bytes=len(user)))
        place = printed_place(payload)
        proof = dict(source_span=place['span']) if place['span'] else dict(document='', page=0, quote='')
        value = dict(title='Прогон r229', ruleset='dnd2024', start=place['name'], entities=[
            dict(key='r229-place', kind='location',
                 data=dict(name=place['name'], description=place['description']),
                 evidence=[proof]),
        ])
        raw = json.dumps(value, ensure_ascii=False)
        for offset in range(0, len(raw), 64):
            yield raw[offset:offset + 64]
            await asyncio.sleep(0)


def wait_job(client, job, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = client.get('/api/adventure/jobs/' + job).json()
        if value['status'] not in ('queued', 'running'):
            return value
        time.sleep(.2)
    return value


def wait_run(client, run, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = client.get('/api/creation/runs/' + run).json()
        if value['status'] not in ('queued', 'running'):
            return value
        time.sleep(.05)
    return value


def library_facts(work, document):
    with AdventureLibrary(work).db() as db:
        doc = db.execute('SELECT format,status,pages,quality,bytes FROM documents WHERE id=?', (document,)).fetchone()
        pages = [dict(row) for row in db.execute('SELECT number,length(content) AS chars FROM pages WHERE document=? ORDER BY number', (document,))]
        materials = db.execute('SELECT count(*) FROM materials WHERE document=?', (document,)).fetchone()[0]
    return dict(document=document[:12], document_status=doc['status'] if doc else None,
                format=doc['format'] if doc else None, pages=len(pages),
                extracted_characters=sum(row['chars'] or 0 for row in pages),
                materials=materials, quality=json.loads(doc['quality']) if doc else None)


def run_file(client, work, path, request, llm):
    llm.calls.clear(); llm.sizes.clear(); llm.payloads.clear()
    draft = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + draft['id']
    client.put(url, json=dict(description='Прогон r229: загрузка и разбор', revision=draft['revision'])).raise_for_status()
    with path.open('rb') as stream:
        uploaded = client.post(url + '/files', files={'file': (path.name, stream, 'application/octet-stream')})
    report = dict(file=path.name, upload_status=uploaded.status_code)
    if uploaded.status_code != 200:
        report.update(refused=True, message=uploaded.json().get('detail') or uploaded.text[:300], step='upload')
        return report
    document = uploaded.json()['files'][-1]['document']
    job = [row for row in uploaded.json()['files'] if row['document'] == document][0]['job']
    finished = wait_job(client, job['id'])
    report.update(job=dict(id=job['id'], status=finished['status'], message=finished['message']),
                  library=library_facts(work, document))
    if finished['status'] != 'complete':
        report.update(step='extract', refused=True)
        return report
    started = client.post(url + '/prepare', json=dict(request_id=request, ruleset='dnd2024', consent_to_send=True, world_structure=True))
    if started.status_code != 200:
        report.update(step='prepare-refused', refused=True, message=started.json().get('detail'))
        return report
    status = wait_run(client, started.json()['id'])
    report.update(step='prepare', run=dict(id=status['id'], status=status['status'], ready=status['ready'],
                                           done=status['done'], total=status['total'], message=status['message'],
                                           errors=status['errors'],
                                           summary=status.get('summary')),
                  model=dict(calls=len(llm.payloads), largest_block=max(llm.sizes + [0]),
                             blocks_sent=list(llm.sizes), block_detail=list(llm.calls),
                             characters_in_model=sum(call['characters'] for call in llm.calls)))
    with client.app.state.preparation.connection() as db:
        parts = [dict(row) for row in db.execute(
            'SELECT l.ordinal,p.status,length(p.source) AS characters '
            'FROM creation_part_links l JOIN creation_parts p ON p.id=l.part WHERE l.run=? ORDER BY l.ordinal',
            (status['id'],))]
    report['parts'] = parts
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', default=str(ROOT / 'reports/r229/probe'))
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    work = Path(args.workdir)
    if work.exists() and not args.keep:
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    llm = SpanAnswer()
    reports = []
    with TestClient(create_app(work / 'solo.sqlite3', llm)) as client:
        for index, name in enumerate(('text-layer.pdf', 'scan-only.pdf', 'cp1251.txt')):
            reports.append(run_file(client, work, FIXTURES / name, 'r229-' + str(index), llm))
    out = work.parent / 'report.json'
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf8')
    for report in reports:
        print(json.dumps(report, ensure_ascii=False))
    print('report', out)


if __name__ == '__main__':
    main()
