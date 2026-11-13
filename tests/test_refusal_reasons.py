'''r195: a refusal names what is missing, not one polite sentence for everything.

A player typed a scene action in a campaign without an engine and read
'Мастер отказал: действие невозможно в текущем состоянии'. The real reason was
in `solo/command_engine.py` ('Это действие подключено только для проверенных
кампаний Nat20.'), but the sentence contains the engine's own name, and the
guard replaced every refusal that still had Latin letters in it with that one
phrase. Fourteen reasons reached the table as the same words.

The rule kept here: a known refusal says what is missing and what to do; an
unknown one keeps its Russian text as written; only a technical remainder falls
back to the polite sentence, and that fallback carries the refusal code so the
log line can be found from what the reader saw. The original text always lands
in the log (r183).
'''
import logging
import pathlib
import re
import socket
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solo.app import create_app  # noqa: E402
from solo.contracts import Hero  # noqa: E402
from solo.game import refusal_code, refusal_text  # noqa: E402
from solo.llm import CommandMockLLM  # noqa: E402
from solo.storage import Store  # noqa: E402

# Raw reasons exactly as they are raised today: three from the host
# (`solo/command_engine.py`, `solo/game.py`) and six engine typed rejections
# (`IntentRejectedError(reason, detail)`), plus the corpus' blocked marker.
KNOWN_REFUSALS = [
    ('Это действие подключено только для проверенных кампаний Nat20.',
     'no_engine', 'нет движка'),
    ("no_action_economy: actor_id='hero' has no Action remaining for Dash",
     'action_spent', 'израсходовано'),
    ("target_invalid: actor_id='hero' is not behind Three-Quarters/Total cover, "
     "out of any enemy's line of sight",
     'no_cover', 'укрытие'),
    ("out_of_range: actor_id='hero' grapple target 'goblin' is not a living "
     "combatant within reach (5 ft)",
     'reach_five_feet', 'пяти футов'),
    ('not_actor_turn: current=0', 'wrong_actor', 'не ход героя'),
    ('combat_ended: handle=r1', 'combat_ended', 'закончился'),
    ('insufficient_movement: 3 ft needed', 'insufficient_movement', 'скорости'),
    ('narrative_or_unsupported_in_combat: no typed combat handler payload for shield',
     'spell_not_in_combat', 'боевого обработчика'),
    ('blocked: pinned Hide does not spend its required Action',
     'hide_blocked', 'не тратит действие'),
]

# 'Nat20' is the engine's own name: the one Latin word the table is allowed to
# read. Any other Latin letter means engine prose reached the player.
LATIN = re.compile(r'[a-z]')


def readable(text):
    return not LATIN.search(text.replace('Nat20', '').casefold())


@pytest.mark.parametrize('raw,code,needle', KNOWN_REFUSALS)
def test_known_refusal_says_what_is_missing(raw, code, needle, caplog):
    with caplog.at_level(logging.INFO, logger='solo.game'):
        text = refusal_text(ValueError(raw))
    assert readable(text), text
    assert needle in text, text
    # The player reads a reason, not the code: the code belongs to the log.
    assert code not in text
    matched = [record for record in caplog.records
               if code in record.getMessage() and raw in record.getMessage()]
    assert matched, caplog.text
    # A launcher without a logging config keeps only WARNING and above, so an
    # info-level line would leave the technical reason out of the journal.
    assert all(record.levelno >= logging.WARNING for record in matched), matched


def test_every_known_refusal_reads_differently():
    texts = [refusal_text(ValueError(raw)) for raw, _, _ in KNOWN_REFUSALS]
    assert len(set(texts)) == len(texts), texts


def test_unknown_technical_reason_carries_a_code_and_keeps_the_log(caplog):
    raw = 'some engine condition failed'
    with caplog.at_level(logging.INFO, logger='solo.game'):
        text = refusal_text(ValueError(raw))
    assert re.search(r'Мастер отказал \(refused-[0-9a-f]{8}\)', text), text
    assert 'engine' not in text
    # Same reason, same code: the log line is findable from the table's words.
    assert refusal_code(raw) == refusal_code(raw)
    assert any(raw in record.getMessage() and refusal_code(raw) in record.getMessage()
               for record in caplog.records), caplog.text


def test_engine_typed_reason_is_used_as_the_code():
    raw = 'unknown_combatant: entity=ghost is not in the encounter'
    assert refusal_code(raw) == 'unknown_combatant'
    # Many divergences share the corpus' 'blocked:' prefix, so the digest names
    # the sentence instead of collapsing them into one code.
    assert refusal_code('blocked: pinned A') != refusal_code('blocked: pinned B')
    assert refusal_code('blocked: pinned A').startswith('refused-')


def test_russian_refusal_is_kept_as_written():
    assert refusal_text(ValueError('Герой уже мёртв.')) == 'Герой уже мёртв.'


@pytest.mark.parametrize('raw', [
    'Сцена изменилась. Разберите action=attack заново.',
])
def test_mixed_latin_is_stripped_and_the_sentence_survives(raw):
    text = refusal_text(ValueError(raw))
    assert 'action=' not in text
    assert readable(text), text
    # The reason was Russian: the payload goes, the sentence stays.
    assert text == 'Сцена изменилась. Разберите заново.'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No paid or external call may happen in this file."""
    connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)  # Windows asyncio wakeup socket.
        raise AssertionError('Paid/external APIs are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local_only)


TEXT = 'Прыгаю через яму'


def test_legacy_campaign_scene_action_names_the_missing_engine(tmp_path, caplog):
    """The reported case, at the route the player actually calls, offline.

    A campaign created by the old screen has no `nat20` key. Its scene action is
    refused by the host, and the player must read why -- not the polite phrase
    that used to swallow every refusal.
    """
    store = Store(tmp_path / 'refusal-reasons.sqlite3')
    cid = store.create('Старая история', Hero(name='Мирон'))['id']
    model = CommandMockLLM({TEXT: dict(status='command', action=dict(kind='dash'), message='')})
    app = create_app(store.path, model)
    version = store.campaign(cid)['version']
    with caplog.at_level(logging.INFO, logger='solo.game'):
        with TestClient(app) as client:
            response = client.post(f'/api/campaigns/{cid}/command-turn',
                                   json=dict(request_id='r195-legacy', text=TEXT,
                                             expected_version=version))
    assert response.status_code == 200, response.text
    reply = response.json()
    assert reply['status'] == 'rejected', reply
    assert 'нет движка' in reply['narrative'], reply['narrative']
    assert 'действие невозможно в текущем состоянии' not in reply['narrative']
    # The technical reason stayed in the log, with its code.
    raw = 'только для проверенных кампаний Nat20'
    assert any(raw in record.getMessage() and 'no_engine' in record.getMessage()
               for record in caplog.records), caplog.text
