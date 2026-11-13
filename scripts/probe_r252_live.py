"""r252: живая проверка «собранный мир запускается» на копии профиля.

Дефект r250: мир малой книги нёс оба направления одной двусторонней дороги
(`p11→p12` и `p12→p11`), а `import_package` сравнивает маршруты как
неупорядоченную пару — кнопка «Начать» отвечала
`409 {"detail":"Маршрут между узлами уже существует"}`.

Скрипт работает на КОПИИ профиля и своём порту, модель не вызывается:

1. до правки мира — воспроизводит 409 на уже собранном мире;
2. пересобирает мир из готовых блоков (`/retry` → `finalize`, без вызовов модели);
3. `world-readiness` → `confirm-world` → `POST /api/prototype/campaigns` → 200;
4. печатает маршруты до/после и стартовое место кампании.

    .venv/bin/python scripts/probe_r252_live.py \
        --source runtime/web-local --workdir /tmp/r252-live --port 19230
"""
import argparse, json, shutil, sqlite3, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASES = ('solo.sqlite3', 'adventure.sqlite3', 'preparation.sqlite3',
             'cloud-profiles.json', 'cloud-usage.sqlite3', 'cloud-secrets')


def call(base, method, path, body=None, expect=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=600) as answer:
            status, text = answer.status, answer.read().decode()
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode()
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def copy_profile(source, workdir):
    workdir.mkdir(parents=True, exist_ok=True)
    for name in DATABASES:
        origin = source / name
        if origin.is_file():
            shutil.copy2(origin, workdir / name)
        elif origin.is_dir():
            # `cloud-secrets` — каталог с ключом профиля. Сборка мира из готовых
            # блоков не зовёт модель, но сверяет профиль, поэтому копия нужна.
            shutil.copytree(origin, workdir / name, dirs_exist_ok=True)


def find_world(preparation, title):
    """Прогон и черновик, чей собранный мир назван `title`."""
    with sqlite3.connect(preparation) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute('SELECT id,draft,status,result FROM creation_runs ORDER BY created DESC'):
            if not row['result']:
                continue
            result = json.loads(row['result'])
            # Игра импортирует `definition` (входной мир книги), а `full_definition`
            # — весь материал: дубли смотреть в том, что реально пойдёт в кампанию.
            definition = result.get('definition') or result.get('full_definition') or {}
            if (definition.get('title') or '').startswith(title):
                return row['draft'], row['id'], result
    raise SystemExit('В профиле нет собранного мира «' + title + '»')


def routes_of(result):
    definition = result.get('definition') or result.get('full_definition') or {}
    return (definition.get('world') or {}).get('routes') or []


def report_routes(label, routes):
    pairs = [frozenset((route['from'], route['to'])) for route in routes]
    print(label, 'маршрутов', len(routes), 'пар мест', len(set(pairs)),
          'лишних записей', len(routes) - len(set(pairs)))
    for pair in sorted({pair for pair in pairs if pairs.count(pair) > 1}):
        print('   пара дважды:', sorted(pair))


def start_server(workdir, port):
    process = subprocess.Popen(
        [sys.executable, str(ROOT / 'scripts' / 'serve_r238.py'),
         '--data-dir', str(workdir), '--port', str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        base = 'http://127.0.0.1:' + str(port)
        try:
            status, health = call(base, 'GET', '/api/health')
            # Готовность — это ответ приложения; `readiness.status` у профиля без
            # модели честно «unconfigured», и ждать 'ready' значит не дождаться.
            if status == 200 and isinstance(health, dict) and health.get('product'):
                return process, base
        except Exception:
            time.sleep(.2)
            continue
        time.sleep(.2)
    process.terminate()
    output = process.communicate(timeout=20)[0]
    raise SystemExit('Сервер не поднялся:\n' + (output or '')[-2000:])


def campaign_body(draft, revision, build):
    return dict(name='Рив', request_id='r252-live-' + str(int(time.time())),
                build=build, adventure='prepared:' + draft, prepared_spells=[],
                encounter_monsters=[], description='Мир малой книги',
                mechanics_ruleset='dnd2024', rules_confirmed=True, hero_confirmed=True,
                background_confirmed=True, hero_background='Ищет дорогу из Диса',
                adventure_revision=revision)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(ROOT / 'runtime' / 'web-local'))
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--port', type=int, default=19230)
    parser.add_argument('--title', default='Город Дис')
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()
    copy_profile(Path(args.source), workdir)
    draft, run, stored = find_world(workdir / 'preparation.sqlite3', args.title)
    print('черновик', draft, 'прогон', run)
    report_routes('до правки:', routes_of(stored))
    process, base = start_server(workdir, args.port)
    try:
        status, options = call(base, 'GET', '/api/prototype/options')
        build = options['default_build']
        revision = call(base, 'GET', '/api/creation/drafts/' + draft)[1]['revision']
        # 1. Уже собранный (старый) мир: 409 и есть наблюдаемый дефект r250.
        status, body = call(base, 'POST', '/api/prototype/campaigns', campaign_body(draft, revision, build))
        print('старый мир, первая попытка:', status, json.dumps(body, ensure_ascii=False)[:160])
        if status == 409 and 'изменилась' in str(body):
            # Мир перепубликован после подтверждения (r250): сначала подтверждаем
            # заново — иначе до проверки маршрутов дело не доходит.
            readiness = call(base, 'GET', '/api/creation/drafts/' + draft + '/world-readiness')[1]
            call(base, 'POST', '/api/creation/drafts/' + draft + '/confirm-world',
                 dict(expected_revision=readiness['revision'],
                      run_id=readiness.get('run_id') or run, confirm=True))
            revision = call(base, 'GET', '/api/creation/drafts/' + draft)[1]['revision']
            status, body = call(base, 'POST', '/api/prototype/campaigns',
                                campaign_body(draft, revision, build))
            print('старый мир:', status, json.dumps(body, ensure_ascii=False)[:160])
        # 2. Пересборка из готовых блоков: локально, без вызовов модели.
        status, retried = call(base, 'POST', '/api/creation/runs/' + run + '/retry')
        print('retry:', status, retried.get('status') if isinstance(retried, dict) else retried)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status, view = call(base, 'GET', '/api/creation/runs/' + run)
            if view['status'] not in ('queued', 'running'):
                break
            time.sleep(.5)
        print('сборка:', view['status'], '|', view.get('message'))
        rebuilt = sqlite3.connect(workdir / 'preparation.sqlite3').execute(
            'SELECT result FROM creation_runs WHERE id=?', (run,)).fetchone()[0]
        report_routes('после правки:', routes_of(json.loads(rebuilt)))
        # 3. Подтверждение мира и кнопка «Начать».
        readiness = call(base, 'GET', '/api/creation/drafts/' + draft + '/world-readiness')[1]
        print('world-readiness:', readiness.get('world_ready'), readiness.get('reason', ''))
        status, confirmed = call(base, 'POST', '/api/creation/drafts/' + draft + '/confirm-world',
                                 dict(expected_revision=readiness['revision'],
                                      run_id=readiness.get('run_id') or run, confirm=True))
        print('confirm-world:', status)
        revision = call(base, 'GET', '/api/creation/drafts/' + draft)[1]['revision']
        status, launched = call(base, 'POST', '/api/prototype/campaigns', campaign_body(draft, revision, build))
        print('POST /api/prototype/campaigns:', status)
        if status != 200:
            print(json.dumps(launched, ensure_ascii=False)[:600])
            return 1
        campaign = launched['id'] if isinstance(launched, dict) else launched
        status, view = call(base, 'GET', '/api/campaigns/' + str(campaign))
        state = view.get('state') or {}
        places = state.get('locations') or []
        print('кампания', campaign, '| мест в состоянии', len(places),
              '| первое место:', (places[0] or {}).get('name') if places else None,
              '| текущее:', state.get('location'))
        with sqlite3.connect(workdir / 'solo.sqlite3') as db:
            row = db.execute('SELECT state FROM campaigns WHERE id=?', (str(campaign),)).fetchone()
        stored_state = json.loads(row[0]) if row else {}
        print('в базе: мест', len(stored_state.get('locations') or []),
              '| маршрутов', len((stored_state.get('geography') or {}).get('routes') or []),
              '| первое место:', (stored_state.get('locations') or [{}])[0].get('name'))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == '__main__':
    raise SystemExit(main())
