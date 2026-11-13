"""r167 live run: a printed reward's experience reaches the Nat20 counter.

Not a product module: this is the evidence script for card r167. It talks to a
separate uvicorn process (scripts/start_cloud.py --offline-mock) on its own port
and its own profile (runtime/r167-live), so no paid model is called.

What it does, in the order a player would:

1. starts a Nat20 campaign through HTTP;
2. assembles a reviewed module whose opening event pays one printed reward;
3. reads experience and level before the turn;
4. plays one ordinary turn, which drains the pending reward on the Nat20 screen;
5. reads experience, level, the engine's own reward receipts and the words the
   player was shown, then repeats the turn to prove nothing is paid twice.

Every fact goes to reports/r167-run.json.
"""
import argparse
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / 'reports' / 'r167-run.json'
DATABASE = ROOT / 'runtime' / 'r167-live' / 'solo.sqlite3'
TEXT = 'Осматриваюсь'


def pdf_bytes():
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(300, 200)
    writer.add_metadata({'/Title': 'r167 · Источник награды'})
    raw = io.BytesIO()
    writer.write(raw)
    return raw.getvalue()


def wait_job(client, job, limit=180):
    started = time.monotonic()
    while time.monotonic() - started < limit:
        body = client.get('/api/adventure/jobs/' + job).json()
        if body.get('status') in ('complete', 'failed', 'cancelled'):
            return body
        time.sleep(0.25)
    raise SystemExit('import job did not finish: ' + job)


def state_of(client, cid):
    """The public projection plus the internal facts the ledger keeps."""
    public = client.get('/api/campaigns/' + cid).json().get('state') or {}
    with sqlite3.connect('file:%s?mode=ro' % DATABASE, uri=True) as db:
        version, raw = db.execute('SELECT version,state FROM campaigns WHERE id=?', (cid,)).fetchone()
    state = json.loads(raw)
    nat20 = state.get('nat20') or {}
    return dict(
        version=int(version),
        experience=int(nat20.get('experience') or 0),
        receipts=[dict(id=r.get('id'), experience=r.get('experience'), kind=r.get('kind'),
                       page=r.get('page'), encounter_id=r.get('encounter_id'),
                       outcome_sha256=r.get('outcome_sha256')) for r in nat20.get('rewards') or []],
        build_level=int((nat20.get('definition') or {}).get('build', {}).get('level') or 0),
        hero_level=(public.get('hero') or {}).get('level'),
        hero_experience=(public.get('hero') or {}).get('experience'),
        adventure_rewards=(state.get('adventure') or {}).get('rewards') or {},
        deferred=[row.get('kind') for row in (state.get('adventure') or {}).get('deferred') or []],
    )


def player_notices(cid, request_ids):
    """The words the turn wrote for the player, read back from the journal.

    The HTTP answer of a Nat20 command is the engine's own event; the scenario
    notices ride in the stored results, so they are read here from the same rows
    the next screen turn loads.
    """
    found = []
    with sqlite3.connect('file:%s?mode=ro' % DATABASE, uri=True) as db:
        rows = db.execute('SELECT e.results FROM events e JOIN actions a ON a.id=e.action '
                          'WHERE a.campaign=? AND a.request_id IN (%s) ORDER BY e.rowid'
                          % ','.join('?' * len(request_ids)), (cid, *request_ids)).fetchall()
    for (raw,) in rows:
        for result in json.loads(raw) or []:
            for item in result.get('adventure_events') or []:
                if item.get('text'):
                    found.append(item['text'])
    return found


def main():
    global DATABASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:19318')
    parser.add_argument('--database', default='runtime/r167-live/solo.sqlite3')
    args = parser.parse_args()
    DATABASE = ROOT / args.database
    log = dict(base=args.base, database=str(DATABASE),
               started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    with httpx.Client(base_url=args.base, timeout=180.0) as client:
        health = client.get('/api/health')
        health.raise_for_status()
        log['health'] = health.json()

        campaign = client.post('/api/nat20/campaigns', json={'name': 'r167 · Опыт награды'})
        campaign.raise_for_status()
        cid = campaign.json()['id']

        created = client.post('/api/adventure/library',
                              json={'title': 'r167 · Награда из книги', 'metadata': {'rights': 'synthetic'}})
        created.raise_for_status()
        bundle = created.json()['id']
        uploaded = client.post('/api/adventure/library/' + bundle + '/files',
                               files={'file': ('r167-reward.pdf', pdf_bytes(), 'application/pdf')},
                               data={'purpose': 'book'})
        uploaded.raise_for_status()
        job = wait_job(client, uploaded.json()['id'])
        assert job.get('status') == 'complete', job

        client.post('/api/adventure/review/session', json={'confirm_spoilers': True}).raise_for_status()
        review = client.get('/api/adventure/review/library/' + bundle).json()
        document = next(row['id'] for row in review['documents'] if row.get('original_name') == 'r167-reward.pdf')
        log['import'] = dict(bundle=bundle, document=document, pages=job.get('pages'),
                             status=job.get('status'))

        rule = dict(id='purse-xp', amount=50, page=1, reason='Награда напечатана в книге r167')
        definition = dict(
            id='r167-opening', title='r167 · Награда за находку', ruleset='dnd2024', ruleset_version=1,
            source_ranges=[dict(document=document, first_page=1, last_page=2)],
            events=[dict(id='purse', category='timed', after_minutes=0,
                         source=dict(document=document, page=1, review='confirmed'),
                         effects=[dict(kind='reward', target=rule['id'], text='Награда за находку')])],
            progression=dict(mode='experience', rewards=[rule]))
        saved = client.put('/api/adventure/review/library/' + bundle + '/definition',
                           json={'definition': definition, 'confirmed': True})
        saved.raise_for_status()
        attached = client.post('/api/adventure/review/campaigns/' + cid + '/attach',
                               json={'bundle': bundle, 'request_id': 'r167-attach', 'confirm': True})
        attached.raise_for_status()
        client.delete('/api/adventure/review/session').raise_for_status()

        before = state_of(client, cid)
        screen = client.get('/api/campaigns/' + cid + '/screen').json()
        offered = [row['parameters'] for row in screen.get('commands') or []]
        choice = next((row for row in offered if row.get('kind') == 'check'), None)
        log['offered'] = offered
        if choice is not None:
            first = client.post('/api/campaigns/' + cid + '/commands',
                                json={'request_id': 'r167-live-1', 'expected_version': before['version'],
                                      'action': choice})
            played = choice['kind']
        else:
            first = client.post('/api/campaigns/' + cid + '/command-turn',
                                json={'request_id': 'r167-live-1', 'text': TEXT,
                                      'expected_version': before['version']})
            played = TEXT
        first.raise_for_status()
        after = state_of(client, cid)
        if choice is not None:
            second = client.post('/api/campaigns/' + cid + '/commands',
                                 json={'request_id': 'r167-live-2', 'expected_version': after['version'],
                                       'action': choice})
        else:
            second = client.post('/api/campaigns/' + cid + '/command-turn',
                                 json={'request_id': 'r167-live-2', 'text': TEXT,
                                       'expected_version': after['version']})
        second.raise_for_status()
        repeated = state_of(client, cid)

        log['campaign'] = cid
        log['rule'] = rule
        log['played'] = played
        log['before'] = before
        log['after'] = after
        log['repeated'] = repeated
        log['player_text'] = [row for row in player_notices(cid, ['r167-live-1'])
                              if 'Опыт' in row or 'Награда' in row or 'уровень' in row]
        log['first_status'] = first.json().get('status')
        log['second_status'] = second.json().get('status')
        SUMMARY.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')

        print('xp      %s -> %s (повтор %s)' % (before['experience'], after['experience'],
                                                repeated['experience']))
        print('уровень %s -> %s (повтор %s)' % (before['build_level'], after['build_level'],
                                                repeated['build_level']))
        print('receipts', json.dumps(after['receipts'], ensure_ascii=False))
        print('adventure rewards', json.dumps(after['adventure_rewards'], ensure_ascii=False))
        print('deferred', before['deferred'], '->', after['deferred'])
        for row in log['player_text']:
            print('игроку:', row)
        return 0


if __name__ == '__main__':
    sys.exit(main())
