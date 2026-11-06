"""A file changed on disk must show up as drift in the build identity.

solo/build_identity.py:131 sets drift from a comparison of the frozen build
digest with a re-read of disk. Replacing it with drift=False left 6 tests green:
they call build_identity() without refresh, where drift is False by design, so
the comparison itself was never exercised. Section 9.4 asks that the product
name what it is actually running.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

from solo import build_identity as identity_module


def test_a_changed_file_is_reported_as_drift(tmp_path, monkeypatch):
    target = tmp_path / 'piece.py'
    target.write_bytes(b'v1')
    files = {'piece': (Path('piece.py'), target)}

    monkeypatch.setattr(identity_module, '_freeze',
                        lambda: {'build': 'a', 'disk': 'a', 'ui': 'u',
                                 '_files': files})
    monkeypatch.setattr(identity_module, '_digest',
                        lambda pairs: 'a' if any(b'v1' in body for _name, body in pairs) else 'b')
    monkeypatch.setattr(identity_module, '_ui_files', lambda: [])

    target.write_bytes(b'v2')
    seen = identity_module.build_identity(refresh=True)
    assert seen['drift'] is True, 'disk changed but the build claims to match'


def test_an_unchanged_tree_is_not_drift(tmp_path, monkeypatch):
    target = tmp_path / 'piece.py'
    target.write_bytes(b'v1')
    files = {'piece': (Path('piece.py'), target)}

    monkeypatch.setattr(identity_module, '_freeze',
                        lambda: {'build': 'a', 'disk': 'a', 'ui': 'u',
                                 '_files': files})
    monkeypatch.setattr(identity_module, '_digest', lambda pairs: 'a')
    # ui_files empty -> ui_disk is the digest of nothing, so 'ui' must equal it
    monkeypatch.setattr(identity_module, '_ui_files', lambda: [])
    monkeypatch.setattr(identity_module, '_freeze',
                        lambda: {'build': 'a', 'disk': 'a', 'ui': 'u',
                                 '_files': files})
    monkeypatch.setattr(identity_module, '_digest',
                        lambda pairs: 'u' if not pairs else 'a')

    seen = identity_module.build_identity(refresh=True)
    assert seen['drift'] is False, 'nothing changed, yet drift is claimed'
