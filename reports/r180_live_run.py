"""r180 live run: a player's own book import, through the player's own route.

The defect the card was written for was seen by the owner in the browser: a book
downloaded from a store was uploaded into a new adventure draft
(``POST /api/creation/drafts`` + ``POST /api/creation/drafts/{id}/files``),
imported with «Материалы извлечены» and gave **zero** modules, so the whole
256-page book had to be cut by hand. This script repeats exactly that route on a
separate profile (``runtime/r180-live``) and its own port, uploads four real
books, and prints what each one produced:

* Witchlight — the book that failed: it must cut itself into ten modules;
* Hoard of the Dragon Queen, The Rise of Tiamat, Icewind Dale — the books that
  already split: their module counts must not go down (8 / 12 / 8).

Nothing is invented: every number below comes from ``GET
/api/adventure/library/{bundle}/modules`` and the import job's own message.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT / 'runtime' / 'books'
LIVE = ROOT / 'runtime' / 'r180-live'
BOOK = ROOT / 'runtime' / 'r180-book'
SUMMARY = ROOT / 'reports' / 'r180-live.json'

#: The ten parts the book prints, as its own heads number them.
EXPECTED_RANGES = [(24, 57), (58, 99), (100, 133), (134, 171), (172, 208),
                   (209, 215), (216, 229), (230, 242), (243, 250), (251, 256)]
BASELINE = {'Hoard of the Dragon Queen RUS.pdf': 8, 'The Rise of Tiamat RUS.pdf': 12,
            'Icewind Dale - Rime of the Frostmaiden RUS.pdf': 8}


def wait_job(client, job, limit=1800):
    started = time.monotonic()
    states = []
    while time.monotonic() - started < limit:
        body = client.get('/api/adventure/jobs/' + job).json()
        state = body.get('status')
        if not states or states[-1] != state:
            states.append(state)
        if state in ('complete', 'failed', 'cancelled'):
            return body, states, round(time.monotonic() - started, 1)
        time.sleep(1.0)
    raise SystemExit('import did not finish in %ss: %s' % (limit, states))


def bundles(client):
    return {row['id'] for row in client.get('/api/adventure/library').json()}


def upload_into_draft(client, path):
    """The player's own route: a new draft, then the book into that draft."""
    before = bundles(client)
    draft = client.post('/api/creation/drafts')
    draft.raise_for_status()
    draft_id = draft.json()['id']
    with path.open('rb') as handle:
        uploaded = client.post('/api/creation/drafts/' + draft_id + '/files',
                               files={'file': (path.name, handle, 'application/pdf')})
    uploaded.raise_for_status()
    job_id = uploaded.json()['files'][0]['job']['id']
    job, states, seconds = wait_job(client, job_id)
    created = bundles(client) - before
    assert len(created) == 1, 'the draft created %d bundles' % len(created)
    bundle = created.pop()
    modules = client.get('/api/adventure/library/' + bundle + '/modules').json()
    return dict(draft=draft_id, bundle=bundle, job=job.get('status'), states=states,
                seconds=seconds, message=(job.get('message') or ''),
                pages=job.get('pages'), chapters=job.get('chapters'),
                modules=[dict(title=row['title'], pages=row.get('source_pages'),
                              ready=row.get('ready')) for row in modules])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:19318')
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    log = dict(base=args.base, started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    wanted = [name for name in BASELINE if not args.only or args.only in name]
    with httpx.Client(base_url=args.base, timeout=600.0) as client:
        health = client.get('/api/health')
        health.raise_for_status()
        log['health'] = health.json()
        witchlight = BOOK / 'The Wild Beyond the Witchlight RUS.pdf'
        result = upload_into_draft(client, witchlight)
        ranges = [tuple(row['pages'][0]) if isinstance(row.get('pages'), list)
                  and row['pages'] and isinstance(row['pages'][0], list)
                  else tuple(row['pages']) for row in result['modules']]
        log['witchlight'] = dict(result, ranges=ranges,
                                 matches_card=ranges == EXPECTED_RANGES)
        print('WITCHLIGHT modules=%d chapters=%s %ss %s' % (
            len(result['modules']), result['chapters'], result['seconds'], result['message']), flush=True)
        for row in result['modules']:
            print('   %-55s %s ready=%s' % (row['title'][:55], row['pages'], row['ready']), flush=True)
        log['regression'] = {}
        for name in wanted:
            path = BOOKS / name
            if not path.exists():
                log['regression'][name] = dict(error='book is not on this machine')
                print('MISSING', name, flush=True)
                continue
            row = upload_into_draft(client, path)
            log['regression'][name] = dict(modules=len(row['modules']), chapters=row['chapters'],
                                           seconds=row['seconds'], message=row['message'],
                                           baseline=BASELINE[name],
                                           kept=len(row['modules']) >= BASELINE[name])
            print('%-52s modules=%d (baseline %d) chapters=%s %ss' % (
                name, len(row['modules']), BASELINE[name], row['chapters'], row['seconds']), flush=True)
    SUMMARY.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', SUMMARY, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
