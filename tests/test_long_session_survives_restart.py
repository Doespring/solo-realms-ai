"""r189 (r210: переписано на Nat20): a long evening is written into the file turn by
turn and survives a restart.

The live run behind these tests (``scripts/probe_r189_long_session.py``, 120 turns,
own profile, own port, ``SIGKILL`` between the two halves) showed 120 turns written
as 120 events, latency 203 ms on the first turn and 209 ms on the hundred-and-
twentieth, and a byte-identical state after the restart. Those numbers are what
the prototype owes the owner, so the same claims are pinned here without a socket
server and without a PDF:

* **every turn is in the file before the next one starts** — version and event
  count are read through a separate sqlite connection, not through the object
  that wrote them, so "saved at the end of the scene" cannot pass;
* **a fresh application over the same file gives the state back field by field**
  and the next turn continues the count (version, ``turn``, journal);
* **an unanswered question does not stop the hero from walking** — this is the
  defect the long run found: one «говорю с Ириной» where nobody stands asked a
  question, and afterwards every «иду в ...» was fed to the interpreter as an
  answer to it. 83 of 120 turns never reached the world layer;
* **what grows is measured**: the per-turn snapshots in ``events`` and memory
  rows for spoken turns, so the threshold for a future compaction card is a
  number, not a feeling.

r210: кампания рождается только через Nat20 (ручной лист и ``POST /api/campaigns``
удалены), поэтому вечер идёт по маленькому миру, установленному доверенным
сценарным хуком. Ходы по-прежнему идут единственным интерпретатором
``command-turn``; дорогу разбирает хост, а не модель.
"""
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.nat20_api import Nat20GameEngine
from solo.preview_mock import PreviewMock
from solo.storage import Store

ROUTE = [('к', 'башне', 'Старая башня'),
         ('на', 'берег реки', 'Берег реки'),
         ('в', 'таверну', 'Таверна «Последний огонь»')]

WORLD = [dict(name='Старая башня', aliases=['к башне'], known_to_hero=True, description='Башня.',
              interactions=[], npc=[]),
         dict(name='Берег реки', aliases=['на берег реки'], known_to_hero=True, description='Берег.',
              interactions=[], npc=[]),
         dict(name='Таверна «Последний огонь»', aliases=['в таверну'], known_to_hero=True,
              description='Таверна.', interactions=[],
              npc=[dict(id='irina', name='Ирина', present=True)])]


def start(path):
    """An application object, as the server builds it. Calls are offline."""
    client = TestClient(create_app(path, PreviewMock()))
    client.__enter__()
    return client


def campaign(client, name='r189 долгая сессия'):
    """Nat20 is the only way a campaign is born (r210).

    The world is installed by the same trusted Python hook the printed scenarios
    use: three places, one of them with a present NPC, so «говорю с ...» is a real
    conversation where someone stands and a legitimate question where nobody does.
    """
    store = client.app.state.store
    cid = Nat20GameEngine(store).create_campaign(name, seed=5)['id']

    def install(state):
        state['world'] = dict(mode='story', map_travel=False)
        state['locations'] = [dict(row) for row in WORLD]
        state['location'] = 'Старая башня'
        return [{'type': 'scripted', 'text': 'Мир пробы установлен.'}]

    store.record_scenario(cid, 'r189-world', 'Мир пробы', install)
    return dict(id=cid, version=store.campaign(cid)['version'])


def turn(client, cid, request_id, text, version):
    answer = client.post('/api/campaigns/%s/command-turn' % cid,
                         json={'request_id': request_id, 'text': text, 'expected_version': version})
    assert answer.status_code == 200, answer.text
    return answer.json()


def place(client, cid):
    state = client.get('/api/campaigns/' + cid).json()['state']
    here = next((row for row in state.get('locations') or [] if row.get('name') == state.get('location')), {})
    return here


def utterance(client, cid, number):
    """One player line of a live evening: road, look around, talk, road.

    A conversation is only held with someone who is actually there: talking to an
    absent person is a refusal of the mock, not a turn of the game, and would
    measure the mock instead of the campaign.
    """
    slot = (number - 1) % 4
    if slot == 1:
        return 'осматриваюсь', None
    if slot == 2:
        who = next((npc.get('name') for npc in place(client, cid).get('npc') or []
                    if npc.get('present', True)), None)
        return ('говорю с ' + who if who else 'осматриваюсь'), None
    index = ((number - 1) // 2) % len(ROUTE)
    preposition, said, expected = ROUTE[index]
    return 'иду %s %s' % (preposition, said), expected


def file_view(path, cid):
    """Version and event count as a foreign reader sees them."""
    with sqlite3.connect(str(path)) as db:
        version = db.execute('SELECT version FROM campaigns WHERE id=?', (cid,)).fetchone()[0]
        events = db.execute('SELECT count(*) FROM events WHERE campaign=?', (cid,)).fetchone()[0]
    return version, events


def test_every_turn_is_in_the_file_before_the_next_one_starts(tmp_path):
    path = tmp_path / 'turn-by-turn.sqlite3'
    client = start(path)
    try:
        created = campaign(client)
        cid, version = created['id'], created['version']
        # Установка мира — отдельное событие журнала, поэтому счёт идёт от базы,
        # а не от нуля: иначе тест мерил бы только себя.
        baseline = file_view(path, cid)[1]
        assert baseline == 1, baseline
        for number in range(1, 11):
            text, _ = utterance(client, cid, number)
            packet = turn(client, cid, 'turn-%02d' % number, text, version)
            version = packet['state_version']
            # Отдельное соединение: объект, писавший ход, здесь ни при чём.
            disk_version, disk_events = file_view(path, cid)
            assert disk_version == version, 'ход %d не в базе: %r против %r' % (number, disk_version, version)
            assert disk_events == baseline + number, 'событий на диске %d после хода %d' % (disk_events, number)
    finally:
        client.__exit__(None, None, None)


def test_fifty_turns_come_back_field_by_field_on_a_fresh_application(tmp_path):
    path = tmp_path / 'restart.sqlite3'
    client = start(path)
    try:
        created = campaign(client)
        cid, version = created['id'], created['version']
        start_version = version
        start_turn = client.get('/api/campaigns/' + cid).json()['state']['turn']
        last_text = ''
        for number in range(1, 51):
            text, _ = utterance(client, cid, number)
            last_text = text
            version = turn(client, cid, 'long-%03d' % number, text, version)['state_version']
        before = client.get('/api/campaigns/' + cid).json()
        baseline = file_view(path, cid)[1] - 50
    finally:
        client.__exit__(None, None, None)

    assert before['version'] == start_version + 50
    assert before['state']['turn'] == start_turn + 50
    assert len(before['history']) == 50
    # Ходы осмотра и разговора пишут журнал со своим внутренним request_id, но
    # реплика игрока в записи — его собственные слова, и последняя запись о них.
    assert before['history'][-1]['original'] == last_text

    # Ни одного объекта первого приложения: всё, что вернётся, прочитано из файла.
    fresh = start(path)
    try:
        after = fresh.get('/api/campaigns/' + cid).json()
        assert after['version'] == before['version']
        assert json.dumps(after['state'], sort_keys=True) == json.dumps(before['state'], sort_keys=True), \
            'состояние после перезапуска разошлось по полям'
        assert after['history'] == before['history']
        assert after['conversation'] == before['conversation']
        assert after['saves'] == before['saves']

        text, expected = utterance(fresh, cid, 51)
        packet = turn(fresh, cid, 'long-051', text, after['version'])
        assert packet['state_version'] == after['version'] + 1
        final = fresh.get('/api/campaigns/' + cid).json()
        assert final['state']['turn'] == before['state']['turn'] + 1
        if expected:
            assert final['state']['location'] == expected
        assert file_view(path, cid) == (after['version'] + 1, baseline + 51)
    finally:
        fresh.__exit__(None, None, None)


def test_an_unanswered_question_does_not_stop_the_hero_walking(tmp_path):
    path = tmp_path / 'question.sqlite3'
    client = start(path)
    try:
        created = campaign(client)
        cid, version = created['id'], created['version']
        walked = turn(client, cid, 'to-river', 'иду на берег реки', version)
        assert walked['status'] == 'resolved', walked
        version = walked['state_version']
        assert place(client, cid)['npc'] == []

        # Собеседника здесь нет: это законный вопрос, и он открыт на этой версии.
        asked = turn(client, cid, 'talk-alone', 'говорю с Ириной', version)
        assert asked['status'] == 'clarification', asked
        assert asked['state_version'] == version
        question = Store(path).open_question(cid)
        assert question is not None

        # Дорогу хост разбирает сам: вопрос модели её больше не съедает.
        out = turn(client, cid, 'walk-out', 'иду к башне', version)
        assert out['status'] == 'resolved', out
        assert out['state_version'] == version + 1
        assert Store(path).campaign(cid)['state']['location'] == 'Старая башня'
        assert Store(path).open_question(cid) is None, 'вопрос остался открытым после хода'

        # И дальше игра идёт обычным порядком, а не циклом отказов.
        text, expected = utterance(client, cid, 4)
        packet = turn(client, cid, 'after-walk', text, out['state_version'])
        assert packet['status'] in ('resolved', 'observation', 'conversation'), packet
        assert packet['state_version'] == out['state_version'] + 1
    finally:
        client.__exit__(None, None, None)


def test_memory_and_journal_grow_with_the_turns_that_write_them(tmp_path):
    path = tmp_path / 'growth.sqlite3'
    client = start(path)
    try:
        created = campaign(client)
        cid, version = created['id'], created['version']
        for number in range(1, 25):
            text, _ = utterance(client, cid, number)
            version = turn(client, cid, 'grow-%02d' % number, text, version)['state_version']
        with sqlite3.connect(str(path)) as db:
            events = db.execute('SELECT count(*) FROM events WHERE campaign=?', (cid,)).fetchone()[0]
            memories = db.execute('SELECT count(*) FROM memory_records WHERE campaign=?', (cid,)).fetchone()[0]
            fts = db.execute('SELECT count(*) FROM journal_fts WHERE campaign=?', (cid,)).fetchone()[0]
            snapshot_bytes = db.execute(
                'SELECT coalesce(sum(length(before_state)+length(after_state)),0) FROM events WHERE campaign=?',
                (cid,)).fetchone()[0]
            state_bytes = db.execute('SELECT length(state) FROM campaigns WHERE id=?', (cid,)).fetchone()[0]
        # 24 хода + установка мира: каждый ход пишет своё событие, ни одного пропуска.
        assert events == 25, events
        # Память реплик пишется разговором, а не каждым ходом. В этом мире
        # собеседник стоит только в таверне, поэтому разговоров меньше, чем
        # ходов; порог — замер прогона, а не догадка.
        print('r189 memory rows', memories, 'fts', fts, 'state', state_bytes, 'snapshots', snapshot_bytes)
        assert memories >= 1, memories
        assert fts >= memories, (fts, memories)
        # Состояние кампании остаётся маленьким; растут снимки ходов.
        assert state_bytes < 60_000, state_bytes
        assert snapshot_bytes > state_bytes * 5, (snapshot_bytes, state_bytes)
    finally:
        client.__exit__(None, None, None)
