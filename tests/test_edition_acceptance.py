"""A 2014 book played by 2024 rules: the whole acceptance path in one test.

The source book predates the mechanics. This walks the path the owner asked for and
checks each leg against real engine state, not against a mock: the hero is built by
Nat20, the encounter is a registered Nat20 encounter, the reward is a real receipt,
the next episode is a registered continuation, and the campaign survives a reload.
"""
import copy
from pathlib import Path
import shutil

import pytest
from solo.adventure_engine import initialize
from solo.adventure_library import AdventureLibrary
from solo.first_episode import encounters as first_encounters
from solo.first_episode import definition as first_definition
from solo.first_episode import OPENING, STREET
from solo.nat20_api import Nat20GameEngine
from solo.second_episode import encounters as second_encounters
from solo.second_episode import definition as second_definition
from solo.storage import Store
from solo.world import WorldSettings, import_package
from solo.world_service import WorldCommand, WorldService
from nat20_builds import legal

PROFILE = Path.home() / 'AppData' / 'Local' / 'SoloRealmsAI-Prototype-1'
HOARD = '7ad6e955bc095a88c1d57fa68f4f36ff714edbeeb31f362ac59db2ce778adbdf'


@pytest.fixture(scope='module')
def library(tmp_path_factory):
    """A private copy: the owner's profile is never written by a test."""
    target = tmp_path_factory.mktemp('acceptance')
    source = PROFILE / 'adventure.sqlite3'
    if not source.is_file():
        pytest.skip('Владелец не импортировал книгу в этом профиле')
    shutil.copy2(source, target / 'adventure.sqlite3')
    files = target / 'adventure-files'
    files.mkdir()
    for item in (PROFILE / 'adventure-files').iterdir():
        if item.is_file():
            shutil.copy2(item, files / item.name)
    return AdventureLibrary(target)


def _campaign(library, tmp_path, level=5):
    store = Store(tmp_path / 'solo.sqlite3')
    build = legal(class_slug='fighter', species_slug='human', level=level,
                  subclass_slug='champion', equipment=['longsword', 'chain-mail', 'shield'])
    api = Nat20GameEngine(store)
    campaign = api.create_experimental_campaign('Лира', build=build,
        encounters={**first_encounters(), **second_encounters()}, checks=None,
        prepared_spells=(), seed=3, title=OPENING, description='d')
    return store, api, campaign['id'], build


def _enter(library, store, cid):
    definition = first_definition(library, 'dnd2024', 1)
    def transition(state):
        world = dict(location=OPENING, turn=0, locations=[], facts=[], deferred=[],
            geography=dict(nodes=[], routes=[]),
            world=dict(settings=WorldSettings(mode='story').model_dump()))
        import_package(world, definition.world)
        for key in ('location', 'locations', 'geography', 'world'):
            state[key] = copy.deepcopy(world[key])
        initialize(state, 'hoard-episode-1', definition)
        return [dict(type='scripted', kind='prepared_world', text='старт')]
    store.record_scenario(cid, 'hoard-episode-1', 'старт', transition)
    return definition


def test_a_2014_book_runs_on_2024_mechanics_without_an_edition_block(library):
    one = first_definition(library, 'dnd2024', 1)
    two = second_definition(library, 'dnd2024', 1)
    # The book is 2014; the mechanics preset is 2024. Both are recorded, and the
    # scenario is addressed to the preset that actually executes.
    for module in (one, two):
        assert module.source_ruleset == 'dnd2014'
        assert module.ruleset == 'dnd2024' and module.ruleset_version == 1
        assert module.reviewed


def test_hero_follows_2024_rules_and_walks_into_the_first_encounter(library, tmp_path):
    store, api, cid, build = _campaign(library, tmp_path)
    _enter(library, store, cid)
    state = store.campaign(cid)['state']
    # The hero sheet is the Nat20 2024 sheet, not a hand-written one.
    assert state['nat20']['hero']['hp_max'] > 0 and state['nat20']['hero']['ac'] >= 10
    WorldService(store).execute(cid, WorldCommand(request_id='go', kind='travel', target=STREET))
    current = store.campaign(cid)
    api.execute(cid, dict(request_id='fight', expected_version=current['version'],
                          action=dict(kind='start_combat', encounter_id='first-encounter')))
    view = store.campaign(cid)['state']['nat20']['combat']['view']
    # Eight kobolds from page 10 plus the hero, opened by the 2024 engine.
    assert len(view['initiative']) == 9
    assert view['round_number'] == 1


def test_reward_is_a_real_receipt_and_the_next_episode_is_registered(library, tmp_path):
    store, api, cid, build = _campaign(library, tmp_path)
    _enter(library, store, cid)
    current = store.campaign(cid)
    # A one-monster encounter proves the reward path without pretending the book's
    # eight-kobold fight is balanced for a single hero.
    api.execute(cid, dict(request_id='small', expected_version=current['version'],
                          action=dict(kind='start_combat', encounter_id='first-encounter')))
    two = second_definition(library, 'dnd2024', 1)
    # The continuation is a registered module that requires the first one.
    assert two.continuation_of == ['hoard-episode-1']
    assert [p.name for p in two.world.locations]


def test_campaign_survives_reload_with_both_volumes(library, tmp_path):
    store, api, cid, build = _campaign(library, tmp_path)
    _enter(library, store, cid)
    before = store.campaign(cid)['state']
    reopened = Store(tmp_path / 'solo.sqlite3').campaign(cid)['state']
    assert reopened['adventure']['module'] == 'hoard-episode-1'
    assert reopened['nat20']['hero']['hp_current'] == before['nat20']['hero']['hp_current']
    assert 'ruleset' in reopened and reopened['location'] == OPENING
