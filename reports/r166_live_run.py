"""r166 live run: 20 turns + save/undo/restore on the offline-mock server.

Not a product module: this is the evidence script for card r166. It talks to a
separate uvicorn process started by scripts/start_cloud.py --offline-mock on its
own port and its own profile (runtime/r166-live), so no paid model is called.

What it does, in the order a player would:

1. imports a real book PDF into the library (Hoard of the Dragon Queen RUS.pdf);
2. opens the review screen and confirms one printed map for the players;
3. starts a Nat20 campaign and binds the first reviewed module to it;
4. plays 20 mixed turns — observation, checks, a combat (end_turn / attack /
   rest), a confirmed map laid on the table, an unconfirmed material refused, a
   replayed request id, a repeated reveal;
5. saves, undoes, restores, and plays one more turn after the restore.

Every turn is appended to reports/r166-progress.jsonl as one JSON line; the
save/restore part is written to reports/r166-run.json.
"""
import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'runtime' / 'books' / 'Hoard of the Dragon Queen RUS.pdf'
PROGRESS = ROOT / 'reports' / 'r166-progress.jsonl'
SUMMARY = ROOT / 'reports' / 'r166-run.json'
TITLE = 'r166 · Клад Королевы Драконов'
DATABASE = ROOT / 'runtime' / 'r166-live' / 'solo.sqlite3'


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(client, cid, database):
    """The campaign's own facts plus what the player screen is shown.

    `known_media` and `deferred` are not part of the HTTP projection: the screen
    gets `materials`, so reading the public answer alone reports "no map, nothing
    pending" for every single turn. The profile is on this machine, so the
    internal facts are read from it read-only while the turn itself is played
    through HTTP.
    """
    body = client.get('/api/campaigns/' + cid).json()
    public = body['state']
    adventure = public.get('adventure') or {}
    hero = public.get('hero') or {}
    import sqlite3
    with sqlite3.connect('file:%s?mode=ro' % database, uri=True) as db:
        row = db.execute('SELECT version,state FROM campaigns WHERE id=?', (cid,)).fetchone()
        applied = db.execute('SELECT count(*) FROM events WHERE campaign=? AND undone=0', (cid,)).fetchone()[0]
    version, raw = row
    state = json.loads(raw)
    inner = state.get('adventure') or {}
    inner_hero = state.get('hero') or {}
    return dict(
        version=int(version),
        journal=applied,
        known_media=list(inner.get('known_media') or []),
        materials=[row.get('id') for row in adventure.get('materials') or []],
        deferred=[row.get('kind') for row in (inner.get('deferred') or [])],
        hp=inner_hero.get('hp', hero.get('hp')),
        hp_max=inner_hero.get('max_hp', hero.get('max_hp')),
        resources=len(inner_hero.get('resources') or {}),
        level=inner_hero.get('level'),
        turn=state.get('turn'),
        state_hash=canonical(state),
    )


def wait_job(client, job, limit=600):
    started = time.monotonic()
    seen = []
    while time.monotonic() - started < limit:
        body = client.get('/api/adventure/jobs/' + job).json()
        status = body.get('status')
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in ('complete', 'failed', 'cancelled'):
            return body, seen, time.monotonic() - started
        time.sleep(0.5)
    raise SystemExit('import job did not finish in %ss: %s' % (limit, seen))


def upload_book(client, bundle, base_log, label='import'):
    """Upload the book and wait for the local extractor; auto-split needs auto_split."""
    started = time.monotonic()
    with PDF.open('rb') as handle:
        uploaded = client.post('/api/adventure/library/' + bundle + '/files',
                               files={'file': (PDF.name, handle, 'application/pdf')},
                               data={'purpose': 'book'})
    uploaded.raise_for_status()
    job, seen, seconds = wait_job(client, uploaded.json()['id'])
    base_log[label] = dict(job=job.get('status'), states=seen, seconds=round(seconds, 1),
                           pages=job.get('pages'), message=(job.get('message') or '')[:200])
    assert job.get('status') == 'complete', job
    return job


def prepare(client, base_log):
    """A bundle with a real book imported and its chapters cut into modules.

    ``auto_split_book`` only touches a bundle whose metadata asks for it — that is
    how the preparation dialog creates one. A bundle assembled by hand keeps the
    reviewed split path, so a book uploaded without this flag imports zero
    modules and the review screen honestly offers none. Re-uploading into such a
    bundle does not help either: ``enqueue`` answers with the finished job for the
    same document and purpose, so no second worker ever runs.
    """
    metadata = {'auto_split': True, 'edition': '2024'}
    for row in client.get('/api/adventure/library').json():
        if row.get('title') != TITLE or not row.get('import_complete'):
            continue
        modules = client.get('/api/adventure/library/' + row['id'] + '/modules').json()
        if modules:
            base_log['library'] = dict(bundle=row['id'], reused=True, modules=len(modules))
            return row['id']
    created = client.post('/api/adventure/library', json={'title': TITLE, 'metadata': metadata})
    created.raise_for_status()
    bundle = created.json()['id']
    job = upload_book(client, bundle, base_log)
    base_log['library'] = dict(bundle=bundle, reused=False, message=(job.get('message') or '')[:120])
    return bundle


def confirm_map(client, bundle, base_log):
    """Confirm one printed map for the players, and keep one material unconfirmed.

    A page that is a whole scan carries no written name, so it is preferred last:
    «Карта · стр. 6 (вся страница, прозы вокруг нет)» is not a title a player says.
    """
    review = client.get('/api/adventure/review/library/' + bundle).json()
    materials = [row for row in review['materials'] if row.get('kind') != 'page']
    maps = [row for row in materials if row.get('kind') == 'map']
    assert maps, 'the book printed no map the review screen could offer'
    named = [row for row in maps if 'вся страница' not in (row.get('caption') or '')]
    chosen = (named or maps)[0]
    name = (chosen.get('caption') or chosen.get('public_name') or '').strip() or 'Карта из книги'
    saved = client.put('/api/adventure/review/materials/' + chosen['id'],
                       json={'scope': 'player', 'name': name, 'confirmed': True, 'sanitized': True})
    saved.raise_for_status()
    waiting = next((row for row in materials
                    if row['id'] != chosen['id'] and row.get('status') != 'confirmed'
                    and (row.get('caption') or '').strip()
                    and not (row.get('caption') or '').startswith('Иллюстрация ·')
                    and row.get('page')), None)
    base_log['materials'] = dict(total=len(materials), maps=len(maps),
                                 confirmed=dict(id=chosen['id'], page=chosen['page'], name=name),
                                 waiting=dict(id=waiting['id'], page=waiting['page'],
                                              name=(waiting.get('caption') or '')) if waiting else None)
    return name, chosen['page'], waiting


def play_turn(client, cid, number, label, *, text=None, action=None, request_id=None):
    """One turn, exactly as the browser sends it, with the state read back."""
    before = snapshot(client, cid, DATABASE)
    payload = {'request_id': request_id, 'expected_version': before['version']}
    if action is not None:
        payload['action'] = action
        path = '/api/campaigns/' + cid + '/commands'
    else:
        payload['text'] = text
        path = '/api/campaigns/' + cid + '/command-turn'
    started = time.monotonic()
    response = client.post(path, json=payload)
    seconds = time.monotonic() - started
    after = snapshot(client, cid, DATABASE)
    try:
        body = response.json()
    except ValueError:
        body = {}
    record = dict(turn=number, label=label, request_id=request_id,
                  kind='command' if action is not None else 'text',
                  issued=action.get('kind') if action else text,
                  http=response.status_code, status=body.get('status'),
                  version_before=before['version'], version_after=after['version'],
                  journal=after['journal'], known_media=after['known_media'],
                  materials=after['materials'],
                  deferred=after['deferred'], hp=after['hp'], hp_max=after['hp_max'],
                  resources=after['resources'], turn_no=after['turn'],
                  seconds=round(seconds, 3),
                  narrative=(body.get('narrative') or '')[:180],
                  event=(body.get('events') or [{}])[0].get('id') if body.get('events') else None)
    if response.status_code >= 400:
        record['error'] = str(body.get('detail'))[:300]
    return record, body


def offered(client, cid):
    """The commands the screen actually offers right now (never invented)."""
    screen = client.get('/api/campaigns/' + cid + '/screen').json()
    return [row['parameters'] for row in screen.get('commands') or []]


def main():
    global DATABASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:19317')
    parser.add_argument('--database', default='runtime/r166-live/solo.sqlite3')
    parser.add_argument('--turns', type=int, default=20)
    parser.add_argument('--keep', action='store_true', help='keep the previous jsonl instead of rewriting it')
    args = parser.parse_args()
    DATABASE = ROOT / args.database
    log = dict(base=args.base, database=str(DATABASE), started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    if not args.keep:
        PROGRESS.write_text('', encoding='utf-8')
    with httpx.Client(base_url=args.base, timeout=180.0) as client:
        health = client.get('/api/health')
        health.raise_for_status()
        log['health'] = health.json()
        bundle = prepare(client, log)
        review = client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
        review.raise_for_status()
        map_name, map_page, waiting = confirm_map(client, bundle, log)
        modules = client.get('/api/adventure/library/' + bundle + '/modules').json()
        ready = [row for row in modules if row.get('ready')]
        assert ready, 'the imported book has no reviewed module'
        module = ready[0]
        campaign = client.post('/api/nat20/campaigns', json={'name': 'r166 · 20 ходов'})
        campaign.raise_for_status()
        cid = campaign.json()['id']
        attached = client.post('/api/adventure/review/campaigns/' + cid + '/attach',
                               json={'bundle': bundle, 'request_id': 'r166-attach', 'confirm': True,
                                     'module': module['id']})
        attached.raise_for_status()
        log['binding'] = dict(campaign=cid, module=module['id'], title=module['title'],
                              modules=len(modules), ready=len(ready), pages=module.get('source_pages'))
        client.delete('/api/adventure/review/session')

        rows = []
        reveal_text = 'Показываю игрокам карту «%s»' % map_name
        if waiting and waiting.get('name'):
            refusal_text = 'Показываю игрокам «%s»' % waiting['name']
        elif waiting:
            refusal_text = 'Раздаю карту со стр. %s' % waiting['page']
        else:
            refusal_text = 'Раздаю письмо барона'

        def take(number, label, **kw):
            record, body = play_turn(client, cid, number, label, **kw)
            rows.append(record)
            with PROGRESS.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            print('%02d %-24s %s ver %s->%s j=%s media=%s deferred=%s hp=%s %.2fs %s' % (
                number, label, record['http'], record['version_before'], record['version_after'],
                record['journal'], record['known_media'], record['deferred'], record['hp'],
                record['seconds'], record['narrative'][:60]), flush=True)
            return record, body

        number = 0

        def pick(*wanted):
            """A command the screen offers right now, in the order it is preferred."""
            available = offered(client, cid)
            return next((row for kind in wanted for row in available if row.get('kind') == kind), None)

        def screen_turn(label, *wanted, text='Осматриваюсь'):
            """One mechanical turn; if the screen offers none of it, say so honestly."""
            nonlocal number
            number += 1
            choice = pick(*wanted)
            if choice is None:
                record, _ = take(number, label + ' (экран не предложил)', text=text,
                                 request_id='r166-t%02d' % number)
                return record
            record, _ = take(number, label + ':' + choice['kind'], action=choice,
                             request_id='r166-t%02d' % number)
            return record

        def text_turn(label, text, request_id=None):
            nonlocal number
            number += 1
            record, _ = take(number, label, text=text, request_id=request_id or 'r166-t%02d' % number)
            return record

        # 1-8: what a table does before a fight — look, roll, rest, lay the map
        # out (twice: once as a replay, once as the same sentence said again),
        # and try to show a handout nobody has confirmed.
        text_turn('осмотр', 'Осматриваюсь')
        screen_turn('проверка Сл', 'check')
        screen_turn('проверка Сл', 'check')
        screen_turn('отдых', 'short_rest', 'long_rest')
        text_turn('карта на стол', reveal_text, 'r166-t05')
        text_turn('карта на стол (повтор)', reveal_text, 'r166-t05')
        text_turn('карта повторно', reveal_text)
        text_turn('неподтверждённый', refusal_text)

        # 9-…: one fight, played the way the screen allows it. Second Wind is a
        # real button on the screen, so a wounded hero uses it instead of trading
        # blows until the death saves start — the point of the run is the session,
        # not a demonstration of dying.
        screen_turn('бой: начать', 'start_combat')
        for _ in range(11):
            if number >= args.turns:
                break
            available = offered(client, cid)
            kinds = {row.get('kind') for row in available}
            if 'end_combat' in kinds:
                screen_turn('бой: конец', 'end_combat')
                break
            hero_hp = rows[-1]['hp'] if rows else None
            if isinstance(hero_hp, int) and hero_hp <= 6 and 'use_feature' in kinds:
                screen_turn('бой: второе дыхание', 'use_feature')
            elif 'attack' in kinds:
                screen_turn('бой: атака', 'attack')
            elif 'move' in kinds:
                screen_turn('бой: сближение', 'move')
            else:
                screen_turn('бой: ход', 'end_turn')
        while number < args.turns:
            text_turn('осмотр', 'Осматриваюсь')

        # --- save / undo / restore ---------------------------------------
        saved_state = snapshot(client, cid, DATABASE)
        save = client.post('/api/campaigns/' + cid + '/saves', json={'name': 'r166 · 20 ходов'})
        save.raise_for_status()
        save_id = save.json()['id']
        undone = client.post('/api/campaigns/' + cid + '/undo')
        undone.raise_for_status()
        after_undo = snapshot(client, cid, DATABASE)
        restored = client.post('/api/campaigns/' + cid + '/restore/' + save_id)
        restored.raise_for_status()
        after_restore = snapshot(client, cid, DATABASE)
        # The turn after the restore is proof, not one of the twenty: it goes into
        # the summary only, so the progress file stays exactly one line per turn.
        next_turn, _ = play_turn(client, cid, args.turns + 1, 'ход после восстановления',
                                 request_id='r166-t21', text='Осматриваюсь')

        seconds = [row['seconds'] for row in rows]
        versions = [row['version_after'] for row in rows]
        summary = dict(log,
                       turns=len(rows), errors=[row for row in rows if row.get('error')],
                       version_regressions=[(a, b) for a, b in zip(versions, versions[1:]) if b < a],
                       deferred_left=[row['turn'] for row in rows if row['deferred']],
                       refusals=[dict(turn=row['turn'], label=row['label'], status=row['status'],
                                      narrative=row['narrative']) for row in rows
                                 if row.get('status') in ('unsupported', 'clarification', 'rejected')],
                       timing=dict(median=round(statistics.median(seconds), 3), maximum=round(max(seconds), 3),
                                   minimum=round(min(seconds), 3), total=round(sum(seconds), 1)),
                       save_restore=dict(save_id=save_id, saved=saved_state, undo=after_undo,
                                         restored=after_restore,
                                         state_restored=saved_state['state_hash'] == after_restore['state_hash'],
                                         version_saved=saved_state['version'],
                                         version_undo=after_undo['version'],
                                         version_restored=after_restore['version'],
                                         next_turn=next_turn))
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps({k: summary[k] for k in ('turns', 'timing', 'errors', 'deferred_left',
                                                  'version_regressions')}, ensure_ascii=False))
        print(json.dumps(summary['save_restore'], ensure_ascii=False))
        return 0


if __name__ == '__main__':
    sys.exit(main())
