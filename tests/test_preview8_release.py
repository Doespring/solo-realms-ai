"""Legacy release guards remain in force beside typed prototype native actions."""
import json
import sys
from pathlib import Path

import pytest
from solo.contracts import Hero
from solo.game_commands import GameCommand, tool_schemas
from solo.nat20_api import Nat20GameEngine
from solo.nat20_bless import PROFILE, RELEASE, require_stand
from solo.nat20_campaign import load_definition
from solo.nat20_content import release
from solo.storage import Store

BASE_TOOLS = {'check', 'start_combat', 'move', 'dash', 'attack', 'end_turn', 'short_rest',
              'long_rest', 'end_combat', 'use_feature', 'native_action', 'level_up',
              'scene_action'}


def test_pending_is_not_a_normal_capability(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    monkeypatch.delenv('SOLO_NAT20_BLESS_STAND', raising=False)
    assert {t['function']['name'] for t in tool_schemas()} == BASE_TOOLS
    assert release()[0].get_spell('bless') is None
    for operation in (require_stand, lambda: load_definition(PROFILE), lambda: release(RELEASE)):
        with pytest.raises(ValueError, match='activation pending'):
            operation()
    with pytest.raises(ValueError, match='release changed'):
        release('0' * 64)


def test_frozen_flags_cannot_enable_stand(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    monkeypatch.setenv('SOLO_NAT20_BLESS_STAND', '1')
    assert {t['function']['name'] for t in tool_schemas()} == BASE_TOOLS | {'cast_spell', 'drop_concentration'}
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert {t['function']['name'] for t in tool_schemas()} == BASE_TOOLS
    for operation in (require_stand, lambda: load_definition(PROFILE), lambda: release(RELEASE)):
        with pytest.raises(ValueError, match='activation pending'):
            operation()


def test_real_preview7_fighter_restores_and_continues_without_stand(tmp_path, monkeypatch):
    monkeypatch.delenv('SOLO_NAT20_BLESS_STAND', raising=False)
    fixture = json.loads((Path(__file__).parent/'fixtures/nat20-preview7-fighter-combat.json').read_text(encoding='utf-8'))
    old = fixture['campaign']
    api = Nat20GameEngine(Store(tmp_path/'old-fighter.sqlite3'))
    cid = api.store.create('Preview7 unchanged fixture', Hero.model_validate(old['state']['hero']), initial_state=old['state'])['id']
    assert release()[1] == fixture['release_digest']
    assert api.restore(cid)['runtime'] == old['state']['nat20']
    version = api.store.campaign(cid)['version']
    api.execute(cid, GameCommand(request_id='preview8-next', expected_version=version, action={'kind':'end_turn'}))
    current = api.store.campaign(cid)
    assert current['version'] == version + 1
    assert current['state']['nat20']['release_digest'] == fixture['release_digest']
    assert 'effects' not in current['state']['engine_snapshot']
