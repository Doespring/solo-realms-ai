"""The synthetic PDF narrator must accept a scene packet, not only a conflict one.

The packet the narrator receives is built around the scene (place, npc,
atmosphere, observation). The fixture's route used to key only on
'outcomes'/'player_words'/'description_sources', so a plain look-around was
rejected with HTTP 400 and `narrative_grounding.fallback` was unreachable.

These tests go through the real mock transport, so reverting the route
breaks them. A copy of the rule would not: it would keep passing while the
fixture regressed.
"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest


def _transport(tmp_path):
    from solo.prototype_profile import prepare_profile
    from scripts.smoke_fixture import make_pdf_world_smoke_llm
    profile = tmp_path / 'profile'
    profile.mkdir()
    prepare_profile(profile, offline=True)
    llm = make_pdf_world_smoke_llm(profile, smoke_test=True, phase='prepare')
    return llm


def _send(llm, packet):
    body = dict(model='synthetic', messages=[dict(role='user', content=json.dumps(packet))])
    request = httpx.Request('POST', 'https://pdf-smoke.invalid/v1/chat/completions', json=body)
    return asyncio.run(llm.client.post(request.url, json=body))


@pytest.mark.parametrize('packet', [
    # The exact shape the narrator sends for 'Осматриваюсь'. It carries no
    # outcomes and no description_sources, so only the scene keys can route it.
    dict(packet_version=2, place='Двор', atmosphere='Тихо', observation=True,
         confirmed=[], npc=[], memory=[]),
    dict(packet_version=2, place='Двор', atmosphere='Тихо'),
])
def test_a_scene_packet_without_outcomes_reaches_the_narrator(tmp_path, packet):
    llm = _transport(tmp_path)
    answer = _send(llm, packet)
    assert answer.status_code == 200, answer.text
    kinds = [row['kind'] for row in llm.smoke_packet_log]
    assert kinds == ['narrator'], kinds


@pytest.mark.parametrize('packet', [
    {},
    {'foo': 1},
    # A place alone is not a scene: the narrator always sends its atmosphere
    # or its occupant list with it, and anything else is a foreign packet.
    {'place': 'Перекрёсток'},
])
def test_a_packet_that_is_not_a_scene_is_still_rejected(tmp_path, packet):
    llm = _transport(tmp_path)
    answer = _send(llm, packet)
    assert answer.status_code == 400, answer.text
    assert not llm.smoke_packet_log

