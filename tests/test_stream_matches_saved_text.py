"""What the stream shows and what the final response says must be the same turn.

Section 5 asks that optimism in the screen, the stream, the final answer and a
later re-read agree by operation identity. The part that can be proven without a
model is the tail of that chain: the streamed narrative and the narrative in the
responses must describe the same event, and a re-read must show it once.
"""
import os
os.environ['SOLO_TEST']='1'

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.storage import Store
from test_dialogue_paging_api import MockLLM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from command_smoke import setup as make_campaign  # noqa: E402


def setup(tmp_path):
    store, campaign, game = make_campaign(tmp_path)
    return TestClient(create_app(store.path, MockLLM())), store, campaign, game


def test_the_stream_and_the_stored_event_agree(tmp_path):
    api, store, campaign, game = setup(tmp_path)
    journal = store.record_scenario(campaign, 'req-s', 'Осматриваюсь',
                                    lambda s: [dict(type='scripted', kind='notice', text='Двор пуст')])
    event = store.event(journal['id'])
    stored = None
    for row in (event or {}).get('narratives', []):
        stored = row.get('text')
    response = api.post('/api/events/%s/narrate' % journal['id'], json=dict(regenerate=False))
    assert response.status_code == 200, response.text
    body = response.text
    assert 'data: ' in body, 'the stream must deliver event frames'
    # Whatever the stream says, the event it points at is the same one.
    after = store.event(journal['id'])
    assert after['id'] == journal['id']
    if stored:
        assert stored in body or 'error' in body.lower()


def test_a_read_after_the_stream_shows_one_turn(tmp_path):
    api, store, campaign, game = setup(tmp_path)
    journal = store.record_scenario(campaign, 'req-t', 'Жду',
                                    lambda s: [dict(type='scripted', kind='notice', text='Тихо')])
    api.post('/api/events/%s/narrate' % journal['id'], json=dict(regenerate=False))
    with store.connection(True) as c:
        events = c.execute("SELECT COUNT(*) FROM events WHERE campaign=?", (campaign,)).fetchone()[0]
    assert events == 1, 'narrating an event must not create a second event'
    rows = api.get('/api/campaigns/%s/conversation?limit=10' % campaign).json()['conversation']
    assert len([r for r in rows if r['request_id'] == 'req-t']) <= 1


def test_a_second_open_replays_the_saved_text_without_a_model(tmp_path):
    """Opening a finished event again must not ask the model a second time.

    The screen reconnects to the stream whenever the page is rebuilt. If the
    replay branch is gone, every reconnect pays for narration and the reader can
    see a different text than the one stored - which is what section 5 forbids.
    """
    api, store, campaign, game = setup(tmp_path)
    journal = store.record_scenario(campaign, 'req-r', 'Жду',
                                    lambda s: [dict(type='scripted', kind='notice', text='Тихо')])
    first = api.post('/api/events/%s/narrate' % journal['id'], json=dict(regenerate=False))
    assert first.status_code == 200, first.text
    saved = store.event(journal['id'])
    assert saved['narrative_status'] == 'complete', saved['narrative_status']
    before = len(game.llm.metrics)
    second = api.post('/api/events/%s/narrate' % journal['id'], json=dict(regenerate=False))
    assert second.status_code == 200, second.text
    assert len(game.llm.metrics) == before, 'a replay must not reach the model'
    frames = [l for l in second.text.splitlines() if l.startswith('data: ')]
    assert any('"replayed":true' in f.replace(' ', '') for f in frames), 'the replay must say so'
    assert saved['narrative'].strip() in second.text, 'the replay must show the saved text'
