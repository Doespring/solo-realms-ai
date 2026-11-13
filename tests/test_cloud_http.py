"""HTTP/SSE workflow with an explicitly mocked remote provider, never paid inference."""
import json
from fastapi.testclient import TestClient
from solo.app import create_app
from test_cloud import configured,sse


def test_http_connection_profile_is_not_a_fake_success_without_key(tmp_path):
    app=create_app(tmp_path/'offline.sqlite3')
    with TestClient(app) as c:
        health=c.get('/api/health').json()
        assert health['cloud_only'] and health['llm'] is None and health['readiness']['status']=='unconfigured'
        assert c.get('/api/ai/costs').json()['calls']==[]
        assert c.get('/').status_code==200 and c.get('/static/cloud.js').status_code==200
