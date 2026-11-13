"""A02: адаптер старых событий, проверка совместимости и обновление на копии.

Проверки идут на одноразовых базах во временном каталоге: живая база не
открывается даже на чтение. Механика — настоящий Nat20, модель — заглушка.
"""
import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_compat import (blocking_world_events, export, inspect, integrity, migrate,
                               world_events)
from solo.nat20_screen import Nat20Screen
from solo.storage import Store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def denied(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('Paid/external APIs are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', denied)


@pytest.fixture
def native(tmp_path):
    store = Store(tmp_path / 'native-a02.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('A02 кампания', seed=11)['id']
    return store, cid


def write_state(store, cid, mutate):
    with store.connection(True) as db:
        current = store.campaign(cid, db)
        state = json.loads(json.dumps(current['state']))
        mutate(state)
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(state), cid))
    return store.campaign(cid)


def make_outdated(store, cid):
    """Кампания, чьё определение разошлось с проверенной регистрацией (S64)."""
    def mutate(state):
        state['nat20']['definition']['title'] = 'Дозор на тракте · старая запись'
    return write_state(store, cid, mutate)


def make_legacy_events(store, cid):
    """События прежнего обработчика списком — так записана «Тест Тиамат 08.11»."""
    def mutate(state):
        state['world'] = dict(settings=dict(mode='sandbox'),
                              events=[dict(id='foe-printed-kobold', after_minutes=0, kind='encounter',
                                           location='Совет Глубоководья', text='Противник из источника: kobold',
                                           npc='', target='', relationship=0,
                                           encounter=dict(environment='room', seed=1)),
                                      dict(id='foe-printed-guard', after_minutes=0, kind='encounter',
                                           location='Глубоководья', text='Противник из источника: guard')],
                              triggered=['foe-printed-guard'])
    return write_state(store, cid, mutate)


# ── адаптер событий прежнего обработчика ─────────────────────────────────────


def test_legacy_event_list_is_read_as_history_and_does_not_block(native):
    store, cid = native
    current = make_legacy_events(store, cid)
    events = world_events(current['state'])
    assert events['format'] == 'world-events-list-v0'
    assert events['historical'] is True and events['blocking'] is False
    assert events['count'] == 2 and events['triggered'] == 1
    assert [item['id'] for item in events['items']] == ['foe-printed-kobold', 'foe-printed-guard']
    assert events['items'][1]['happened'] is True
    assert 'исторические' in events['reason']
    assert blocking_world_events(current['state']) is None


def test_action_passes_in_a_campaign_with_old_events(native):
    """Раньше здесь стоял отказ «Отложенные мировые события…» на любое действие."""
    store, cid = native
    current = make_legacy_events(store, cid)
    command = GameCommand(request_id='a02-check', expected_version=current['version'],
                          action=dict(kind='check', check_id='athletics'))
    runtime = Nat20GameEngine._preflight(current, command)
    assert runtime['hero']['hp_current'] > 0


def test_dodge_passes_in_a_campaign_with_old_events(native):
    """Приёмка п.4: «Уклонение» проходит, а не упирается в отказ обработчика."""
    store, cid = native
    make_legacy_events(store, cid)
    engine = Nat20GameEngine(store)
    current = store.campaign(cid)
    runtime = Nat20GameEngine._bound(current)
    encounters = sorted((runtime.get('definition') or {}).get('encounters') or {})
    assert encounters, 'в определении кампании должно быть зарегистрированное столкновение'
    engine.execute(cid, GameCommand(request_id='a02-combat', expected_version=current['version'],
                                    action=dict(kind='start_combat', encounter_id=encounters[0])))
    # Инициатива может поставить первым противника: ход передаётся штатно, не
    # обходом. «Уклонение» — действие своего хода, и отказ на чужом ходе русский.
    for step in range(6):
        current = store.campaign(cid)
        view = (Nat20GameEngine._bound(current).get('combat') or {}).get('view') or {}
        initiative = view.get('initiative') or []
        turn = initiative[view['current_turn_index']]['entity_id'] if initiative else None
        if turn == 'hero':
            break
        engine.execute(cid, GameCommand(request_id='a02-end-' + str(step), expected_version=current['version'],
                                        action=dict(kind='end_turn')))
    else:
        pytest.fail('герой так и не получил ход: приёмка п.4 не проверена')
    current = store.campaign(cid)
    result = engine.execute(cid, GameCommand(request_id='a02-dodge', expected_version=current['version'],
                                             action=dict(kind='native_action', operation='dodge')))
    payload = result['events'][-1].payload
    assert 'Уклонение' in payload['outcome']
    assert {'type': 'resource',
            'payload': {'target': 'hero', 'resource': 'action_available', 'before': 1, 'after': 0}} in payload['effects']


def test_unreadable_event_still_refuses_with_russian_reason(native):
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(world=dict(events=[dict(id='unbound')])))
    blocked = blocking_world_events(current['state'])
    assert blocked and blocked['code'] == 'world_events_unreadable'
    assert 'без описания' in blocked['reason']
    command = GameCommand(request_id='a02-check', expected_version=current['version'],
                          action=dict(kind='check', check_id='athletics'))
    with pytest.raises(ValueError, match='без описания'):
        Nat20GameEngine._preflight(current, command)


def test_open_scheduler_queue_refuses_and_archives_on_migration(native):
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(world=dict(
        events={'late-omen': 'pending', 'done': 'resolved'}, schedule={'late-omen': 30})))
    events = world_events(current['state'])
    assert events['format'] == 'world-scheduler-v0' and events['blocking'] is True
    assert events['pending'] == ['late-omen']
    result = migrate(store, cid)
    assert result['migrated'] is True
    state = store.campaign(cid)['state']
    assert state['world']['events'] == {}
    assert state['world']['legacy_scheduler']['events'] == {'late-omen': 'pending', 'done': 'resolved'}
    assert state['world']['legacy_scheduler']['schedule'] == {'late-omen': 30}
    assert inspect(store.campaign(cid))['status'] == 'ok'


# ── проверка совместимости до ввода ─────────────────────────────────────────


def test_new_campaign_is_compatible(native):
    store, cid = native
    report = inspect(store.campaign(cid))
    assert report['status'] == 'ok'
    assert [check['id'] for check in report['checks']] == ['schema', 'state', 'ruleset', 'registration', 'revision']
    assert report['actions'] == []


def test_outdated_registration_is_named_in_russian_before_input(native):
    store, cid = native
    make_outdated(store, cid)
    report = inspect(store.campaign(cid))
    assert report['status'] == 'needs_choice' and report['migratable'] is True
    registration = next(check for check in report['checks'] if check['id'] == 'registration')
    assert registration['ok'] is False and registration['code'] == 'registration_changed'
    assert 'проверенной' in registration['detail']
    assert [action['id'] for action in report['actions']] == ['migrate', 'verify', 'export']
    packet = Nat20Screen().project(store.campaign(cid))
    assert packet['commands'] == []
    assert packet['terminal']['code'] == 'compatibility_required'
    assert packet['compatibility']['status'] == 'needs_choice'
    assert packet['hero']['name'] == 'A02 кампания'
    assert 'Не удалось' not in packet['terminal']['message']


def test_history_and_export_work_for_an_incompatible_campaign(native):
    store, cid = native
    engine = Nat20GameEngine(store)
    current = store.campaign(cid)
    engine.execute(cid, GameCommand(request_id='before-break', expected_version=current['version'],
                                    action=dict(kind='check', check_id='athletics')))
    history = len(store.history(cid, limit=1000))
    make_outdated(store, cid)
    packet = Nat20Screen().project(store.campaign(cid))
    bundle = export(store, cid)
    assert len(packet['commands']) == 0
    assert bundle['format'] == 'solo-campaign-export-v1'
    assert bundle['counts']['events'] == history >= 1
    assert bundle['compatibility']['status'] == 'needs_choice'
    assert bundle['state']['hero']['name'] == 'A02 кампания'


def test_screen_and_engine_refusal_are_russian(native):
    store, cid = native
    make_outdated(store, cid)
    current = store.campaign(cid)
    with pytest.raises(ValueError) as refusal:
        Nat20GameEngine(store)._bound(current)
    message = str(refusal.value)
    assert 'Сохранённое определение кампании' in message
    assert not any('a' <= letter.lower() <= 'z' for letter in message)


# ── обновление: копия, изменение, сравнение ─────────────────────────────────


def test_migration_copies_first_then_changes_and_keeps_history(native):
    store, cid = native
    engine = Nat20GameEngine(store)
    current = store.campaign(cid)
    engine.execute(cid, GameCommand(request_id='turn-before-migration', expected_version=current['version'],
                                    action=dict(kind='check', check_id='athletics')))
    before = integrity(store, cid)
    assert before['integrity'] == 'ok'
    make_outdated(store, cid)
    before = integrity(store, cid)

    result = migrate(store, cid)

    assert result['migrated'] is True and result['problems'] == []
    assert result['history_kept'] is True and result['integrity_ok'] is True
    assert result['after']['counts']['events'] >= before['counts']['events']
    assert [change['kind'] for change in result['changes']] == ['registration']
    copy = Path(result['backup'])
    assert copy.is_dir() and (copy / 'snapshot.json').is_file()
    assert (copy / store.path.name).is_file()
    # Копия снята ДО изменения: в ней старое определение, в кампании — текущее.
    import sqlite3
    with sqlite3.connect(copy / store.path.name) as db:
        copied = json.loads(db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()[0])
    assert copied['nat20']['definition']['title'] == 'Дозор на тракте · старая запись'
    assert store.campaign(cid)['state']['nat20']['definition']['title'] == 'Дозор на тракте'
    after = inspect(store.campaign(cid))
    assert after['status'] == 'ok'
    assert Nat20Screen().project(store.campaign(cid))['commands']


def test_migration_is_recorded_in_the_journal(native):
    store, cid = native
    make_outdated(store, cid)
    before = len(store.history(cid, limit=1000))
    result = migrate(store, cid)
    history = store.history(cid, limit=1000)
    assert len(history) == before + 1
    assert history[-1]['id'] == result['event_id']
    assert 'Кампания обновлена' in history[-1]['original']
    assert store.campaign(cid)['version'] == result['after']['version']


def test_migration_refuses_when_nothing_can_be_rebound(native):
    store, cid = native
    def mutate(state):
        state['nat20']['contract_version'] = 'nat20-state-v0'
    current = write_state(store, cid, mutate)
    report = inspect(current)
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    with pytest.raises(ValueError) as refusal:
        migrate(store, cid)
    assert 'нельзя обновить автоматически' in str(refusal.value)
    # Ничего не записано: состояние то же, что было.
    assert store.campaign(cid)['state']['nat20']['contract_version'] == 'nat20-state-v0'


# ── исторические события не меняют мир ───────────────────────────────────────

NOTICE = dict(id='old-notice', after_minutes=0, kind='notice', location='Дозор на тракте',
              text='СТАРОЕ СОБЫТИЕ СРАБОТАЛО')


def legacy_notice(store, cid, *, live=False):
    def mutate(state):
        state['location'] = 'Дозор на тракте'
        state['world'] = dict(settings=dict(mode='sandbox'), triggered=[], chronicle=[], schedule={},
                              events=[dict(NOTICE)])
        if live:
            state['world']['event_format'] = 'world-events-v1'
    return write_state(store, cid, mutate)


def test_legacy_events_are_history_and_do_not_change_the_world(native):
    """Критик A02: экран говорил «исторические», а world.tick их исполнял."""
    store, cid = native
    current = legacy_notice(store, cid)
    assert world_events(current['state'])['historical'] is True
    engine = Nat20GameEngine(store)
    engine.execute(cid, GameCommand(request_id='after-legacy', expected_version=current['version'],
                                    action=dict(kind='check', check_id='athletics')))
    state = store.campaign(cid)['state']
    assert state['world']['chronicle'] == [], 'старое событие записалось в хронику'
    assert state['world']['triggered'] == [], 'старое событие отмечено сработавшим'


def test_prepared_scenario_events_still_fire(native):
    """Обратная сторона: событие текущего формата (подготовленная книга) работает."""
    store, cid = native
    current = legacy_notice(store, cid, live=True)
    events = world_events(current['state'])
    assert events['historical'] is False and events['format'] == 'world-events-v1'
    Nat20GameEngine(store).execute(cid, GameCommand(request_id='after-live', expected_version=current['version'],
                                                   action=dict(kind='check', check_id='athletics')))
    state = store.campaign(cid)['state']
    assert state['world']['triggered'] == ['old-notice']


def test_malformed_legacy_event_blocks_in_russian_instead_of_keyerror(native):
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(
        location='Дозор на тракте',
        world=dict(events=[dict(kind='notice', text='без id и времени')])))
    events = world_events(current['state'])
    assert events['blocking'] is True and events['broken'] == 1
    blocked = blocking_world_events(current['state'])
    assert blocked['code'] == 'world_events_unreadable'
    command = GameCommand(request_id='bad-event', expected_version=current['version'],
                          action=dict(kind='check', check_id='athletics'))
    with pytest.raises(ValueError, match='без описания'):
        Nat20GameEngine._preflight(current, command)


def test_schedule_only_queue_is_not_called_compatible(native):
    """Критик A02: world.events={} при непустом schedule — живая очередь."""
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(
        world=dict(events={}, schedule={'late-omen': 30})))
    events = world_events(current['state'])
    assert events['format'] == 'world-scheduler-v0' and events['blocking'] is True
    report = inspect(current)
    assert report['status'] == 'needs_choice' and report['migratable'] is True
    result = migrate(store, cid)
    assert result['migrated'] is True
    state = store.campaign(cid)['state']
    assert state['world']['events'] == {} and state['world']['schedule'] == {}
    assert state['world']['legacy_scheduler']['events'] == {'late-omen': 'scheduled'}
    assert inspect(store.campaign(cid))['status'] == 'ok'


def test_dict_queue_with_existing_archive_is_read_by_type(native):
    """Смешанное состояние: очередь словарём + уже созданный архив."""
    def mutate(state):
        state['world'] = dict(events={'late': 'pending'},
                              legacy_events=dict(events=[dict(id='old', text='старое', kind='notice')]))
    store, cid = native
    current = write_state(store, cid, mutate)
    events = world_events(current['state'])
    assert events['blocking'] is True and events.get('pending') == ['late']
    assert events['format'] == 'world-scheduler-v0'


def test_repeated_migration_keeps_the_previous_archive(native):
    """Критик A02: вторая миграция затирала первый архив."""
    store, cid = native
    make_outdated(store, cid)
    make_legacy_events(store, cid)
    migrate(store, cid)
    first = store.campaign(cid)['state']['world']['legacy_events']['events']
    assert [item['id'] for item in first] == ['foe-printed-kobold', 'foe-printed-guard']
    def again(state):
        state['world']['events'] = [dict(id='new-event', after_minutes=0, kind='notice',
                                         location='Дозор на тракте', text='новое')]
        state['nat20']['definition']['title'] = 'Дозор на тракте · снова старая запись'
    write_state(store, cid, again)
    migrate(store, cid)
    archive = store.campaign(cid)['state']['world']['legacy_events']['events']
    assert [item['id'] for item in archive] == ['foe-printed-kobold', 'foe-printed-guard', 'new-event']


# ── сохранение странной формы: отчёт вместо 500 ─────────────────────────────

def test_broken_definition_gives_a_russian_report_and_export_still_works(native):
    store, cid = native
    write_state(store, cid, lambda state: state['nat20'].update(definition='сломано'))
    report = inspect(store.campaign(cid))
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    assert 'не объектом' in report['summary']
    assert not any('a' <= letter.lower() <= 'z' for letter in report['summary'].replace('str', ''))
    bundle = export(store, cid)
    assert bundle['format'] == 'solo-campaign-export-v1'
    assert bundle['counts']['events'] >= 0
    with TestClient(create_app(store.path, CommandMockLLM({}))) as client:
        assert client.get(f'/api/campaigns/{cid}/compat').status_code == 200
        exported = client.get(f'/api/campaigns/{cid}/compat/export')
        assert exported.status_code == 200


def test_structurally_invalid_definition_is_named_in_russian_and_migrates(native):
    store, cid = native
    def mutate(state):
        state['nat20']['definition']['checks'] = {}
    write_state(store, cid, mutate)
    report = inspect(store.campaign(cid))
    registration = next(check for check in report['checks'] if check['id'] == 'registration')
    assert registration['code'] == 'invalid_form' and report['migratable'] is True
    assert 'проверку формы' in registration['detail'] and 'validation error' not in registration['detail']
    packet = Nat20Screen().project(store.campaign(cid))
    assert packet['terminal']['code'] == 'compatibility_required'
    assert 'validation error' not in packet['terminal']['message']
    assert migrate(store, cid)['migrated'] is True
    assert inspect(store.campaign(cid))['status'] == 'ok'


def test_state_that_the_engine_refuses_is_caught_before_input(native):
    """Критик A02: inspect говорил ok, а _bound падал 409-м сырым pydantic."""
    store, cid = native
    write_state(store, cid, lambda state: state['nat20'].pop('exhaustion'))
    report = inspect(store.campaign(cid))
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    assert 'проверку формы' in report['summary']
    packet = Nat20Screen().project(store.campaign(cid))
    assert packet['commands'] == [] and packet['terminal']['code'] == 'compatibility_required'


def test_unverified_release_does_not_count_as_compatible(native, monkeypatch):
    store, cid = native
    from solo import nat20_content
    def broken(digest):
        raise ValueError('Content differs from pinned SHA: dnd5e_engine/orchestrator.py')
    monkeypatch.setattr(nat20_content, 'release', broken)
    from solo.nat20_compat import _REVISIONS
    _REVISIONS.clear()
    report = inspect(store.campaign(cid))
    revision = next(check for check in report['checks'] if check['id'] == 'revision')
    assert revision['ok'] is False and revision['code'] == 'tree_broken'
    assert 'непроверенных правилах' in revision['detail']
    _REVISIONS.clear()


def test_integrity_summary_follows_the_real_result(native, monkeypatch):
    store, cid = native
    from solo import user_data
    real = user_data.sqlite_health
    def unhealthy(path):
        return dict(ok=False, integrity='database disk image is malformed', foreign_keys=[])
    monkeypatch.setattr(user_data, 'sqlite_health', unhealthy)
    report = integrity(store, cid)
    assert report['ok'] is False
    assert 'не подтверждена' in report['summary']
    assert 'в порядке' not in report['summary']
    monkeypatch.setattr(user_data, 'sqlite_health', real)


# ── импорт мира не взводит старые события ────────────────────────────────────

def test_importing_a_book_does_not_revive_legacy_events(native):
    """Критик-2 A02: import_package ставил метку v1 и оживлял старые события."""
    from solo.world import ScenarioPackage, import_package
    store, cid = native
    current = legacy_notice(store, cid)
    state = json.loads(json.dumps(current['state']))
    import_package(state, ScenarioPackage(name='Добавка', source='test', events=[]))
    assert state['world'].get('event_format') is None, 'пустая книга не имеет права менять формат событий'
    assert [event['id'] for event in state['world']['events']] == ['old-notice']
    Nat20GameEngine(store).execute(cid, GameCommand(request_id='after-import', expected_version=current['version'],
                                                   action=dict(kind='check', check_id='athletics')))
    after = store.campaign(cid)['state']
    assert after['world']['triggered'] == [], 'импорт книги взвёл старое событие'


def test_importing_a_book_archives_legacy_events_and_keeps_its_own_live(native):
    from solo.world import ScenarioPackage, WorldEvent, import_package
    store, cid = native
    current = legacy_notice(store, cid)
    state = json.loads(json.dumps(current['state']))
    package = ScenarioPackage(name='Добавка', source='test', events=[
        WorldEvent(id='book-event', after_minutes=0, kind='notice', location='Дозор на тракте', text='Строка книги')])
    import_package(state, package)
    assert state['world']['event_format'] == 'world-events-v1'
    assert [event['id'] for event in state['world']['events']] == ['book-event']
    assert [event['id'] for event in state['world']['legacy_events']['events']] == ['old-notice']


def test_importing_a_book_into_an_old_queue_refuses_in_russian(native):
    from solo.world import ScenarioPackage, WorldEvent, import_package
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(
        world=dict(events={'late': 'pending'}, schedule={'late': 30})))
    state = json.loads(json.dumps(current['state']))
    package = ScenarioPackage(name='Добавка', source='test', events=[
        WorldEvent(id='book-event', after_minutes=0, kind='notice', text='Строка книги')])
    with pytest.raises(ValueError, match='очередь мировых событий прежнего обработчика'):
        import_package(state, package)


# ── остальное из второго разбора ────────────────────────────────────────────

def test_list_events_with_schedule_are_both_archived(native):
    """Критик-2 A02: список событий + непустое расписание давали вечный замок."""
    store, cid = native
    def mutate(state):
        state['location'] = 'Дозор на тракте'
        state['world'] = dict(events=[dict(NOTICE)], schedule={'late': 30}, triggered=[])
    current = write_state(store, cid, mutate)
    assert blocking_world_events(current['state'])['code'] == 'world_events_pending'
    result = migrate(store, cid)
    assert result['migrated'] is True
    state = store.campaign(cid)['state']
    assert state['world']['events'] == [] and state['world']['schedule'] == {}
    assert state['world']['legacy_scheduler']['events'] == {'late': 'scheduled'}
    assert [item['id'] for item in state['world']['legacy_events']['events']] == ['old-notice']
    assert inspect(store.campaign(cid))['status'] == 'ok'


def test_definition_without_id_is_not_offered_for_migration(native):
    """Критик-2 A02: кнопка «обновить» на определении без id отдавала 404 «id»."""
    store, cid = native
    def mutate(state):
        state['nat20']['definition'].pop('id')
        state['nat20']['definition']['title'] = 'Дозор на тракте · без id'
    write_state(store, cid, mutate)
    report = inspect(store.campaign(cid))
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    with pytest.raises(ValueError, match='нет идентификатора|нельзя'):
        migrate(store, cid)


def test_event_of_unknown_kind_is_shown_but_does_not_lock_the_campaign(native):
    """Критик-2 A02: kind='weather' запирал кампанию навсегда."""
    store, cid = native
    current = write_state(store, cid, lambda state: state.update(
        location='Дозор на тракте',
        world=dict(events=[dict(id='w1', after_minutes=0, kind='weather', text='Дождь')])))
    events = world_events(current['state'])
    assert events['blocking'] is False and events['broken'] == 0
    assert events['items'][0]['id'] == 'w1' and events['items'][0]['executable'] is False
    command = GameCommand(request_id='weather-turn', expected_version=current['version'],
                          action=dict(kind='check', check_id='athletics'))
    Nat20GameEngine._preflight(current, command)  # не падает


def test_campaign_without_nat20_offers_export_only(native):
    """Критик-2 A02: сервер обещал кнопки, которые гейт одного бэкенда отбивает."""
    store, _ = native
    from solo.contracts import Hero
    other = store.create('Соло прежней версии', Hero(name='Старый'))['id']
    report = inspect(store.campaign(other))
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    assert [action['id'] for action in report['actions']] == ['export']
    assert report['events']['format'] == 'none'


def test_missing_scene_fields_are_caught_before_the_screen(native):
    """Критик-2 A02: inspect=ok, а project() падал KeyError 'location'."""
    store, cid = native
    write_state(store, cid, lambda state: state.pop('location'))
    report = inspect(store.campaign(cid))
    assert report['status'] == 'needs_choice' and report['migratable'] is False
    assert any(check['id'] == 'scene' and not check['ok'] for check in report['checks'])


# ── HTTP: экран, проверка и выгрузка ────────────────────────────────────────


def test_compat_routes_answer_behind_the_one_backend_gate(native):
    store, cid = native
    make_outdated(store, cid)
    with TestClient(create_app(store.path, CommandMockLLM({}))) as client:
        screen = client.get(f'/api/campaigns/{cid}/screen')
        assert screen.status_code == 200, screen.text
        snapshot = screen.json()['snapshot']
        assert snapshot['terminal']['code'] == 'compatibility_required'
        assert [action['id'] for action in snapshot['compatibility']['actions']] == ['migrate', 'verify', 'export']
        assert 'проверенной' in json.dumps(snapshot['compatibility'], ensure_ascii=False)

        report = client.get(f'/api/campaigns/{cid}/compat')
        assert report.status_code == 200 and report.json()['status'] == 'needs_choice'

        verified = client.post(f'/api/campaigns/{cid}/compat/verify')
        assert verified.status_code == 200, verified.text
        assert verified.json()['integrity'] == 'ok'

        exported = client.get(f'/api/campaigns/{cid}/compat/export')
        assert exported.status_code == 200
        assert 'attachment' in exported.headers['content-disposition']
        assert json.loads(exported.text)['format'] == 'solo-campaign-export-v1'

        updated = client.post(f'/api/campaigns/{cid}/compat/migrate')
        assert updated.status_code == 200, updated.text
        assert updated.json()['migrated'] is True

        reopened = client.get(f'/api/campaigns/{cid}/screen')
        assert reopened.status_code == 200
        assert reopened.json()['snapshot']['terminal'] is None
        assert reopened.json()['snapshot']['compatibility']['status'] == 'ok'


def test_legacy_events_reach_the_screen_as_history(native):
    store, cid = native
    make_legacy_events(store, cid)
    with TestClient(create_app(store.path, CommandMockLLM({}))) as client:
        response = client.get(f'/api/campaigns/{cid}/screen')
        assert response.status_code == 200, response.text
        compatibility = response.json()['snapshot']['compatibility']
        assert compatibility['status'] == 'ok'
        assert compatibility['events']['historical'] is True
        assert compatibility['events']['count'] == 2
        assert 'исторические' in compatibility['events']['reason']


def test_direct_engine_call_records_a_russian_label(native):
    """Журнал читает игрок: прямой вызов движка не оставляет «Nat20 check».

    Экран присылает своё имя действия («Проверка»), а прямой вызов из теста и
    интеграций раньше писал ``"Nat20 " + kind`` — в ленте появлялась английская
    подпись, хотя это не отказ движка. Приёмка A02 п.4-5: английских строк нет.
    """
    store, cid = native
    current = store.campaign(cid)
    Nat20GameEngine(store).execute(cid, GameCommand(request_id='a02-label', expected_version=current['version'],
                                                    action=dict(kind='check', check_id='athletics')))
    original = store.history(cid, limit=10)[-1]['original']
    assert original == 'Проверка', original
