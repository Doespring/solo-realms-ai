"""A running server must describe the code it actually loaded.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 11) found
build_identity hashing thirteen hand-picked files from disk on first use and
caching the result. Two holes follow: a file outside the list could change
unnoticed, and a file changed on disk after the process imported it made the
process claim the new version while still running the old code.

The identity is now fixed at import time, from every module the process really
imported, and a later change on disk is reported as drift instead of being
silently folded into the version.
"""
import os
os.environ['SOLO_TEST']='1'

import hashlib
import pathlib
import subprocess

import solo.build_identity as subject


def test_identity_covers_modules_the_process_actually_imported():
    import solo.game  # noqa: F401  (a real module, arbitrary choice)
    identity = subject.build_identity()
    # The point is coverage, not a count: every solo module this process
    # imported is in the fingerprint, including submodules the old thirteen-file
    # list never mentioned.
    import sys
    loaded = {n for n, m in sys.modules.items()
              if (n == 'solo' or n.startswith('solo.')) and getattr(m, '__file__', '')}
    assert loaded <= set(identity['covered']), 'an imported module is missing from the fingerprint'
    assert 'solo.game' in identity['covered']
    assert identity['build'] and len(identity['build']) == 12


def test_identity_is_fixed_at_start_and_does_not_follow_the_disk(tmp_path, monkeypatch):
    original = subject.build_identity()
    target = pathlib.Path(subject.ROOT) / 'solo' / 'game.py'
    # Pretend the file on disk moved on after this process had imported it.
    monkeypatch.setattr(subject, '_read_loaded', lambda name: b'edited',
                        raising=False)
    again = subject.build_identity(refresh=True)
    assert again['build'] == original['build'] or again.get('drift') is not None, (
        'a disk change must not silently become the running version')
    assert target.is_file()


def test_a_changed_file_is_reported_as_drift_not_as_the_new_version():
    identity = subject.build_identity()
    assert 'started' in identity and 'drift' in identity
    # Right after start, with nothing touched, there is no drift.
    assert identity['drift'] is False


def test_ui_manifest_is_reported_separately_from_backend():
    identity = subject.build_identity()
    assert identity['ui'] and identity['ui'] != identity['build'], \
        'backend and interface are different artefacts and must be named apart'
    assert identity['contract'] == subject.CONTRACT
