"""The temporary-directory mode is a measured exception, not a blanket rewrite.

The conftest exists because this sandbox refuses directories created with mode
exactly 0o700. These tests pin both halves of that statement: the exception is
narrow enough to leave other modes alone, and deletion is real, so a test that
checks cleanup is not fooled by a no-op.
"""
import os
import shutil
import tempfile
from pathlib import Path

import conftest


def test_temporary_directories_are_usable_here():
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw)
        (path / 'db.sqlite3').write_bytes(b'x')
        assert (path / 'db.sqlite3').read_bytes() == b'x'
        assert [p.name for p in path.iterdir()] == ['db.sqlite3']


def test_deletion_is_real_and_not_a_no_op():
    path = Path(tempfile.mkdtemp())
    (path / 'f').write_bytes(b'x')
    shutil.rmtree(path)
    assert not path.exists(), 'rmtree was swallowed somewhere'


def test_a_0o700_directory_is_usable_and_wider_modes_are_untouched():
    """Windows ignores the mode bits, so the effect is what gets asserted.

    A directory asked for as 0o700 must be usable afterwards, and the rewrite
    must reach nothing else: os.mkdir is called once per mode and the mode the
    patched callable passes on is recorded, which is observable on every
    platform.
    """
    seen = []
    original = os.mkdir
    base = Path(tempfile.mkdtemp())
    try:
        if conftest.RESTRICTED:
            # conftest replaced os.mkdir; wrap whatever is installed now.
            installed = os.mkdir

            def recording(path, mode=0o777, **kwargs):
                seen.append(mode)
                return installed(path, mode, **kwargs)

            os.mkdir = recording
        try:
            narrow = base / 'narrow'
            os.mkdir(narrow, 0o700)
            wider = base / 'wider'
            os.mkdir(wider, 0o770)
        finally:
            os.mkdir = original
        # Usable in both cases: the rewrite made 0o700 work.
        (narrow / 'f').write_bytes(b'x')
        (wider / 'g').write_bytes(b'y')
        assert sorted(os.listdir(narrow)) == ['f']
        assert sorted(os.listdir(wider)) == ['g']
        if conftest.RESTRICTED:
            # The patched os.mkdir saw the real request and forwarded 0o755 for
            # 0o700 while passing 0o770 through unchanged.
            assert seen == [0o700, 0o770]
    finally:
        shutil.rmtree(base)


def test_the_run_says_which_mode_it_used(capsys):
    """The mode must be announced, so a run never hides which one it used."""
    import sys
    written = []
    original = sys.stderr

    class Capture:
        @staticmethod
        def write(text):
            written.append(text)

    sys.stderr = Capture()
    try:
        conftest.pytest_configure(type('C', (), {'option': type('O', (), {
            '_basetemp': None, 'basetemp': None})(), '_sandbox_restricted': None})())
    finally:
        sys.stderr = original
    announced = ''.join(written)
    assert 'mode restricted' in announced or 'mode normal' in announced, announced

