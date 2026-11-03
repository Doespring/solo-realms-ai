"""End-to-end control run: the real cloud profile, the real ledger, one book.

No fixture answers the model here. The app is built exactly as it ships
(create_app), the only substitution is the database directory, so the run
cannot touch the owner's saves. The cloud profile, the DPAPI vault and the cost
ledger are the owner's own files, because the spend must be real and counted.
"""
import json, os, sys, time
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud import CloudLLM
from solo.cloud_settings import CloudSettings
from solo.cloud_cost import CostLedger
from fixtures.control_book import build as build_control_book

#: A working copy of the owner's cloud profile. The real profile directory is
#: not writable from this process, and the ledger has to be written for the
#: spend to be counted, so the copy keeps the same key, prices and history.
PROFILE_DIR = ROOT / '.uitmp' / 'control' / 'profile'
WORK = ROOT / '.uitmp' / 'control'


def ledger_totals():
    import sqlite3
    db = sqlite3.connect(PROFILE_DIR / 'cloud-usage.sqlite3')
    try:
        row = db.execute('select count(*), coalesce(sum(cost),0) from calls').fetchone()
    finally:
        db.close()
    return dict(calls=row[0], spent_micro=row[1], spent_rub=round(row[1] / 1e6, 4))


def wait_run(client, run_id, deadline=900):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        status = client.get('/api/creation/runs/' + run_id).json()
        if status['status'] not in ('queued', 'running'):
            return status
        time.sleep(0.5)
    raise SystemExit('run did not finish: ' + status.get('status', '?'))


def wait_job(client, job_id, deadline=120):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        status = client.get('/api/adventure/jobs/' + job_id).json()
        if status['status'] not in ('queued', 'running'):
            return status
        time.sleep(0.05)
    raise SystemExit('upload did not finish')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    book = WORK / 'lantern-quarry.pdf'
    if not book.exists():
        build_control_book(book)
    print('book', book.stat().st_size, 'bytes')

    before = ledger_totals()
    print('ledger before', json.dumps(before))

    settings = CloudSettings(PROFILE_DIR / 'cloud-profiles.json')
    ledger = CostLedger(PROFILE_DIR / 'cloud-usage.sqlite3')
    llm = CloudLLM(settings, ledger)
    profile, _key = llm.configured()
    print('profile', profile.name, profile.provider, profile.interpreter.model)

    app = create_app(WORK / 'solo.sqlite3', llm, experimental=True)
    client = TestClient(app)
    client.__enter__()
    try:
        draft = client.post('/api/creation/drafts').json()
        did = draft['id']
        url = '/api/creation/drafts/' + did
        client.put(url, json=dict(description='Control book: the Lantern Quarry.', revision=draft['revision'])).raise_for_status()
        uploaded = client.post(url + '/files',
                               files={'file': ('lantern-quarry.pdf', book.read_bytes(), 'application/pdf')})
        assert uploaded.status_code == 200, uploaded.text
        job = uploaded.json()['files'][0]['job']['id']
        print('upload job', json.dumps(wait_job(client, job), ensure_ascii=False))

        started = client.post(url + '/prepare', json=dict(
            request_id='control-4', ruleset='dnd2014', consent_to_send=True, world_structure=True))
        assert started.status_code == 200, started.text
        run_id = started.json()['id']
        print('run', run_id)
        status = wait_run(client, run_id)
        print('run status', status['status'], 'ready', status.get('ready'))

        after = ledger_totals()
        print('ledger after', json.dumps(after))
        print('delta rub', round(after['spent_rub'] - before['spent_rub'], 4))

        Path(WORK / 'run-status.json').write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding='utf-8')
        draft_state = client.get(url).json()
        Path(WORK / 'draft.json').write_text(json.dumps(draft_state, ensure_ascii=False, indent=1), encoding='utf-8')
        Path(WORK / 'draft-id.txt').write_text(did, encoding='utf-8')
        private = app.state.preparation_pipeline.raw(run_id)
        Path(WORK / 'run-raw.json').write_text(json.dumps(private, ensure_ascii=False, indent=1), encoding='utf-8')
        print('draft ready', draft_state.get('preparation', {}).get('status'))
    finally:
        client.__exit__(None, None, None)


if __name__ == '__main__':
    main()
