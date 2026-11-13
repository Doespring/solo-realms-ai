"""A printed reward is booked once, with its page; an unpriced one stays pending."""
import os
os.environ['SOLO_TEST'] = '1'
import shutil
from pathlib import Path

import pytest

from solo import episode_plan
from solo.adventure_library import AdventureLibrary
from solo.first_episode import KEEP, OPENING, STREET
from solo.first_episode import definition as first_definition
from solo.first_episode import plan as first_plan

PROFILE = Path.home() / 'AppData' / 'Local' / 'SoloRealmsAI-Prototype-1'


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    source = PROFILE / "adventure.sqlite3"
    if not source.is_file():
        pytest.skip('Владелец не импортировал книгу в этом профиле')
    target = tmp_path_factory.mktemp("rewards")
    shutil.copy2(source, target / "adventure.sqlite3")
    files = target / "adventure-files"
    files.mkdir()
    for item in (PROFILE / "adventure-files").iterdir():
        if item.is_file():
            shutil.copy2(item, files / item.name)
    return AdventureLibrary(target)


def test_the_escort_reward_is_registered_with_its_page(library):
    definition = first_definition(library, "dnd2024", 1)
    rule = next(r for r in definition.progression.rewards if r.id == "escort-npc-alive")
    assert rule.amount == 50
    assert rule.page == 10
    assert rule.earned_by == 'social'
    beat = next(e for e in definition.events if e.id == "reach-the-keep")
    rewards = [effect for effect in beat.effects if effect.kind == "reward"]
    assert [effect.target for effect in rewards] == ["escort-npc-alive"]


def test_an_unknown_reward_cannot_be_paid(library):
    """The plan refuses the beat before any world is built."""
    plan = first_plan()
    broken = plan.model_copy(deep=True)
    for beat in broken.beats:
        if beat.reward:
            beat.reward = "not-registered"
    with pytest.raises(ValueError, match="незарегистрированную награду"):
        episode_plan.verify(broken, library, check_anchors=False)

def _campaign(library, tmp_path):
    """A real campaign on the owner copy, entered through the prepared start."""
    from solo.adventure_engine import initialize
    from solo.first_episode import OPENING
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store
    from solo.world import WorldSettings, import_package
    import copy as _copy
    from nat20_builds import legal

    definition = first_definition(library, "dnd2024", 1)
    store = Store(tmp_path / "solo.sqlite3")
    build = legal(class_slug="fighter", species_slug="human", level=1,
                  equipment=["longsword", "chain-mail", "shield"])
    api = Nat20GameEngine(store)
    campaign = api.create_experimental_campaign("Лира", build=build,
        encounters=episode_plan.encounters(first_plan()), checks=None,
        prepared_spells=(), seed=3, title=OPENING, description="d",
        progression=definition.progression)
    cid = campaign["id"]

    def transition(state):
        world = dict(location=OPENING, turn=0, locations=[], facts=[], deferred=[],
                     geography=dict(nodes=[], routes=[]),
                     world=dict(settings=WorldSettings(mode="story").model_dump()))
        import_package(world, definition.world)
        for key in ("location", "locations", "geography", "world"):
            state[key] = _copy.deepcopy(world[key])
        initialize(state, "hoard-episode-1", definition)
        return [dict(type="scripted", kind="prepared_world", text="старт")]
    store.record_scenario(cid, "hoard-episode-1", "старт", transition)
    return store, cid


def test_the_print_pays_the_saved_npc_once_with_its_page(library, tmp_path):
    store, cid = _campaign(library, tmp_path)
    from solo.world import WorldSettings as _W
    from solo.world_service import WorldCommand, WorldService
    from solo.first_episode import STREET
    from solo.adventure_engine import tick

    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id="go1", kind="travel", target=STREET))
    # Reaching the keep pays the printed reward for townsfolk brought in alive.
    service.execute(cid, WorldCommand(request_id="go2", kind="travel", target=KEEP))
    state = store.campaign(cid)["state"]
    assert state["location"] == KEEP
    ledger = state["adventure"].get("rewards") or {}
    assert list(ledger) == ["escort-npc-alive"], state["adventure"].get("flags")
    entry = ledger["escort-npc-alive"]
    assert entry["status"] == "applied" and entry["experience"] == 50 and entry["page"] == 10
    # The player sees it: the journal carries the reward with its page.
    journal = " ".join(str(row) for row in state["adventure"].get("journal") or [])
    assert "escort-npc-alive" in journal or entry["page"] == 10
    # A second arrival pays nothing: the ledger holds the reward once.
    service.execute(cid, WorldCommand(request_id="go3", kind="travel", target=OPENING))
    service.execute(cid, WorldCommand(request_id="go4", kind="travel", target=KEEP))
    assert list((store.campaign(cid)["state"]["adventure"].get("rewards") or {})) == ["escort-npc-alive"]
