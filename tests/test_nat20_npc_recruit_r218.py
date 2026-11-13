"""r218 acceptance: an NPC of the material becomes a companion by playing.

The owner's decision (SPEC-COMPANIONS.md, item 6, 03.11.2026): during play some
NPCs may become companions. The transformation is a journal event with its reason
- not a row that quietly changes - and afterwards the person fights like any
companion (r215), answers orders (r216) and can leave for his own reason (r218).

Every number asserted here is Nat20's own output: the sheet ``derive_sheet``
produced, the privilege the engine rolled for him in initiative. The host only
reads the printed sentence about the person and names the class it found.
"""
import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_recruit import CLASS_SLUGS, NOT_FROM_BOOK, read_hint
from solo.nat20_wizard import suggested_build
from solo.storage import Store
from solo.world import WorldLocation, WorldNPC


def build(class_slug, level=1):
    return suggested_build(class_slug=class_slug, species_slug='human',
                           background_slug='soldier', level=level)


def campaign(tmp_path, name='Партия', encounters=None):
    store = Store(tmp_path / 'recruit.sqlite3')
    api = Nat20GameEngine(store)
    identity = api.create_experimental_campaign(name, build=build('fighter'),
                                                encounters=encounters or {}, title=name)
    return store, api, identity['id']


def install_scene(store, cid, people):
    """The shape ``bind_library_module`` installs: one book place, its people.

    A module's opening place replaces the built-in tavern: the campaign stands
    where the pages said, with the people the pages printed. The ids are the ones
    ``world.import_package`` assigns, so the join route addresses a person the
    same way production does.
    """
    def transition(state):
        place = WorldLocation(name='Лагерь налётчиков', description='Лагерь у реки.',
                              known_to_hero=True,
                              npc=[WorldNPC(name=person['name'],
                                            private_knowledge=list(person.get('private_knowledge') or ()))
                                   for person in people])
        installed = place.model_dump()
        # Ids are assigned after validation, exactly as ``world.import_package``
        # does it: the world contract keeps them beside the printed record.
        for row, person in zip(installed['npc'], people):
            row['id'] = person['id']
        state['locations'] = [installed]
        state['location'] = place.name
        return [dict(type='scripted', text='Сцена модуля установлена')]

    return store.record_scenario(cid, 'scene-' + str(len(people)), 'Сцена модуля', transition)


FIGHTER_QUOTE = ('Источником громкого стука является безоружный голиаф-воин по имени '
                 'Капанук Высокое Дерево, который колотит по камню.')
SAILOR_QUOTE = ('У костра сидит человек по имени Тобб, он молчит и смотрит в огонь.')


def app_for(store):
    return create_app(store.path, CommandMockLLM({}), experimental=True)


def journal_kinds(store, cid):
    """Every typed block the journal holds for this campaign, newest last."""
    rows = []
    for row in store.history(cid):
        for result in row.get('results') or []:
            if result.get('kind'):
                rows.append(result)
    return rows


def test_book_warrior_joins_by_playing_and_fights_as_a_party_member(tmp_path):
    store, api, cid = campaign(tmp_path, 'Свой лист', encounters={
        'yard': dict(grid=dict(width=14, height=10), hero_position=dict(x=1, y=1, z=0),
                     actors=[dict(id='goblin', monster='goblin-warrior',
                                  position=dict(x=5, y=1, z=0))])})
    install_scene(store, cid, [dict(id='npc-kapanuk', name='Капанук Высокое',
                                    private_knowledge=[FIGHTER_QUOTE])])

    with TestClient(app_for(store)) as client:
        # The screen lists who stands beside the hero, with what the book said.
        listed = client.get(f'/api/campaigns/{cid}/party/candidates').json()
        assert [row['name'] for row in listed] == ['Капанук Высокое'], listed
        assert listed[0]['from_book'] is True
        assert listed[0]['class_slug'] == 'fighter'
        assert listed[0]['species_slug'] == 'goliath'
        # He joins while playing, with the reason the table gave.
        joined = client.post(f'/api/campaigns/{cid}/party/npc',
                             json=dict(npc='npc-kapanuk', reason='rescued',
                                       request_id='join-kapanuk'))
        assert joined.status_code == 200, joined.text
        member = joined.json()['member']
        assert member.startswith('companion-')
        campaign_state = client.get(f'/api/campaigns/{cid}').json()

    party = campaign_state['party']
    assert [row['role'] for row in party] == ['hero', 'companion']
    companion = party[1]
    # His sheet is the engine's: a class the material named, the hero's level.
    assert companion['classes'] == {'fighter': 1}
    assert companion['level'] == party[0]['level']
    assert companion['hp_max'] > 0 and companion['hp_current'] == companion['hp_max']
    assert companion['origin']['from_book'] is True
    assert companion['origin']['npc_id'] == 'npc-kapanuk'
    assert companion['origin']['reason'] == 'rescued'

    # The transformation is an event in the journal, with the reason and the NPC.
    joins = [row for row in journal_kinds(store, cid) if row['kind'] == 'npc_joined_party']
    assert len(joins) == 1, joins
    block = joins[0]['npc_joined_party']
    assert block['reason'] == 'rescued' and block['reason_text'] == 'спасён'
    assert block['npc_id'] == 'npc-kapanuk' and block['place'] == 'Лагерь налётчиков'
    assert 'спасён' in joins[0]['text']
    # The place does not show a man standing at the fire while his sheet walks.
    place = next(spot for spot in store.campaign(cid)['state']['locations']
                 if spot['name'] == 'Лагерь налётчиков')
    assert place['npc'][0]['present'] is False
    assert place['npc'][0]['party_member'] == member

    # The next fight: the engine rolled him his own initiative and his own turn.
    opened = api.execute(cid, GameCommand(request_id='fight', expected_version=store.campaign(cid)['version'],
                                          action=dict(kind='start_combat', encounter_id='yard')))
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    assert set(view['party_ids']) == {'hero', member}
    assert {row['entity_id'] for row in view['initiative']} == {'hero', member, 'goblin'}

    turns, outcomes = [], []
    for index in range(4):
        if not store.campaign(cid)['state']['nat20'].get('combat'):
            break
        version = store.campaign(cid)['version']
        result = api.execute(cid, GameCommand(request_id='turn-' + str(index),
                                              expected_version=version,
                                              action=dict(kind='end_turn')))
        turns += (result.get('result') or {}).get('companion_turns') or []
        outcomes.append(result['event'].payload['outcome'])
    assert turns, outcomes
    assert {turn['name'] for turn in turns} == {'Капанук Высокое'}, turns


def test_npc_without_a_class_in_the_material_joins_with_an_honest_note(tmp_path):
    store, api, cid = campaign(tmp_path, 'Без класса')
    install_scene(store, cid, [dict(id='npc-tobb', name='Тобб', private_knowledge=[SAILOR_QUOTE])])

    with TestClient(app_for(store)) as client:
        listed = client.get(f'/api/campaigns/{cid}/party/candidates').json()
        assert listed[0]['from_book'] is False, listed
        assert listed[0]['evidence'] == ''
        response = client.post(f'/api/campaigns/{cid}/party/npc',
                               json=dict(npc='npc-tobb', reason='asked'))
        assert response.status_code == 200, response.text
        party = client.get(f'/api/campaigns/{cid}').json()['party']

    companion = party[1]
    # A minimal legal sheet, and the record says it is not the book's.
    assert companion['hp_max'] > 0 and companion['classes']
    assert companion['origin']['from_book'] is False
    assert companion['origin']['note'] == NOT_FROM_BOOK
    joins = [row for row in journal_kinds(store, cid) if row['kind'] == 'npc_joined_party']
    assert NOT_FROM_BOOK in joins[0]['text'], joins[0]['text']


def test_a_companion_leaves_with_his_reason_and_the_party_shrinks(tmp_path):
    store, api, cid = campaign(tmp_path, 'Уход')
    install_scene(store, cid, [dict(id='npc-kapanuk', name='Капанук Высокое',
                                    private_knowledge=[FIGHTER_QUOTE])])
    with TestClient(app_for(store)) as client:
        client.post(f'/api/campaigns/{cid}/party/npc',
                    json=dict(npc='npc-kapanuk', reason='hired'))
        member = client.get(f'/api/campaigns/{cid}').json()['party'][1]['id']
        gone = client.delete(f'/api/campaigns/{cid}/party/{member}?reason=died')
        assert gone.status_code == 200, gone.text
        party = client.get(f'/api/campaigns/{cid}').json()['party']

    assert [row['role'] for row in party] == ['hero']
    lefts = [row for row in journal_kinds(store, cid) if row['kind'] == 'npc_left_party']
    assert len(lefts) == 1, lefts
    assert lefts[0]['npc_left_party']['reason'] == 'died'
    assert lefts[0]['npc_left_party']['reason_text'] == 'погиб'
    assert lefts[0]['npc_left_party']['npc_id'] == 'npc-kapanuk'
    # A dead companion does not reappear at the fire for the party to meet twice.
    place = next(spot for spot in store.campaign(cid)['state']['locations']
                 if spot['name'] == 'Лагерь налётчиков')
    assert place['npc'][0]['present'] is False
    assert 'party_member' not in place['npc'][0]


def test_walking_away_puts_the_person_back_in_his_place(tmp_path):
    store, api, cid = campaign(tmp_path, 'Ушёл')
    install_scene(store, cid, [dict(id='npc-kapanuk', name='Капанук Высокое',
                                    private_knowledge=[FIGHTER_QUOTE])])
    with TestClient(app_for(store)) as client:
        client.post(f'/api/campaigns/{cid}/party/npc',
                    json=dict(npc='npc-kapanuk', reason='asked'))
        member = client.get(f'/api/campaigns/{cid}').json()['party'][1]['id']
        client.delete(f'/api/campaigns/{cid}/party/{member}?reason=left')
        listed = client.get(f'/api/campaigns/{cid}/party/candidates').json()

    assert [row['name'] for row in listed] == ['Капанук Высокое']
    assert listed[0]['joined'] is False and listed[0]['present'] is True


@pytest.mark.parametrize('body,message', [
    (dict(npc='npc-nobody', reason='rescued'), 'Такого NPC в мире кампании нет'),
    (dict(npc='npc-kapanuk', reason='kidnapped'), None),
])
def test_an_impossible_join_is_refused_with_a_reason(tmp_path, body, message):
    store, api, cid = campaign(tmp_path, 'Отказ')
    install_scene(store, cid, [dict(id='npc-kapanuk', name='Капанук Высокое',
                                    private_knowledge=[FIGHTER_QUOTE])])
    with TestClient(app_for(store)) as client:
        response = client.post(f'/api/campaigns/{cid}/party/npc', json=body)
    assert response.status_code in (400, 409, 422), response.text
    if message:
        assert message in response.text, response.text
    # Nothing was written: the party is still the hero alone.
    assert [row['role'] for row in store.campaign(cid)['state']['nat20']['party']] == ['hero']


def test_a_book_person_without_an_id_is_addressed_by_his_name(tmp_path):
    """`bind_library_module` installs the opening scene's people without ids.

    The world contract already requires a name to identify one person, so the
    route addresses him by that name instead of refusing a campaign the book
    started.
    """
    store, api, cid = campaign(tmp_path, 'Без id')
    install_scene(store, cid, [dict(id='npc-tobb', name='Тобб', private_knowledge=[SAILOR_QUOTE])])
    def strip(state):
        for person in state['locations'][0]['npc']:
            person.pop('id', None)
        return [dict(type='scripted', text='идентификаторы сняты, как у сцены из книги')]
    store.record_scenario(cid, 'strip-ids', 'Сцена без id', strip)

    with TestClient(app_for(store)) as client:
        listed = client.get(f'/api/campaigns/{cid}/party/candidates').json()
        assert [row['id'] for row in listed] == ['Тобб'], listed
        joined = client.post(f'/api/campaigns/{cid}/party/npc',
                             json=dict(npc='Тобб', reason='hired'))
        assert joined.status_code == 200, joined.text
        party = client.get(f'/api/campaigns/{cid}').json()['party']

    assert party[1]['name'] == 'Тобб'
    assert party[1]['origin']['npc_id'] == 'Тобб'
    assert party[1]['origin']['reason'] == 'hired'


def test_a_person_already_in_the_party_cannot_join_twice(tmp_path):
    store, api, cid = campaign(tmp_path, 'Дважды')
    install_scene(store, cid, [dict(id='npc-kapanuk', name='Капанук Высокое',
                                    private_knowledge=[FIGHTER_QUOTE])])
    with TestClient(app_for(store)) as client:
        first = client.post(f'/api/campaigns/{cid}/party/npc',
                            json=dict(npc='npc-kapanuk', reason='rescued'))
        assert first.status_code == 200, first.text
        second = client.post(f'/api/campaigns/{cid}/party/npc',
                             json=dict(npc='npc-kapanuk', reason='hired'))
    assert second.status_code in (400, 409), second.text
    assert 'уже в партии' in second.text
    assert len(store.campaign(cid)['state']['nat20']['party']) == 2


def test_the_reading_does_not_take_another_persons_class():
    """«…Квентин Скотт и маг Вальтер» names two people: no class for Квентин."""
    hint = read_hint('Квентин Скотт', 'Его охрана — это сквайр, только получивший свой меч, '
                                      'по имени Квентин Скотт и маг Вальтер.')
    assert hint['from_book'] is False, hint
    assert hint['note'] == NOT_FROM_BOOK
    # The same sentence read for Вальтер does name a class — the parser is not
    # simply refusing everything.
    other = read_hint('Вальтер', 'Его охрана — это сквайр, только получивший свой меч, '
                                 'по имени Квентин Скотт и маг Вальтер.')
    assert other['class_slug'] == 'wizard', other
    # A capitalised epithet is a title, not a class description.
    assert read_hint('Векна', 'Злой Волшебник по имени Векна.')['from_book'] is False
    # And a class printed next to the name is read with its species.
    named = read_hint('Капанук Высокое', FIGHTER_QUOTE)
    assert named['class_slug'] == 'fighter' and named['species_slug'] == 'goliath'


def test_the_class_list_is_the_engines_own_catalogue():
    from dnd5e_engine.lib_loader import get_lib_loader

    assert set(CLASS_SLUGS) == set(get_lib_loader().list_slugs('classes'))
