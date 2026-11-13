"""A02: приёмка старых кампаний на копии живой базы.

Проверяет ровно то, что просит карточка A02 (Q03):

1. старая кампания открывается: история читается, выгрузка работает;
2. несовместимость видна ДО ввода, по-русски, с тремя действиями;
3. после обновления ``integrity_check`` ok, записей истории не меньше;
4. действие «Уклонение» в кампании со старыми событиями проходит или даёт
   русскую причину (а не отказ обработчика);
5. новая кампания после правок открывается без английских исключений.

Живая база только читается: снимок снимается через SQLite backup API.
Итог печатается JSON-ом и, если указан ``--report DIR``, пишется в отчёт.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIVE = ROOT / 'runtime/web-local/solo.sqlite3'
WORK = ROOT / 'runtime/a02'
OLD = {
    '4c11debb-862f-4e91-b508-845f2e350bf7': 'Тест Тиамат 08.11',
    'b753145c-50e7-4cce-bbbc-e9ab5748f534': 'Проверка стенда r192',
}
CYRILLIC = re.compile(r'[а-яё]', re.I)
#: Английский текст, который видит игрок: латиница целыми словами, не именами
#: полей. Служебные id (check_id, campaign) в отчёт не попадают.
ENGLISH = re.compile(r'\b(?:[A-Za-z]{3,}\s+){2,}[A-Za-z]{3,}\b')


def copy_database(target=None):
    WORK.mkdir(parents=True, exist_ok=True)
    target = Path(target or WORK / 'probe.sqlite3')
    source = sqlite3.connect(f'file:{LIVE}?mode=ro', uri=True)
    dest = sqlite3.connect(target)
    with dest:
        source.backup(dest)
    source.close()
    dest.close()
    return target


def english_lines(payload):
    """Строки интерфейса с английским текстом (а не с id или именем).

    Две колонки: ``english`` — строка без кириллицы целиком (исключение как
    было); ``mixed`` — русская рамка с английской причиной внутри («Движок
    отклонил… Причина от движка: …»). Вторую нельзя прятать: игрок видит
    английскую фразу, даже если рядом есть русские слова.
    """
    found, mixed = [], []

    def walk(value, path=''):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, path + '.' + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + '[' + str(index) + ']')
        elif isinstance(value, str) and len(value) > 12 and ENGLISH.search(value):
            if CYRILLIC.search(value):
                mixed.append(path + ': ' + value[:200])
            else:
                found.append(path + ': ' + value[:200])

    walk(payload)
    return dict(english=found, mixed=mixed)


def screen_packet(store, campaign):
    from solo.play_screen import screen
    from solo.nat20_screen import Nat20Screen
    return screen(store, campaign, nat20_projector=Nat20Screen())


def command(store, campaign, action, request_id):
    from solo.game_commands import GameCommand
    from solo.nat20_api import Nat20GameEngine
    current = store.campaign(campaign)
    return Nat20GameEngine(store).execute(
        campaign, GameCommand(request_id=request_id, expected_version=current['version'], action=action))


def open_campaign(store, campaign):
    from solo.nat20_api import Nat20GameEngine
    from solo.nat20_compat import inspect
    current = store.campaign(campaign)
    record = dict(campaign=campaign, name=current['name'], version=current['version'],
                  history=len(store.history(campaign, limit=100000)))
    record['before'] = inspect(current)
    packet = screen_packet(store, campaign)
    snapshot = packet['snapshot']
    texts = english_lines(dict(snapshot=snapshot, events=packet['events'], commands=packet['commands']))
    record['screen'] = dict(backend=snapshot.get('backend'), commands=len(packet['commands']),
                            events=len(packet['events']), terminal=(snapshot.get('terminal') or {}).get('code'),
                            compatibility=(snapshot.get('compatibility') or {}).get('status'),
                            compatibility_actions=[a['label'] for a in
                                                   (snapshot.get('compatibility') or {}).get('actions') or []],
                            compatibility_summary=(snapshot.get('compatibility') or {}).get('summary'),
                            english=texts['english'], english_mixed=texts['mixed'])
    try:
        Nat20GameEngine(store).restore(campaign)
        record['restore'] = 'ok'
    except ValueError as error:
        record['restore'] = 'отказ: ' + str(error)
    return record


def export_campaign(store, campaign):
    from solo.nat20_compat import export
    bundle = export(store, campaign)
    encoded = json.dumps(bundle, ensure_ascii=False)
    return dict(events=bundle['counts']['events'], messages=bundle['counts']['messages'],
                bytes=len(encoded.encode('utf-8')), format=bundle['format'])


def verify_database(store, campaign):
    from solo.nat20_compat import integrity
    return integrity(store, campaign)


def migration(store, campaign):
    from solo.nat20_compat import migrate
    result = migrate(store, campaign)
    before, after = result['before'], result['after']
    return dict(status=result['status'], migrated=result['migrated'], event=result.get('event_id'),
                backup=result.get('backup'), changes=result['changes'], message=result['message'],
                problems=result['problems'],
                before=dict(version=before['version'], counts=before['counts'],
                            integrity=before['integrity'], broken_links=before['broken_links'],
                            state_sha256=before['state_sha256'][:16]),
                after=dict(version=after['version'], counts=after['counts'],
                           integrity=after['integrity'], broken_links=after['broken_links'],
                           state_sha256=after['state_sha256'][:16]),
                history_kept=result['history_kept'],
                integrity_ok=result['integrity_ok'])


def _outcome(event):
    """Текст исхода из события движка (GameEvent.payload.outcome)."""
    payload = getattr(event, 'payload', None) or {}
    if not payload and isinstance(event, dict):
        payload = (event.get('payload') or (event.get('game_event') or {}).get('payload') or {})
    return str(payload.get('outcome') or payload.get('text') or '')


def dodge_in_old_campaign(store, campaign):
    """«Уклонение» в кампании со старыми событиями: проходит или русская причина."""
    from solo.nat20_api import Nat20GameEngine
    runtime = Nat20GameEngine._bound(store.campaign(campaign))
    encounters = sorted(((runtime.get('definition') or {}).get('encounters') or {}))
    record = dict(encounters=encounters, started=None, dodge=None)
    if encounters:
        try:
            command(store, campaign, dict(kind='start_combat', encounter_id=encounters[0]), 'a02-combat')
            record['started'] = encounters[0]
        except ValueError as error:
            record['started'] = 'отказ: ' + str(error)
    # «Уклонение» — действие своего хода: чужой ход передаётся штатным end_turn,
    # иначе отказ будет русским, но проверка приёмки п.4 ничего не докажет.
    for step in range(6):
        current = store.campaign(campaign)
        view = (Nat20GameEngine._bound(current).get('combat') or {}).get('view') or {}
        initiative = view.get('initiative') or []
        if initiative and initiative[view['current_turn_index']]['entity_id'] == 'hero':
            break
        try:
            command(store, campaign, dict(kind='end_turn'), 'a02-end-' + str(step))
        except ValueError as error:
            record['end_turn'] = 'отказ: ' + str(error)
            break
    try:
        result = command(store, campaign, dict(kind='native_action', operation='dodge'), 'a02-dodge')
        events = list(result['events'])
        effects = [effect for event in events for effect in ((getattr(event, 'payload', None) or {}).get('effects') or [])]
        record['dodge'] = dict(ok=True, events=len(events), text=' '.join(_outcome(e) for e in events)[:300],
                               effects=effects)
    except ValueError as error:
        text = str(error)
        record['dodge'] = dict(ok=False, russian=bool(CYRILLIC.search(text)) and not ENGLISH.search(text),
                               text=text)
    return record


def new_campaign(store):
    """Сохранение НОВОЙ кампании после правок читается без ошибок."""
    from solo.nat20_api import Nat20GameEngine
    engine = Nat20GameEngine(store)
    created = engine.create_campaign('A02 · новая кампания')
    record = dict(campaign=created['id'], name=created['name'])
    packet = screen_packet(store, created['id'])
    snapshot = packet['snapshot']
    texts = english_lines(dict(snapshot=snapshot, events=packet['events'], commands=packet['commands']))
    record['screen'] = dict(backend=snapshot.get('backend'), commands=len(packet['commands']),
                            terminal=(snapshot.get('terminal') or {}).get('code'),
                            compatibility=(snapshot.get('compatibility') or {}).get('status'),
                            english=texts['english'], english_mixed=texts['mixed'])
    try:
        command(store, created['id'], dict(kind='check', check_id='athletics'), 'a02-new-check')
        record['action'] = 'прошло'
    except ValueError as error:
        record['action'] = str(error)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default='')
    parser.add_argument('--database', default='')
    args = parser.parse_args()
    from solo.storage import Store
    path = Path(args.database) if args.database else copy_database()
    store = Store(path)
    summary = dict(database=str(path), live=str(LIVE), campaigns={}, new_campaign=None)
    for identity, title in OLD.items():
        record = open_campaign(store, identity)
        record['export'] = export_campaign(store, identity)
        record['verify'] = verify_database(store, identity)
        if record['before']['status'] != 'ok' and record['before']['migratable']:
            record['migration'] = migration(store, identity)
            record['after_open'] = open_campaign(store, identity)
            record['after_verify'] = verify_database(store, identity)
        if title.startswith('Тест Тиамат'):
            record['dodge'] = dodge_in_old_campaign(store, identity)
        summary['campaigns'][title] = record
    summary['new_campaign'] = new_campaign(store)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.report:
        folder = ROOT / args.report
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'probe.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
        print('report:', folder / 'probe.json')


if __name__ == '__main__':
    main()
