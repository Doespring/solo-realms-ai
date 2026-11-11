"""The vendored engine must be the reviewed revision, not whatever is on disk.

solo/nat20_content.py:101 compares UPSTREAM.json's commit with the reviewed one
and refuses anything else. Replacing the raise with pass left 32 tests green:
they all run against the real vendor tree, whose lock names the reviewed commit,
so the refusal never fires. Section 9.1: the build must reproduce the reviewed
engine, and an unreviewed copy must stop the release instead of running.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import nat20_content


def _fake_read_json(real, mapping):
    """Answer for the named files and let everything else through."""
    def read(path):
        key = str(path).replace('\\', '/')
        for name, value in mapping.items():
            if key.endswith(name):
                return value
        return real(path)
    return read


def test_an_unreviewed_commit_stops_the_release(monkeypatch):
    real = nat20_content.read_json
    lock = real(nat20_content.VENDOR / 'UPSTREAM.json')
    monkeypatch.setattr(nat20_content, 'read_json',
                        _fake_read_json(real, {'UPSTREAM.json': dict(lock, commit='0' * 40)}))
    with pytest.raises(ValueError, match='Unreviewed Nat20 upstream revision'):
        nat20_content.release()


def test_a_foreign_package_version_stops_the_release(monkeypatch):
    monkeypatch.setattr(nat20_content, 'version', lambda package: '9.9.9')
    with pytest.raises(ValueError, match='Nat20 package version mismatch'):
        nat20_content.release()


def test_a_changed_dice_dependency_stops_the_release(monkeypatch):
    def version(package):
        return '1.1.3' if package == 'd20' else nat20_content_real_version(package)
    nat20_content_real_version = nat20_content.version
    monkeypatch.setattr(nat20_content, 'version', version)
    with pytest.raises(ValueError, match='Nat20 dice dependency changed'):
        nat20_content.release()


def _with_manifest(monkeypatch, change):
    real = nat20_content.read_json
    original = real(nat20_content.CONTENT / 'manifest.json')
    manifest = dict(original)
    change(manifest)
    monkeypatch.setattr(nat20_content, 'read_json',
                        _fake_read_json(real, {'manifest.json': manifest}))


def test_another_rules_edition_stops_the_release(monkeypatch):
    _with_manifest(monkeypatch, lambda m: m.update(rules_version='dnd2014'))
    with pytest.raises(ValueError, match='Content rules edition/release mismatch'):
        nat20_content.release()


def test_an_edited_content_document_stops_the_release(monkeypatch):
    def corrupt(manifest):
        manifest['documents'] = [dict(manifest['documents'][0], sha256='0' * 64),
                                 *manifest['documents'][1:]]
    _with_manifest(monkeypatch, corrupt)
    with pytest.raises(ValueError):
        nat20_content.release()


def test_the_reviewed_commit_is_accepted(monkeypatch):
    real = nat20_content.read_json
    lock = real(nat20_content.VENDOR / 'UPSTREAM.json')
    assert lock['commit'] == 'e187c813bb4e43c4e4e62942ecd83831f1cdbcaa'
    monkeypatch.setattr(nat20_content, 'read_json',
                        _fake_read_json(real, {'UPSTREAM.json': lock}))
    assert nat20_content.release() is not None
