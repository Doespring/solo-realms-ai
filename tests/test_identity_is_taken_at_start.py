"""The start identity must describe the start, not the first question.

Asked lazily, the frozen identity described whatever the disk held at the first
/api/health call. An edit made in between became the baseline, so the very
change the check exists to catch was reported as drift=False - and stayed
invisible for the life of the process.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM


def test_the_identity_is_taken_before_the_first_question(tmp_path, monkeypatch):
    """A change made after start must be drift, even if nobody asked yet."""
    import solo.build_identity as identity

    seen = {}
    real = identity.build_identity

    def record(refresh=False):
        result = real(refresh)
        if not seen:
            seen['ui'] = result['ui']
            seen['taken'] = True
        return result

    monkeypatch.setattr(identity, 'build_identity', record)
    app = create_app(tmp_path/'id.sqlite3', MockLLM())
    with TestClient(app) as client:
        assert seen.get('taken'), 'the identity is taken while the server starts'
        first = client.get('/api/health').json()
    assert first['ui'], first
    assert first['drift'] is False and first['ui_drift'] is False


def test_drift_appears_and_clears_with_the_disk(tmp_path, monkeypatch):
    import solo.build_identity as identity
    app = create_app(tmp_path/'id2.sqlite3', MockLLM())
    with TestClient(app) as client:
        before = client.get('/api/health').json()
        # The start identity stays put; only the disk answer moves.
        monkeypatch.setattr(identity, '_ui_files', lambda: [])
        after = client.get('/api/health').json()
    assert before['ui'] == after['ui'], 'the start identity never changes'
    assert after['ui_drift'] is True, 'an emptied file set must read as drift'
    assert after['ui_disk'] != after['ui'], 'the diverged value is reported too'
