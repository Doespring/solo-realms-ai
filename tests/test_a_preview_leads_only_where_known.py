"""A path preview may only lead to a place the hero has come to know."""
import os
os.environ['SOLO_TEST']='1'

import json
import sys
from pathlib import Path

import pytest

from solo.world import known
from solo.world_service import WorldService

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_scenario


def test_a_preview_cannot_lead_to_an_unknown_place(tmp_path):
    store, cid, _ = make_scenario(Path(tmp_path) / 'preview')
    state = dict(store.campaign(cid)['state'])
    state["locations"] = list(state["locations"]) + [
        dict(state["locations"][0], name="Забытая часовня",
             known_to_hero=False)]
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?",
                   (json.dumps(state, ensure_ascii=False), cid))
    saved = store.campaign(cid)["state"]
    assert known(saved, "Забытая часовня") is False, "the place became known"
    with pytest.raises(ValueError) as caught:
        WorldService(store).preview(cid, "Забытая часовня")
    assert str(caught.value) == 'Это место пока неизвестно герою', (
        'refused with %r' % str(caught.value))
