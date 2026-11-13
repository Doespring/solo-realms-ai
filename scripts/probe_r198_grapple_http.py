"""r198 live run: «Вырваться из захвата» по HTTP в ШТАТНОЙ кампании.

Приложение поднимается в фоновом потоке этого процесса на 127.0.0.1:19221
со своей базой `runtime/r198-probe` и `CommandMockLLM` (ни одного платного
вызова). Кампании штатные (`create_campaign`, определение
`fighter-road-2024-v1`, встреча `basic`) с ЗАФИКСИРОВАННЫМ сидом — иначе
бросок спасения и бросок освобождения не воспроизвести. Все шаги игрока идут
ровно тем путём, которым ходит игрок: `POST /api/campaigns/<id>/commands`,
экран — `GET /api/campaigns/<id>/screen`.

Сценарий 1 — «существо хватает героя»: три раунда ходов монстра, состояние
героя и кнопки печатаются. Сценарий 2 — что делает кнопка освобождения, когда
условие `grappled` на герое действительно стоит, и как отличить успех броска
от провала. Порт 19215 (живой стенд владельца) не трогается.
"""
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

PORT = 19221
BASE = 'http://127.0.0.1:%d' % PORT
DATA = ROOT / 'runtime' / 'r198-probe'


def serve(path, port):
    import uvicorn
    from solo.app import create_app
    from solo.llm import CommandMockLLM
    app = create_app(path, CommandMockLLM({}), local_port=port, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.status, json.loads(answer.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


def wait_ready(deadline=120):
    started = time.time()
    while time.time() - started < deadline:
        try:
            if call('GET', '/api/health')[0] == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def actors_of(snapshot):
    """Снимок команды — публичная `GameSnapshot`, экран — `screen-v1`."""
    combat = (snapshot or {}).get('combat') or {}
    return combat.get('actors') or (snapshot or {}).get('actors') or []


def movement(row):
    """Остаток движения: экран отдаёт список ресурсов, публичный снимок — словарь."""
    resources = row.get('resources')
    if isinstance(resources, dict):
        entry = (resources.get('movement_remaining') or {})
        return entry.get('current') if isinstance(entry, dict) else entry
    for item in resources or []:
        if item.get('id') == 'movement_remaining':
            return item.get('current')
    return None


def conds(rows):
    return [row if isinstance(row, str) else row.get('condition') for row in rows or []]


def operations(screen):
    return [row.get('parameters', {}).get('operation') for row in screen.get('commands') or []
            if row.get('parameters', {}).get('kind') == 'native_action']


def labels(screen):
    return [row.get('label') for row in screen.get('commands') or []
            if row.get('parameters', {}).get('kind') == 'native_action']


def report(label, value):
    print(label + ':', json.dumps(value, ensure_ascii=False))


def make_campaign(name, seed):
    """Штатная кампания с зафиксированным сидом (своя база, до подъёма сервера)."""
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store
    store = Store(DATA / 'r198-http.sqlite3')
    created = Nat20GameEngine(store).create_campaign(name, seed=seed)
    runtime = store.campaign(created['id'])['state']['nat20']
    assert runtime['seed'] == seed, runtime['seed']
    return created['id']


def session(cid):
    """Обёртки над HTTP: версия, команда, экран, бросок монстров до хода героя."""

    def version():
        return call('GET', '/api/campaigns/' + cid)[1]['version']

    def command(request_id, **action):
        status, packet = call('POST', '/api/campaigns/%s/commands' % cid,
                              dict(request_id=request_id, expected_version=version(), action=action))
        if status != 200:
            return status, (packet.get('detail') or packet), packet
        events = packet.get('events') or []
        text = ((events[0].get('payload') or {}).get('outcome') if events else '') or packet.get('feedback') or ''
        return status, text, packet

    def screen():
        return call('GET', '/api/campaigns/%s/screen' % cid)[1]

    def hero_row(packet=None):
        snapshot = (packet or {}).get('snapshot') or screen()['snapshot']
        if 'hero' in snapshot:
            return snapshot['hero']
        return next(row for row in actors_of(snapshot) if row['id'] == 'hero')

    def hero_turn(limit=24, tag='yield'):
        """Ход героя: пока ходит враг, экран не предлагает действий.

        Возвращает (экран, события ходов монстров) — иначе «монстры просто
        ничего не делали» не отличить от «монстры ходили и не схватили».
        """
        seen = []
        for attempt in range(limit):
            current = screen()
            snapshot = current.get('snapshot') or {}
            combat = snapshot.get('combat') or {}
            if combat.get('active') and combat.get('current_actor') == 'hero':
                return current, seen
            if not combat.get('active'):
                return current, seen
            status, text, packet = command('%s-%d' % (tag, attempt), kind='end_turn')
            seen += [event.get('type') for event in (packet.get('events') or [])]
        return screen(), seen

    return dict(version=version, command=command, screen=screen, hero=hero_row, hero_turn=hero_turn)


def creature_cannot_grapple(seed=1):
    """Сценарий 1: раунд за раундом ходит гоблин — что стоит на герое."""
    print('\n== Сценарий 1: существо против героя (штатная кампания, сид %d)' % seed)
    s = session(make_campaign('r198-существо', seed))
    status, text, packet = s['command']('start', kind='start_combat', encounter_id='basic')
    print('start_combat →', status, text)
    screen, _ = s['hero_turn'](tag='open')
    for round_index in range(3):
        row = screen['snapshot']['hero']
        report('раунд %d: состояние героя' % round_index, conds(row['conditions']))
        report('раунд %d: клетки и HP' % round_index,
               dict(hero_hp=row.get('hp'), actors=[(a['id'], a['hp']) for a in
                                                   screen['snapshot']['combat']['actors']]))
        report('раунд %d: кнопки' % round_index, labels(screen))
        report('раунд %d: «Вырваться» предложена' % round_index,
               'escape_grapple' in operations(screen))
        # Герой пропускает ход — вместе с этим вызовом ходят монстры.
        status, text, packet = s['command']('pass-%d' % round_index, kind='end_turn')
        # Событие хода несёт `payload.effects`: движение и бросок атаки монстра.
        report('раунд %d: ход монстров →' % round_index,
               dict(status=status, text=text,
                    effects=[effect for event in packet.get('events') or []
                             for effect in ((event.get('payload') or {}).get('effects') or [])]))
        screen, _ = s['hero_turn'](tag='monster-%d' % round_index)
    # Герой сам захватывает гоблина: путь «игрок держит врага» работает.
    status, text, packet = s['command']('move', kind='move', position=dict(x=3, y=1, z=0))
    print('move →', status, text)
    status, text, packet = s['command']('grapple', kind='native_action', operation='grapple',
                                       target_id='goblin')
    print('Захватить гоблина →', status, text)
    foe = next(row for row in actors_of(packet['snapshot']) if row['id'] == 'goblin')
    report('после захвата: состояния героя / гоблина',
           dict(hero=conds(s['hero'](packet)['conditions']), goblin=conds(foe['conditions'])))
    report('экран после захвата: кнопки', labels(s['screen']()))


def rid(text):
    """`request_id` — только латиница/цифры/точка/дефис: подпись шага в отчёте может быть русской."""
    cleaned = ''.join(ch if ch.isascii() and (ch.isalnum() or ch in '_.-') else '-' for ch in text)
    return cleaned[:80]


def grapple_self(s, tag, tries=8):
    """Поставить `grappled` на героя: по одному броску спасения за ход."""
    for attempt in range(tries):
        s['hero_turn'](tag='%s-to-hero-%d' % (tag, attempt))
        status, text, packet = s['command'](rid('%s-%d' % (tag, attempt)), kind='native_action',
                                            operation='grapple', target_id='hero')
        if status != 200:
            print('ОТКАЗ захвата героя:', status, text)
            return None
        if conds(s['hero'](packet)['conditions']):
            return text
        print('  попытка %d:' % attempt, text)
    return None


def press_escape(s, current, tag):
    """Нажать предложенную кнопку освобождения и напечатать бросок и состояние."""
    report('%s: состояние до нажатия' % tag, dict(
        conditions=conds(current['snapshot']['hero']['conditions']),
        hp=current['snapshot']['hero'].get('hp'),
        movement=movement(current['snapshot']['hero'])))
    report('%s: кнопки' % tag, labels(current))
    if 'escape_grapple' not in operations(current):
        print('%s: кнопки «Вырваться» нет — нажимать нечего' % tag)
        return None
    offer = next(row for row in current['commands']
                 if row.get('parameters', {}).get('operation') == 'escape_grapple')
    report('%s: нажатие' % tag, offer.get('parameters'))
    status, text, packet = s['command'](rid(tag + '-press'), kind='native_action',
                                        operation='escape_grapple',
                                        **({'target_id': offer['parameters']['target_id']}
                                           if offer['parameters'].get('target_id') else {}))
    print('%s: «Вырваться» →' % tag, status, text)
    for event in packet.get('events') or []:
        payload = event.get('payload') or {}
        if event.get('type') in ('check_rolled', 'condition_applied', 'condition_removed'):
            report('  событие ' + str(event.get('type')),
                   dict(roll=payload.get('roll_total'), dc=payload.get('dc'),
                        succeeded=payload.get('succeeded'), condition=payload.get('condition')))
    after = s['hero'](packet)
    report('%s: состояние после нажатия' % tag,
           dict(conditions=conds(after['conditions']), movement=movement(after)))
    return 'failure' if 'grappled' in conds(after['conditions']) else 'success'


def escape_with_enemy_adjacent(seed=1):
    """Сценарий 2: герой схвачен, враг рядом — кнопка, подпись, бросок."""
    print('\n== Сценарий 2: захват на герое, враг в 5 футах (сид %d)' % seed)
    s = session(make_campaign('r198-рядом-%d' % seed, seed))
    print('start_combat →', s['command']('start', kind='start_combat', encounter_id='basic')[:2])
    landed = grapple_self(s, 'near')
    if not landed:
        print('ОТКАЗ: захват на герое не встал')
        return None
    print('захват на герое встал:', landed)
    current, _ = s['hero_turn'](tag='near-back')
    return press_escape(s, current, 'рядом')


def escape_without_adjacent_enemy(seed=1):
    """Сценарий 3: герой схвачен, захватчик не в 5 футах (гоблин схвачен нами)."""
    print('\n== Сценарий 3: захват на герое, враг не рядом (сид %d)' % seed)
    s = session(make_campaign('r198-далеко-%d' % seed, seed))
    print('start_combat →', s['command']('start', kind='start_combat', encounter_id='basic')[:2])
    s['hero_turn'](tag='far-open')
    status, text, packet = s['command']('far-pin', kind='native_action', operation='grapple',
                                        target_id='goblin')
    print('гоблина схватить →', status, text)
    if status != 200 or conds(next(row for row in actors_of(packet['snapshot'])
                                   if row['id'] == 'goblin')['conditions']) != ['grappled']:
        print('ОТКАЗ: гоблин не схвачен — сценарий требует обездвиженного врага')
        return None
    s['hero_turn'](tag='far-back')
    status, text, packet = s['command']('far-step', kind='move', position=dict(x=3, y=2, z=0))
    print('герой отходит →', status, text)
    landed = grapple_self(s, 'far')
    if not landed:
        print('ОТКАЗ: захват на герое не встал')
        return None
    print('захват на герое встал:', landed)
    current, _ = s['hero_turn'](tag='far-read')
    first = press_escape(s, current, 'не рядом')
    if first is None:
        return dict(button=False)
    outcomes = {first: 1}
    for cycle in range(1, 4):
        if len(outcomes) == 2:
            break
        landed = grapple_self(s, 'far-c%d' % cycle)
        if not landed:
            break
        current, _ = s['hero_turn'](tag='far-c%d-back' % cycle)
        outcome = press_escape(s, current, 'не рядом %d' % cycle)
        if outcome:
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return dict(button=True, outcomes=outcomes)


def main():
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    server = serve(str(DATA / 'r198-http.sqlite3'), PORT)
    if not wait_ready():
        print('ОТКАЗ: приложение не поднялось на', PORT)
        return 1
    creature_cannot_grapple(seed=1)
    adjacent = escape_with_enemy_adjacent(seed=1)
    print('\nитог (враг рядом):', json.dumps(adjacent, ensure_ascii=False))
    far = escape_without_adjacent_enemy(seed=1)
    print('\nитог (враг не рядом):', json.dumps(far, ensure_ascii=False))
    server.should_exit = True
    return 0


if __name__ == '__main__':
    sys.exit(main())
