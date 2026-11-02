"""The server must name the interface it speaks, and the page must notice a mismatch.

Section 9.4 asks for build identity that is truthful. 'build' says which code is
running; 'contract' says whether that code still answers in the shape the open
page understands. Without it, a page built against an older interface fails
later, on a field that quietly changed shape.
"""
import os
from pathlib import Path
os.environ['SOLO_TEST']='1'

import json
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.build_identity import build_identity

from test_dialogue_paging_api import MockLLM

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'play.js')


def test_health_names_the_contract(tmp_path):
    api = TestClient(create_app(tmp_path/'c.sqlite3', MockLLM()))
    body = api.get('/api/health').json()
    assert body.get('contract'), 'a client cannot check what the server does not say'
    assert body['contract'] == build_identity()['contract']


def test_the_page_knows_the_same_contract():
    source = open(UI, encoding='utf-8').read()
    assert "UI_CONTRACT" in source, 'the page does not check the contract at all'
    assert build_identity()['contract'] in source, 'the page and the server disagree on the contract'


def test_a_mismatch_is_reported_to_the_player():
    source = open(UI, encoding='utf-8').read()
    assert 'contractMismatch' in source, 'a mismatch must be visible, not silent'
    assert 'Версия сервера не совпадает' in source, 'the message must be in words a player reads'

def test_the_page_actually_reports_a_mismatch():
    """Run the page's own health logic, do not merely look for its words.

    Reading web/play.js for the string 'contractMismatch' proves the identifier
    exists somewhere, not that the branch runs. This executes the block against
    a fake answer and checks what the player would see.
    """
    import json
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[1]
    script = root / 'runtime' / 'ui-tmp' / 'uiproxy.cjs'
    if not script.exists() or shutil.which('node') is None:
        import pytest
        pytest.skip('the page check needs node and its harness script')
    done = subprocess.run(['node', str(script), str(root)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line for line in done.stdout.splitlines() if line.startswith('RESULT ')]
    seen = {line.split()[1]: line.split(' ', 2)[2] for line in lines}
    assert seen.get('mismatch_flagged') == 'true'
    assert seen.get('mismatch_named') == 'true'
    assert seen.get('missing_is_not_mismatch') == 'true'


def test_a_file_changed_on_disk_is_reported_as_drift(tmp_path, monkeypatch):
    """A running process that no longer matches its files must say so.

    Changing the bytes on disk and reading identity again has to raise drift:
    otherwise an old process keeps presenting itself as the new version, which
    is exactly what section 9.4 forbids.
    """
    from solo import build_identity

    page = build_identity.ROOT / 'web' / 'play.js'
    before = page.read_bytes()
    build_identity._freeze.cache_clear()
    try:
        quiet = build_identity.build_identity(refresh=True)
        assert quiet['drift'] is False, 'nothing changed yet'
        page.write_bytes(before + b'\n// drift probe\n')
        changed = build_identity.build_identity(refresh=True)
        assert changed['drift'] is True, 'a changed file must surface as drift'
        assert changed['build'] == quiet['build'], 'the start digest must not move'
    finally:
        page.write_bytes(before)
        build_identity._freeze.cache_clear()
