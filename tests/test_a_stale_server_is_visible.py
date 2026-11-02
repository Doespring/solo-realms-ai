"""A process must not pass itself off as the files currently on disk.

build_identity() used to answer from a cache filled at the first call, which can
only describe the moment the process started. The health answer was built from
that cache, so a server started before an edit kept reporting its own old digest
and the page had no way to notice. Section 9.4 of the repair brief asks for the
opposite: the changed file must show up as a difference.
"""
import os
os.environ['SOLO_TEST']='1'

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.build_identity import build_identity

from test_dialogue_paging_api import MockLLM

ROOT = Path(__file__).resolve().parents[1]


def health(tmp_path):
    return TestClient(create_app(tmp_path/'h.sqlite3', MockLLM())).get('/api/health').json()


def test_health_says_whether_the_disk_moved_on(tmp_path):
    body = health(tmp_path)
    assert 'drift' in body and 'disk' in body, 'the answer must carry the comparison'
    assert isinstance(body['drift'], bool)


def test_an_unchanged_tree_reports_no_drift(tmp_path):
    body = health(tmp_path)
    assert body['drift'] is False
    assert body['disk'] == body['build']


def test_a_changed_file_is_reported_as_a_difference(tmp_path):
    """The heart of it: editing loaded code must not look like no change."""
    identity = build_identity(refresh=True)
    if identity.get('drift'):
        import pytest
        pytest.skip('the working tree already differs from the running code')
    # Only a module the process actually loaded can be compared; solo.version is
    # imported by build_identity itself, so it is always in the covered set.
    covered = identity['covered']
    name = 'solo.version' if 'solo.version' in covered else covered[0]
    target = ROOT/Path(*name.split('.')).with_suffix('.py')
    original = target.read_bytes()
    try:
        target.write_bytes(original + b'\n# drift probe\n')
        moved = build_identity(refresh=True)
        assert moved['drift'] is True, 'an edited file must show as drift'
        assert moved['disk'] != moved['build'], 'the two digests must not agree'
        assert moved['build'] == identity['build'], 'the running version did not change'
    finally:
        target.write_bytes(original)
    back = build_identity(refresh=True)
    assert back['drift'] is False, 'restoring the file must clear the difference'
