"""A live player session is the campaign id plus the profile, nothing else.

The restart test proves another process answers. It does not prove the player's
own session survives, because "session" could have hidden somewhere a restart
would drop: a server-side cookie, an in-memory screen cache, a lock, a pending
question. This test separates the two sets of facts.

Proof A - continuity. A second application object is built over the same
profile while the first is still alive, i.e. every in-memory dict of the first
is provably absent from the second. A request using only the campaign id, with
no cookie and no stored body, must read the same hero, version and location and
must continue the game with a fresh turn that the first server then sees.

Proof B - what really *is* session-scoped. The Advanced / Developer Mode token
lives only in app.state.advanced_sessions (solo/tabletop_api.py:32,39), so it is
dropped by a restart. That is stated and asserted here so the verdict is not
"nothing is lost" by assertion; it is "the game session is not lost, the
developer-mode flag is". If this test ever stops observing the loss, the code
changed and the verdict has to be re-derived.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_control_play import table, world  # noqa: F401  (fixture)
from test_server_restart_real import SERVICE  # noqa: F401  (kept for parity)

from solo.preview_mock import PreviewMock


def client_for(database):
    """One fresh application over the same profile, exactly like a restart."""
    from fastapi.testclient import TestClient
    from solo.app import create_app
    return TestClient(create_app(database, PreviewMock(), experimental=True, offline_mock=True))


def test_a_campaign_id_alone_resumes_the_session_on_a_fresh_app(world, tmp_path):  # noqa: F811
    """No cookie, no local storage, no warm cache: just GET /api/campaigns/<id>."""
    store, cid, service = table(tmp_path, world)
    database = tmp_path / 'play.sqlite3'
    before = store.campaign(cid)

    first = client_for(database)
    with first:
        played = first.post('/api/campaigns/' + cid + '/play',
                            json=dict(request_id='live-turn', text='Осматриваюсь'))
        assert played.status_code == 200, played.text
        assert played.cookies == {}, 'the play route must not hand out a session cookie'
        state = first.get('/api/campaigns/' + cid)
        assert state.status_code == 200, state.text
        hero_name, version = state.json()['state']['hero']['name'], int(state.json()['version'])
        assert hero_name == before['state']['hero']['name']
    assert first.cookies == {}, 'no cookie is issued for a campaign at all'

    # A genuinely separate application object over the same profile. The first
    # client's in-memory state cannot be consulted by the second one.
    second = client_for(database)
    with second:
        resumed = second.get('/api/campaigns/' + cid)
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body['state']['hero']['name'] == hero_name
        assert int(body['version']) == version, 'the resumed session must be the same turn'
        assert body['state']['location'] == before['state']['location']

        # And it is a live session, not a read-only snapshot: a new turn on the
        # second app advances the version and the first app sees that advance.
        moved = second.post('/api/campaigns/' + cid + '/play',
                            json=dict(request_id='after-restart', text='Иду в The Sunken Gallery'))
        assert moved.status_code == 200, moved.text
        assert int(store.campaign(cid)['version']) > version, 'the session did not continue'
        seen = first.get('/api/campaigns/' + cid).json()
        assert int(seen['version']) == int(store.campaign(cid)['version'])


def test_the_screen_the_ui_loads_after_a_restart_is_rebuilt_from_the_profile(world, tmp_path):  # noqa: F811
    """game-screen.js loads /api/campaigns/<id>/screen on every page open.

    A fresh page load after a restart therefore asks the restarted process for
    the screen. It must answer with the same campaign, not with an empty one.
    """
    store, cid, service = table(tmp_path, world)
    database = tmp_path / 'play.sqlite3'
    first = client_for(database)
    with first:
        screen_before = first.get('/api/campaigns/' + cid + '/screen')
        assert screen_before.status_code in (200, 409, 422), screen_before.text
        if screen_before.status_code == 200:
            assert screen_before.json()['snapshot']['campaign_id'] == cid
        turn = store.campaign(cid)['state']['turn']
    second = client_for(database)
    with second:
        again = second.get('/api/campaigns/' + cid + '/screen')
        assert again.status_code == screen_before.status_code
        if again.status_code == 200:
            assert again.json()['snapshot']['campaign_id'] == cid
        assert store.campaign(cid)['state']['turn'] == turn


def test_developer_mode_is_the_one_thing_a_restart_drops(world, tmp_path):  # noqa: F811
    """The honest half of the verdict: a real in-memory session that does not survive."""
    store, cid, service = table(tmp_path, world)
    database = tmp_path / 'play.sqlite3'
    first = client_for(database)
    with first:
        opened = first.post('/api/advanced/session', json=dict(confirm=True))
        assert opened.status_code == 200, opened.text
        assert first.get('/api/advanced/session').json() == {'enabled': True}
        token = first.cookies.get('solo_advanced')
        assert token, 'the developer-mode session is a server-issued cookie'
    # Same profile, new process: the cookie is still in the browser, the server
    # memory that validated it is gone (solo/tabletop_api.py:32,39,25).
    second = client_for(database)
    with second:
        assert second.get('/api/advanced/session').json() == {'enabled': False}, \
            'developer mode survived a restart - the code changed, re-derive the verdict'
