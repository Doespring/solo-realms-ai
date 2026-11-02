"""Only places the hero knows are offered as destinations."""
import os
os.environ['SOLO_TEST']='1'

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_an_unknown_place_is_not_offered(tmp_path):
    store, cid, game = make_scenario(Path(tmp_path) / 'dest')
    campaign = store.campaign(cid)
    state = copy.deepcopy(campaign["state"])
    hidden = copy.deepcopy(state["locations"][0])
    hidden["name"] = "Забытая часовня"
    hidden["known_to_hero"] = False
    state["locations"].append(hidden)
    campaign["state"] = state

    scene = getattr(game, "scene", None) or game.compiler.scene
    offered = scene(campaign)["destinations"]
    assert offered, "the hero was offered no destinations at all"
    assert "Забытая часовня" not in offered, (
        "a place the hero never learned was offered: %r" % offered)