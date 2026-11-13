"""Integrator seam only: explicit fresh profiles and no implicit default DB."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from solo.app import ROOT, create_app
from solo.preview_mock import PreviewMock
from solo.prototype_profile import prepare_profile,default_profile


def test_bare_import_never_opens_project_data(tmp_path):
    files = [p for p in (ROOT/'data').rglob('*') if p.is_file()]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    env = {k:v for k,v in os.environ.items() if k not in ('SOLO_TEST','SOLO_DATA_DIR')}
    env['PYTHONDONTWRITEBYTECODE']='1'
    child=subprocess.run([sys.executable,'-B','-c',
        'import solo.app; assert solo.app.app is None'],cwd=ROOT,env=env,
        capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}==before


def test_profile_resume_refuses_foreign_or_project_data(tmp_path):
    assert default_profile()!=default_profile(offline=True)
    with pytest.raises(ValueError, match='защищена'):
        prepare_profile(ROOT/'data')
    foreign=tmp_path/'old';foreign.mkdir();(foreign/'solo.sqlite3').write_bytes(b'old save')
    with pytest.raises(ValueError, match='пустой'):
        prepare_profile(foreign)
    assert (foreign/'solo.sqlite3').read_bytes()==b'old save'
    profile=prepare_profile(tmp_path/'new')
    assert prepare_profile(profile)==profile
    with pytest.raises(ValueError, match='защищена'):
        create_app(ROOT/'data/solo.sqlite3',PreviewMock())


@pytest.mark.parametrize('offline', [True, False])
def test_isolated_app_serves_ui_and_preserves_cloud_setup(tmp_path, offline):
    data=prepare_profile(tmp_path/'profile')
    app=create_app(data/'solo.sqlite3',PreviewMock() if offline else None,
                   experimental=True,offline_mock=offline)
    with TestClient(app) as client:
        html=client.get('/')
        assert html.status_code==200 and 'ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОТОТИП' in html.text
        assert ('OFFLINE MOCK' in html.text)==offline
        health=client.get('/api/health').json()
        assert health['experimental'] and health['offline']==offline
        assert health['cloud_only'] is not offline
        if not offline:assert not health['llm']
        status=client.get('/api/prototype/status').json()
        assert status['preserved_data_incident']=='open'
        assert status['components'].keys()=={'register_prototype_ui_api','register_prototype_gm_api'}
        assert client.get('/api/ai/profiles').status_code==200
        assert client.get('/api/campaigns').json()==[]
        assert app.state.cloud_settings.path.parent==data
        with app.state.costs.db() as db:
            assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0
