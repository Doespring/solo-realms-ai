"""r188: experience and advancement are counted, not retold.

Three sources pay the same counter Nat20 owns - a won fight, a reward the book
prints, and a decision the scenario's own check settles. The thresholds are the
corpus table; crossing one offers a level, and the sheet that comes back is the
engine's derivation, never a hand-patched copy. A level that still owes the
player a choice asks for it instead of choosing, and a reward already booked
pays nothing a second time.
"""
import copy
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game import refusal_text
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_contracts import DurableState
from solo.storage import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal, loader as corpus_loader  # noqa: E402

SIGHT = 'Осматриваю калитку'
LEVEL = 'повышаю уровень'
FIGHT = dict(start=dict(kind='start_combat', encounter_id='basic'),
             move=dict(kind='move', position=dict(x=2, y=1, z=0)),
             attack=dict(kind='attack', target='road-raider'),
             turn=dict(kind='end_turn'), end=dict(kind='end_combat'),
             level=dict(kind='level_up'))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)  # Windows asyncio wakeup socket.
        raise AssertionError('Paid/external APIs are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local_only)


def app_for(tmp_path, name='r188.sqlite3', experimental=False, intents=None):
    script = {SIGHT: dict(status='command', action=dict(kind='check', check_id='perception'), message='')}
    for text, action in (intents or {}).items():
        script[text] = dict(status='command', action=action, message='')
    return create_app(tmp_path / name, CommandMockLLM(script),
                      experimental=experimental, offline_mock=experimental)


def experimental_campaign(app, *, progression=None, seed=3, name='Лира'):
    from solo.progression import Progression

    build = legal(class_slug='fighter', species_slug='human', background='soldier')
    return Nat20GameEngine(app.state.store).create_experimental_campaign(
        name, build=build, encounters={}, checks=None, prepared_spells=(), seed=seed,
        title='Reward xp', description='d', progression=progression or Progression())


def reviewed_module(app, tmp_path, rule, *, events=None, definition_id='opening', title='Reward xp'):
    """One reviewed module whose opening pays one printed reward."""
    from test_adventure_continuity import book, definition
    bundle, document = book(app.state.adventures, tmp_path, title)
    if events is None:
        events = [dict(category='timed', id='purse', after_minutes=0,
                       source=dict(document=document, page=3, review='confirmed'),
                       effects=[dict(kind='reward', target=rule['id'], text='Награда за находку')])]
    else:
        # The page a rule came from belongs to the whole event, not to a single
        # effect; the fixture's document is known only after the PDF exists.
        events = copy.deepcopy(events)
        for event in events:
            event.setdefault('source', {})['document'] = document
    app.state.adventures.save_definition(
        bundle['id'], definition(definition_id, document, events=events, tables=[],
                                 progression=dict(mode='experience', rewards=[rule])), True)
    return bundle['id']


def attach(app, cid, bundle):
    with TestClient(app) as client:
        assert client.post('/api/adventure/review/session',
                           json=dict(confirm_spoilers=True)).status_code == 200
        attached = client.post('/api/adventure/review/campaigns/%s/attach' % cid,
                               json=dict(bundle=bundle, request_id='r188-attach', confirm=True))
    assert attached.status_code == 200, attached.text
    return attached


def turn(app, cid, request_id, text=SIGHT):
    version = app.state.store.campaign(cid)['version']
    with TestClient(app) as client:
        return client.post('/api/campaigns/%s/command-turn' % cid,
                           json=dict(request_id=request_id, text=text, expected_version=version))


def notices(app, cid, request_id):
    store = app.state.store
    action = store.request(cid, request_id)
    results = store.event_for_action(action['id'])['results']
    return [item['text'] for item in results[-1].get('adventure_events') or []]


# --------------------------------------------------------------------------- #
# 1. The table is the corpus', not a second copy kept by the host.
# --------------------------------------------------------------------------- #
def test_the_thresholds_are_the_corpus_table():
    from dnd5e_engine.rules.advancement import XP_THRESHOLDS, level_for_experience

    assert len(XP_THRESHOLDS) == 20, XP_THRESHOLDS
    assert XP_THRESHOLDS[:4] == (0, 300, 900, 2700)
    assert [level_for_experience(xp) for xp in (0, 299, 300, 899, 900, 2699, 2700)] == [1, 1, 2, 2, 3, 3, 4]
    assert level_for_experience(355000) == 20


# --------------------------------------------------------------------------- #
# 2. A won fight pays the counter, and the journal says so.
# --------------------------------------------------------------------------- #
def test_a_won_fight_pays_the_counter_and_the_journal(tmp_path):
    store = Store(tmp_path / 'fight.sqlite3')
    api = Nat20GameEngine(store)
    cid = api.create_campaign('Обычный боец', definition_id='fighter-standard-array-2024-v1', seed=5)['id']
    app = create_app(store.path, CommandMockLLM({text: dict(status='command', action=action, message='')
                                                 for text, action in FIGHT.items()}))
    with TestClient(app) as client:
        def resolved(identity, text):
            result = client.post('/api/campaigns/%s/command-turn' % cid, json=dict(
                request_id=identity, text=text, expected_version=store.campaign(cid)['version']))
            assert result.status_code == 200, result.text
            assert result.json()['status'] == 'resolved', result.json()
            return result.json()

        resolved('fight-start', 'start')
        resolved('fight-move', 'move')
        for index in range(24):
            view = store.campaign(cid)['state']['nat20']['combat']['view']
            if view['tracked_hp']['road-raider'] <= 0:
                break
            assert view['tracked_hp']['hero'] > 0, 'Ordinary fighter did not survive'
            hero = view['initiative'][view['current_turn_index']]['entity_id'] == 'hero'
            resolved('fight-%d' % index, 'attack' if hero else 'turn')
            if hero:
                # An Action does not end the turn; without this the next attack
                # is refused as no_action_economy.
                resolved('fight-turn-%d' % index, 'turn')
        else:
            raise AssertionError('Bounded fight did not finish')
        award = resolved('fight-end', 'end')

    runtime = store.campaign(cid)['state']['nat20']
    receipt = runtime['rewards'][0]
    assert runtime['experience'] == receipt['experience'] == 50
    assert store.campaign(cid)['state']['hero']['experience'] == 50
    assert DurableState.model_validate(runtime).experience == 50
    assert any(effect['type'] == 'resource' and effect['payload'].get('resource') == 'experience'
               for effect in award['events'][0]['payload']['effects']), award['events']


# --------------------------------------------------------------------------- #
# 3. A scene decision pays its printed reward once, through the engine ledger.
# --------------------------------------------------------------------------- #
def scene_rules():
    """One reward paid by the scenario's own check, not by the opening."""
    rule = dict(id='hermit-xp', amount=250, page=3, reason='Опыт напечатан в книге')
    events = [
        # The player's own turn is the decision: its result opens the check the
        # module declares, and the check writes the flag the payout waits for.
        dict(category='conditional', id='rescue', after_minutes=0,
             action_kind='nat20_command',
             source=dict(document='', page=3, review='confirmed'),
             effects=[dict(kind='check', target='hermit-rescued', ability='constitution', dc=1,
                           text='Вытаскиваю отшельника из ямы')]),
        dict(category='conditional', id='thanks', after_minutes=0,
             prerequisites=['rescue'],
             conditions=[dict(path='adventure.flags.hermit-rescued', op='eq', value=True)],
             source=dict(document='', page=3, review='confirmed'),
             effects=[dict(kind='reward', target=rule['id'], text='Отшельник отдаёт карту')]),
    ]
    return rule, events


def test_a_scene_decision_pays_its_printed_reward_once(tmp_path):
    from solo.progression import Progression, RewardRule

    rule, events = scene_rules()
    app = app_for(tmp_path, 'r188-scene.sqlite3', experimental=True)
    progression = Progression(mode='experience', rewards=[RewardRule.model_validate(rule)])
    cid = experimental_campaign(app, progression=progression)['id']
    assert app.state.store.campaign(cid)['state']['nat20']['experience'] == 0

    module = reviewed_module(app, tmp_path, rule, events=events)
    attach(app, cid, module)

    assert turn(app, cid, 'scene-1').status_code == 200
    assert turn(app, cid, 'scene-2').status_code == 200
    state = app.state.store.campaign(cid)['state']
    assert state['adventure']['flags']['hermit-rescued'] is True, state['adventure']
    assert state['nat20']['experience'] == 250, state['nat20']['rewards']
    assert [row['id'] for row in state['nat20']['rewards']] == ['printed:opening:hermit-xp']
    words = ' '.join(notices(app, cid, 'scene-2'))
    assert 'Опыт Nat20: 250' in words, words

    # The same decision repeated pays nothing: the receipt is the ledger.
    assert turn(app, cid, 'scene-3').status_code == 200
    assert app.state.store.campaign(cid)['state']['nat20']['experience'] == 250


# --------------------------------------------------------------------------- #
# 4. Crossing a threshold offers the level; the engine derives the new sheet.
# --------------------------------------------------------------------------- #
def campaign_with_printed_xp(tmp_path, amount=900):
    from solo.progression import Progression, RewardRule

    rule = dict(id='episode-xp', amount=amount, page=42, reason='Опыт эпизода напечатан в книге')
    app = app_for(tmp_path, 'r188-level.sqlite3', experimental=True)
    progression = Progression(mode='experience', rewards=[RewardRule.model_validate(rule)])
    cid = experimental_campaign(app, progression=progression)['id']
    attach(app, cid, reviewed_module(app, tmp_path, rule, title='Episode xp'))
    assert turn(app, cid, 'level-turn').status_code == 200
    return app, cid, rule


def test_earned_experience_offers_a_level_the_engine_builds(tmp_path):
    from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
    app, cid, _rule = campaign_with_printed_xp(tmp_path)
    store = app.state.store
    paid = store.campaign(cid)['state']['nat20']
    assert paid['experience'] == 900
    assert paid['definition']['build']['level'] == 1, 'the scenario must not raise the level itself'

    with TestClient(app) as api:
        # One confirmation, one level: the screen applies the transition rules
        # level by level instead of skipping the choices in between.
        offer = api.get('/api/campaigns/' + cid + '/level-up').json()
        assert offer['available'] is True and offer['earned'] == 3 and offer['to_level'] == 2, offer
        done = api.post('/api/campaigns/' + cid + '/level-up', json=dict(
            request_id='r188-level-2', expected_version=offer['version'], draft=offer['draft']))
        assert done.status_code == 200, done.text
        assert store.campaign(cid)['state']['nat20']['definition']['build']['level'] == 2

        # The next level is the subclass gate: the screen offers the choice
        # rather than filling it in.
        offer = api.get('/api/campaigns/' + cid + '/level-up').json()
        assert offer['available'] is True and offer['to_level'] == 3, offer
        steps = str(offer['contract']['steps'])
        assert 'subclass' in steps, steps
        chosen = api.post('/api/campaigns/' + cid + '/level-up', json=dict(
            request_id='r188-level-3', expected_version=offer['version'],
            draft=dict(offer['draft'], subclass_slug='phb2024.fighter.master_boia')))
        assert chosen.status_code == 200, chosen.text

    committed = store.campaign(cid)['state']
    build = committed['nat20']['definition']['build']
    assert build['level'] == 3 and build['subclass_slug'] == 'phb2024.fighter.master_boia'
    # The sheet is the engine's derivation of that build, not a patched copy.
    loader = corpus_loader()
    spec = CharacterBuildSpec.model_validate(build)
    expected = derive_sheet(spec, loader=loader)
    assert committed['nat20']['sheet']['hp_max'] == expected.hp_max
    assert committed['nat20']['sheet']['features'] == list(expected.features)
    # Advancement spends no experience; the ledger still paid it once.
    assert committed['nat20']['experience'] == 900
    action = store.request(cid, 'r188-level-3')
    result = store.event_for_action(action['id'])['results'][0]
    assert result['kind'] == 'nat20_level_up'
    assert (result['native_result']['from_level'], result['native_result']['to_level']) == (2, 3)


# --------------------------------------------------------------------------- #
# 5. A level that owes a choice asks for it and never picks for the player.
# --------------------------------------------------------------------------- #
def test_a_pending_subclass_is_asked_not_chosen(tmp_path):
    app, cid, _rule = campaign_with_printed_xp(tmp_path)
    store = app.state.store
    engine = Nat20GameEngine(store)
    # One confirmation raises one level; experience covering three levels still
    # walks through them one at a time, asking at the gate.
    first = engine.level_up(cid, request_id='r188-level-1', expected_version=store.campaign(cid)['version'],
                            class_slug='fighter')
    assert first['to_level'] == 2
    before = store.campaign(cid)

    with pytest.raises(ValueError) as refused:
        engine.level_up(cid, request_id='r188-owed', expected_version=before['version'],
                        class_slug='fighter')
    question = str(refused.value)
    assert 'подкласс' in question, question
    assert 'Мастер боя' in question, (question, 'the options the corpus offers must be named')
    assert 'advancement choices still owed' not in question
    # The table reads it through the same translation the chat uses.
    assert refusal_text(refused.value) == question

    unchanged = store.campaign(cid)['state']['nat20']
    assert unchanged['definition']['build']['level'] == 2
    assert unchanged['definition']['build']['subclass_slug'] is None
    assert store.campaign(cid)['version'] == before['version']

    chosen = engine.level_up(cid, request_id='r188-chosen', expected_version=before['version'],
                             class_slug='fighter', subclass_slug='phb2024.fighter.master_boia')
    assert chosen['to_level'] == 3
    assert store.campaign(cid)['state']['nat20']['definition']['build']['subclass_slug'] == \
        'phb2024.fighter.master_boia'


def test_the_chat_word_for_advancement_asks_the_same_question(tmp_path):
    """The player who types it gets the question, not the log line."""
    app, cid, _rule = campaign_with_printed_xp(tmp_path)
    store = app.state.store
    engine = Nat20GameEngine(store)
    engine.level_up(cid, request_id='r188-chat-level-2', expected_version=store.campaign(cid)['version'],
                    class_slug='fighter')
    game_app = create_app(store.path, CommandMockLLM({
        LEVEL: dict(status='command', action=dict(kind='level_up'), message='')},
    ), experimental=True, offline_mock=True)
    before = store.campaign(cid)['version']
    with TestClient(game_app) as client:
        reply = client.post('/api/campaigns/%s/command-turn' % cid, json=dict(
            request_id='r188-chat-level', text=LEVEL, expected_version=before))
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body['status'] == 'rejected', body
    assert 'подкласс' in body['narrative'], body['narrative']
    assert 'owed' not in body['narrative']
    assert store.campaign(cid)['state']['nat20']['definition']['build']['level'] == 2
