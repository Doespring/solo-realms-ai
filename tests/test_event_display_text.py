"""r184: the screen renders the sentence the server hands it and parses nothing.

The defect this file guards: web/game-screen.js kept its own Russian dictionary
and rewrote any chat line matching ``Nat20 confirmed: <kind>``. A new command
kind therefore needed a synchronous edit of the server and the front, a player's
or the Master's line that began the same way was silently replaced, and the
wording could not change without rebuilding the browser code. The server now
answers with ``display_text`` (ready to show) and ``display_key`` (an
identifier, never parsed), and old journal rows are translated on the way out.
"""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.event_display import KINDS, UNKNOWN, display_text, translate_legacy
from solo.nat20_api import GameCommand, Nat20GameEngine
from solo.play_screen import screen
from solo.storage import Store
from test_nat20_command_cycle import model

ROOT = Path(__file__).resolve().parents[1]

# Exactly the dictionary the browser kept until r184 (web/game-screen.js:76).
BROWSER_KINDS = ('check', 'start_combat', 'move', 'attack',
                 'end_turn', 'end_combat', 'short_rest', 'long_rest')
CYRILLIC = re.compile('[А-Яа-я]')


def native(tmp_path):
    store = Store(tmp_path / 'r184.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('r184', seed=5)['id']
    api.execute(cid, GameCommand(request_id='r184-start', expected_version=0,
                                 action=dict(kind='start_combat', encounter_id='basic')))
    return store, cid


def test_every_kind_the_browser_knew_has_a_russian_sentence():
    missing = [kind for kind in BROWSER_KINDS if kind not in KINDS]
    assert not missing, missing
    for kind in BROWSER_KINDS:
        text = display_text(kind)
        assert CYRILLIC.search(text), (kind, text)
        assert 'Nat20' not in text, (kind, text)


def test_an_unknown_kind_still_reads_like_a_sentence():
    """A new engine kind must show Russian without editing the front."""
    text = display_text('teleport_brand_new_kind')
    assert text == UNKNOWN and CYRILLIC.search(text) and 'Nat20' not in text


def test_only_the_whole_legacy_line_is_translated():
    assert translate_legacy('Nat20 confirmed: check') == 'Проверка выполнена.'
    assert translate_legacy('  Nat20 confirmed: attack ') == 'Атака выполнена.'
    # A confirmed sentence with numbers reaches the player exactly as written.
    line = 'Атлетика: 13 против Сл 12 — успех'
    assert translate_legacy(line) == line
    # A player's or the Master's line that merely starts the same way is kept.
    assert translate_legacy('Nat20 confirmed: я иду к башне') == 'Nat20 confirmed: я иду к башне'


def test_the_browser_no_longer_parses_engine_lines():
    offenders = [path.name for path in sorted((ROOT / 'web').glob('*.js'))
                 if 'Nat20 confirmed' in path.read_text(encoding='utf-8')]
    assert offenders == [], offenders


def test_the_engine_journal_stores_the_sentence_not_the_engine_line(tmp_path):
    store, cid = native(tmp_path)
    # store.history() is the player projection; the engine's own row lives in the
    # events table, and that is what screen() reads game_event from.
    with store.connection() as db:
        raw = [json.loads(row['results']) for row in db.execute(
            'SELECT results FROM events WHERE campaign=? ORDER BY seq DESC LIMIT 1', (cid,))]
    payload = raw[0][0]['game_event']['payload']
    assert payload['outcome'] == display_text('start_combat')
    assert 'Nat20 confirmed' not in json.dumps(raw, ensure_ascii=False)


def test_the_screen_hands_out_display_text_and_key(tmp_path):
    store, cid = native(tmp_path)
    packet = screen(store, cid)
    event = packet['events'][-1]
    assert event['display_key'] == 'start_combat'
    assert event['display_text'] == display_text('start_combat')
    assert event['text'] == event['display_text']


def test_old_journal_and_chat_rows_are_translated_on_the_server(tmp_path):
    store, cid = native(tmp_path)
    # A row written before r184 holds the engine's own line, as the browser used
    # to see it. The reader translates it; nothing in the front knows that word.
    store.converse(cid, 'r184-legacy', 'осмотреть дорогу', 'Nat20 confirmed: check', 'notice')
    rows = store.conversation_history(cid)
    assert rows[-1]['reply'] == 'Проверка выполнена.'


def test_the_command_answer_carries_the_same_fields_as_the_screen(tmp_path):
    # Out of combat: the check command (and its numbers) only exists there.
    store = Store(tmp_path / 'r184-check.sqlite3')
    cid = Nat20GameEngine(store).create_campaign('r184-check', seed=5)['id']
    with TestClient(create_app(store.path, model())) as client:
        packet = client.get(f'/api/campaigns/{cid}/screen').json()
        command = next(c for c in packet['commands'] if c['parameters']['kind'] == 'check')
        answer = client.post(f'/api/campaigns/{cid}/commands', json=dict(
            request_id='r184-check', expected_version=command['expected_version'],
            action=command['parameters']))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        event = body['events'][-1]
        assert event['display_key'] == 'check'
        # The check path keeps its numbers: the sentence is the roll, not a label.
        assert event['display_text'].startswith('Атлетика: ')
        assert 'Nat20 confirmed' not in json.dumps(body, ensure_ascii=False)
        after = client.get(f'/api/campaigns/{cid}/screen').json()
        assert after['events'][-1]['display_text'] == event['display_text']
