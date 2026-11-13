"""r214 acceptance: the campaign keeps a party, companions have their own sheets.

The owner's decision (SPEC-COMPANIONS.md, 03.11.2026): a campaign stores the
membership - the main hero plus zero, one or several companions, each with its
own build, its own Nat20 sheet and its own resources. A party of one stays
legal, and a companion always stands at the hero's level.

Every number here is asserted from Nat20's own output: the sheet the worker
derived, the level the engine advanced to. Nothing is recomputed in the test.
"""
import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import suggested_build
from solo.storage import Store


def build(class_slug, level=1):
    """A legal character of this class, built by the same editor the hero uses."""
    return suggested_build(class_slug=class_slug, species_slug='human',
                           background_slug='soldier', level=level)


def campaign(tmp_path, name='Партия', encounters=None):
    store = Store(tmp_path / 'party.sqlite3')
    api = Nat20GameEngine(store)
    identity = api.create_experimental_campaign(name, build=build('fighter'), encounters=encounters or {},
                                                title=name)
    return store, api, identity['id']


def app_for(store):
    """The real screen's app: the party routes live with the prototype surface."""
    return create_app(store.path, CommandMockLLM({}), experimental=True)


def test_hero_plus_two_companions_have_their_own_sheets(tmp_path):
    store, api, cid = campaign(tmp_path)
    # A campaign is born with a party of one: the hero alone is the normal case.
    alone = api.party(store.campaign(cid))
    assert [member['role'] for member in alone] == ['hero']
    assert alone[0]['level'] == 1

    with TestClient(app_for(store)) as client:
        for name, class_slug in (('Мира', 'wizard'), ('Торн', 'rogue')):
            response = client.post(f'/api/campaigns/{cid}/party', json=dict(
                name=name, build=build(class_slug), prepared_spells=[],
                request_id='join-' + class_slug))
            assert response.status_code == 200, response.text
            assert response.json()['member'].startswith('companion-')
        state = client.get(f'/api/campaigns/{cid}').json()
    party = state['party']
    assert len(party) == 3, party
    assert [member['role'] for member in party] == ['hero', 'companion', 'companion']
    assert {member['classes'] and next(iter(member['classes'])) for member in party} == {
        'fighter', 'wizard', 'rogue'}
    # Each member carries its own derived maximum: a wizard does not borrow the
    # fighter's hit points, and each level equals the hero's.
    assert {member['level'] for member in party} == {1}
    assert len({member['hp_max'] for member in party}) == 3, party
    assert all(member['hp_current'] == member['hp_max'] for member in party)
    # The join is in the journal, not a silent row in the state.
    joins = [row for row in store.history(cid) if 'Спутник' in (row.get('original') or '')]
    assert len(joins) == 2, joins
    assert all('присоединился' in row['results'][0]['text'] for row in joins), joins


def test_hero_level_up_carries_the_companions(tmp_path):
    store, api, cid = campaign(tmp_path, 'Повышение')
    before = None
    with TestClient(app_for(store)) as client:
        client.post(f'/api/campaigns/{cid}/party', json=dict(
            name='Мира', build=build('wizard'), prepared_spells=[], request_id='join-wizard'))
        before = client.get(f'/api/campaigns/{cid}').json()['party']
        assert {member['level'] for member in before} == {1}
        # The wizard owes one expertise pick at level 2; the host does not
        # invent it, so the table answers for the companion (r214).
        picks = list(build('wizard')['selected_choices']) + ['expertise:arcana']
        api.level_up(cid, request_id='level-two', expected_version=store.campaign(cid)['version'],
                     class_slug='fighter', milestone=2,
                     party_choices={'companion-1': {'selected_choices': picks}})
        after = client.get(f'/api/campaigns/{cid}').json()['party']
    assert {member['level'] for member in after} == {2}, after
    hero_before, hero_after = before[0], after[0]
    companion_before, companion_after = before[1], after[1]
    assert hero_after['hp_max'] > hero_before['hp_max']
    assert companion_after['hp_max'] > companion_before['hp_max']
    # The companion keeps the hit points it had, and its hit dice grow with the level.
    assert companion_after['hp_current'] == companion_before['hp_current']
    assert companion_after['hit_dice']['dice_total'] == 2
    assert companion_after['hit_dice']['dice_remaining'] == 2


def test_a_companion_can_leave_and_solo_stays_intact(tmp_path):
    store, api, cid = campaign(tmp_path, 'Соло')
    with TestClient(app_for(store)) as client:
        joined = client.post(f'/api/campaigns/{cid}/party', json=dict(
            name='Мира', build=build('wizard'), prepared_spells=[], request_id='join')).json()
        member = joined['member']
        assert len(joined['party']) == 2
        left = client.delete(f'/api/campaigns/{cid}/party/{member}')
        assert left.status_code == 200, left.text
        assert [row['role'] for row in left.json()['party']] == ['hero']
        # The hero cannot be removed: a party always has its main hero.
        refused = client.delete(f'/api/campaigns/{cid}/party/hero')
        assert refused.status_code == 409, refused.text
    # No companion, no change of behaviour: the ordinary command still resolves.
    current = store.campaign(cid)
    assert current['state']['nat20']['hero']['hp_current'] == current['state']['hero']['hp']


def test_a_fight_opens_with_the_whole_party(tmp_path):
    """The engine takes a second hero-member; the host must not send one."""
    yard = dict(grid=dict(width=12, height=8), hero_position=dict(x=1, y=1, z=0),
                actors=[dict(id='foe0', monster='wolf', position=dict(x=6, y=1, z=0))])
    store, api, cid = campaign(tmp_path, 'Бой', encounters={'yard': yard})
    with TestClient(app_for(store)) as client:
        client.post(f'/api/campaigns/{cid}/party', json=dict(
            name='Мира', build=build('wizard'), prepared_spells=[], request_id='join'))
        engine = Nat20GameEngine(store)
        engine.execute(cid, __import__('solo.game_commands', fromlist=['GameCommand']).GameCommand(
            request_id='fight', expected_version=store.campaign(cid)['version'],
            action=dict(kind='start_combat', encounter_id='yard')))
        runtime = store.campaign(cid)['state']['nat20']
        party_ids = set(runtime['combat']['view']['party_ids'])
        assert party_ids == {'hero', 'companion-1'}, runtime['combat']['view']
        for identifier in party_ids:
            assert runtime['combat']['view']['tracked_hp'][identifier] > 0
        assert runtime['party'][1]['member']['hp_current'] == \
            runtime['combat']['view']['tracked_hp']['companion-1']
