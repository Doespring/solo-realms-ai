"""r217 acceptance: death by agreement, and the game a player sets for himself.

The owner's decision (03.11.2026, ``SPEC-COMPANIONS.md``, пп. 4-5): whether the
character can die, and how hard the game is, is the player's own rule — the
master announces what changed instead of turning numbers down in silence.

Every number asserted here comes from the engine's own output or from the host
policy table in ``solo/nat20_settings.py``; nothing is recomputed by hand. The
fight that ends in death is a real fight: an elephant against a level-one
fighter, with the campaign's seed fixed so the same rules give the same result.
"""
import json

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_contracts import DurableState
from solo.nat20_wizard import suggested_build
from solo.storage import Store

SEED = 3


def build(class_slug='fighter', level=1):
    return suggested_build(class_slug=class_slug, species_slug='human',
                           background_slug='soldier', level=level)


def campaign(tmp_path, name='Правила', encounters=None, checks=None, filename='rules.sqlite3'):
    store = Store(tmp_path / filename)
    api = Nat20GameEngine(store)
    identity = api.create_experimental_campaign(name, build=build(), encounters=encounters or {},
                                                checks=checks, title=name, seed=SEED)
    return store, api, identity['id']


def app_for(store):
    return create_app(store.path, CommandMockLLM({}), experimental=True)


def command_rows(store, campaign_id, kind):
    """Committed commands of one kind, oldest first.

    ``store.history`` is the player projection and drops the event record, so the
    journal rows are read from the events table the same way the screen does.
    """
    with store.connection() as db:
        rows = db.execute('SELECT results FROM events WHERE campaign=? ORDER BY seq',
                          (campaign_id,)).fetchall()
    found = []
    for row in rows:
        for result in json.loads(row['results']):
            action = (result.get('game_command') or {}).get('action') or {}
            if action.get('kind') == kind:
                found.append(result)
    return found


def outcome_of(store, campaign_id, kind):
    """The player-visible sentence of the last committed command of that kind."""
    rows = command_rows(store, campaign_id, kind)
    return rows[-1]['game_event']['payload']['outcome'] if rows else ''


def command(api, store, cid, request_id, kind, **fields):
    return api.execute(cid, GameCommand(request_id=request_id,
                                        expected_version=store.campaign(cid)['version'],
                                        action=dict(kind=kind, **fields)))


def gauntlet(size=4):
    return dict(grid=dict(width=12, height=8), hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id='foe%d' % index, monster='goblin-warrior',
                             position=dict(x=4 + index, y=1, z=0)) for index in range(size)])


def trap():
    """One elephant, one hero, one cell apart: a fight that really kills."""
    return dict(grid=dict(width=10, height=6), hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id='beast', monster='elephant', position=dict(x=2, y=1, z=0))])


def doomed_hero(tmp_path, death, *, declared=False, filename='doom.sqlite3'):
    """A real fight the hero loses; the rules decide what that loss means."""
    store = Store(tmp_path / filename)
    api = Nat20GameEngine(store)
    cid = api.create_experimental_campaign('Ловушка', build=build(), encounters={'trap': trap()},
                                           title='Ловушка', seed=SEED)['id']
    api.set_settings(cid, death=death, request_id='rules')
    if declared:
        from solo.contracts import Master
        store.master(cid, Master(declared_lethal_risk=True))
    command(api, store, cid, 'fight', 'start_combat', encounter_id='trap')
    last = {}
    for turn in range(10):
        if 'hero' in (store.campaign(cid)['state']['nat20']['combat']['view']['dead_ids'] or []):
            break
        last = command(api, store, cid, 'turn-%d' % turn, 'end_turn')
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    assert 'hero' in (view['dead_ids'] or []), view['tracked_hp']
    return store, api, cid, last


def test_three_rules_are_saved_in_the_campaign_and_in_one_journal_event(tmp_path):
    store, api, cid = campaign(tmp_path)
    with TestClient(app_for(store)) as client:
        before = client.get('/api/campaigns/%s/settings' % cid).json()
        assert (before['death'], before['difficulty'], before['rewards']) == \
            ('agreed', 'as_is', 'normal'), before
        saved = client.put('/api/campaigns/%s/settings' % cid, json=dict(
            death='off', difficulty='solo_softer', rewards='sparse', request_id='rules-1'))
        assert saved.status_code == 200, saved.text
        assert saved.json()['settings']['death'] == 'off'
        after = client.get('/api/campaigns/%s/settings' % cid).json()
        # The campaign itself carries the saved values, not the screen's copy.
        assert client.get('/api/campaigns/%s' % cid).json()['settings']['difficulty'] == 'solo_softer'
    assert after['effects'] == dict(monster_hp_percent=70, monster_attack_bonus=-1, check_dc=-2,
                                    fewer_foes=True, reward_percent=50), after
    state = store.campaign(cid)['state']['nat20']['settings']
    assert state == dict(death='off', difficulty='solo_softer', rewards='sparse'), state
    rows = [row for row in store.history(cid) if (row.get('original') or '') == 'Правила кампании']
    assert len(rows) == 1, rows
    text = rows[0]['results'][0]['text']
    assert 'смерть — выключена' in text and 'сложность — соло-мягче' in text, text
    assert 'награды — скупые' in text, text
    # Saving the same values again writes no second event.
    assert api.set_settings(cid, death='off')['changed'] is False


def test_solo_softer_fight_has_fewer_enemies_and_says_what_changed(tmp_path):
    store, api, cid = campaign(tmp_path, 'Мягче', encounters={'yard': gauntlet(4)})
    printed_store, printed_api, printed_id = campaign(
        tmp_path, 'Как есть', encounters={'yard': gauntlet(4)}, filename='printed.sqlite3')
    api.set_settings(cid, difficulty='solo_softer', request_id='softer')
    command(api, store, cid, 'fight', 'start_combat', encounter_id='yard')
    command(printed_api, printed_store, printed_id, 'fight', 'start_combat', encounter_id='yard')
    softer = store.campaign(cid)['state']['nat20']['combat']['initial']
    printed = printed_store.campaign(printed_id)['state']['nat20']['combat']['initial']
    assert len(printed['encounter']) == 4, printed['encounter']
    # A solo hero meets every second prepared enemy; the printed fight meets all.
    assert [foe['entity_id'] for foe in softer['encounter']] == ['foe0', 'foe2'], softer['encounter']
    printed_hp = printed['encounter'][0]['hp_max']
    assert softer['encounter'][0]['hp_max'] == max(1, (printed_hp * 70 + 99) // 100) < printed_hp
    line = outcome_of(store, cid, 'start_combat')
    assert 'врагов 2 вместо 4' in line, line
    assert 'хиты' in line, line
    assert 'врагов' not in outcome_of(printed_store, printed_id, 'start_combat')


def test_check_dc_follows_the_chosen_difficulty_and_is_named(tmp_path):
    check = dict(kind='skill', skill='perception', ability=None, dc=15,
                 source=dict(kind='authored_scenario', id='test', version='1', note='r217'))
    store, api, cid = campaign(tmp_path, 'Порог', checks={'watch': check})
    api.set_settings(cid, difficulty='solo_softer', request_id='softer')
    result = command(api, store, cid, 'look', 'check', check_id='watch')
    assert result['result']['dc'] == 13, result['result']
    assert result['result']['printed_dc'] == 15
    assert 'КС 15 → 13' in result['result']['settings_dc']
    line = outcome_of(store, cid, 'check')
    assert 'КС 15 → 13' in line, line


def test_death_disabled_makes_the_hero_out_and_the_campaign_goes_on(tmp_path):
    store, api, cid, last = doomed_hero(tmp_path, 'off')
    runtime = store.campaign(cid)['state']['nat20']
    out = runtime['out']
    assert out and out['reason'] == 'death_disabled', out
    assert out['who'] == 'hero' and out['hp'] == 0
    assert last['result']['death']['held'] is False
    # Not a death: the campaign keeps no death record and the state still opens.
    assert 'hero_death' not in runtime
    assert runtime['hero']['hp_current'] == 0
    assert DurableState.model_validate(runtime).out['reason'] == 'death_disabled'
    # The fight can be closed and the campaign continues without it.
    closed = command(api, store, cid, 'close', 'end_combat')
    assert store.campaign(cid)['state']['nat20']['combat'] is None
    assert closed['result']['outcome']['ended_reason'].startswith('defeat'), closed['result']


def test_death_on_with_a_declared_risk_lets_the_death_stand(tmp_path):
    store, api, cid, last = doomed_hero(tmp_path, 'on', declared=True)
    runtime = store.campaign(cid)['state']['nat20']
    # The death stands, and the campaign records it as a death (not as "out"):
    # a long rest cannot undo it.
    assert runtime['out']['reason'] == 'death', runtime.get('out')
    assert runtime['combat']['initial']['lethal_risk_declared'] is True
    assert runtime['combat']['initial']['death_policy'] == 'on'
    # The journal names the declared risk as the reason the outcome stood.
    assert 'Риск был объявлен' in last['result']['death']['text'], last['result']['death']


def test_death_on_without_a_declared_risk_stops_the_irreversible(tmp_path):
    store, api, cid, last = doomed_hero(tmp_path, 'on', filename='held.sqlite3')
    runtime = store.campaign(cid)['state']['nat20']
    out = runtime['out']
    assert out and out['reason'] == 'death_held', out
    assert out['declared_risk'] is False and out['policy'] == 'on'
    assert last['result']['death']['held'] is True
    assert 'риск не был объявлен' in last['result']['death']['text'], last['result']['death']
    line = outcome_of(store, cid, 'end_turn')
    assert 'риск не был объявлен' in line, line


def test_agreed_asks_the_master_question_even_after_a_declared_risk(tmp_path):
    store, api, cid, last = doomed_hero(tmp_path, 'agreed', declared=True,
                                        filename='agreed.sqlite3')
    out = store.campaign(cid)['state']['nat20']['out']
    assert out and out['reason'] == 'death_held', out
    assert 'Мастер спрашивает' in last['result']['death']['text'], last['result']['death']


def test_an_out_hero_comes_back_by_a_long_rest_not_by_a_heal(tmp_path):
    store, api, cid, _ = doomed_hero(tmp_path, 'off', filename='rest.sqlite3')
    command(api, store, cid, 'close', 'end_combat')
    # A check is not a way around the dying state: only the printed stabilizing
    # rule (a long rest of 8 hours > 1d4 hours) wakes the hero.
    refused = None
    try:
        command(api, store, cid, 'look', 'check', check_id='perception')
    except ValueError as error:
        refused = str(error)
    assert refused and 'выбыл' in refused, refused
    rested = command(api, store, cid, 'rest', 'long_rest', food_water=True)
    runtime = store.campaign(cid)['state']['nat20']
    assert runtime['out'] is None, runtime['out']
    assert runtime['hero']['hp_current'] > 1, runtime['hero']['hp_current']
    assert rested['result']['recovery']['hp'] == 1, rested['result']
    line = outcome_of(store, cid, 'long_rest')
    assert 'приходит в себя' in line, line

def test_the_settings_screen_has_the_three_controls(tmp_path):
    """The screen is a real control: three named fields and the same one route.

    This does not click a browser; it proves the panel is in the served page and
    that its script writes through the campaign route the tests above exercise.
    """
    store, api, cid = campaign(tmp_path, filename='screen.sqlite3')
    with TestClient(app_for(store)) as client:
        page = client.get('/').text
        script = client.get('/static/campaign-settings.js')
    assert script.status_code == 200, script.status_code
    for element in ('id="rules-death"', 'id="rules-difficulty"', 'id="rules-rewards"',
                    'id="rules-save"'):
        assert element in page, element
    assert 'campaign-settings.js' in page
    assert '/settings' in script.text and "'PUT'" in script.text, script.text[:400]
