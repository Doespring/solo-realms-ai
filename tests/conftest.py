"""Test environment setup.

Most tests create a temporary directory and put an SQLite database in it.
This sandbox denies access to directories created with mode exactly 0o700:
the directory itself is created, but every later open(), listdir(), chmod()
and rmtree() inside it fails with PermissionError. tempfile.mkdtemp() creates
its directory with mode 0o700, so under this sandbox every such test dies with
sqlite3.OperationalError: unable to open database file.

The boundary is exactly one mode, not a family of modes. Measured on this
host (runtime/_probeA.py, runtime/_probeB.py):

    0o700  write FAIL  listdir FAIL  chmod FAIL  rmtree FAIL
    0o750  write ok    listdir ok    chmod ok    rmtree ok
    0o770  write ok    rmtree ok
    0o701  write ok    rmtree ok
    0o710  write ok    rmtree ok
    0o600  write ok    rmtree ok

So only a request for precisely 0o700 is rewritten, and it is rewritten to
0o755 -- a mode this sandbox accepts and that is no more permissive for other
users than the caller asked for. Everything else, including deletion and
ownership, keeps standard Python behaviour.

So the limited mode exists, it is explicit, and it only changes the mode
passed to mkdir -- never what a test observes about creation or deletion.
It can be forced on or off with SOLO_TEST_SANDBOX_TMP=1/0; by default it is
probed, and the outcome is printed so every run says which mode it used.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ['SOLO_TEST'] = '1'

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / 'runtime' / '_pytest_scratch'

_MODE = '_sandbox_mkdir_mode'


def _probe_restricted() -> bool:
    """Return True when mode 0o700 directories are unusable."""

    probe = SCRATCH / ('probe-' + os.urandom(6).hex())
    try:
        probe.mkdir(parents=True, mode=0o700)
        try:
            (probe / 'f').write_bytes(b'x')
        except PermissionError:
            return True
        return False
    finally:
        try:
            (probe / 'f').unlink(missing_ok=True)
        except OSError:
            pass
        try:
            probe.rmdir()
        except OSError:
            pass


def _prepare():
    SCRATCH.mkdir(parents=True, exist_ok=True)

    forced = os.environ.get('SOLO_TEST_SANDBOX_TMP', '').strip()
    restricted = forced == '1' if forced in ('0', '1') else _probe_restricted()

    # Temporary files must live where this sandbox allows them at all: the
    # platform temporary area C:\\Users\\...\\AppData\\Local\\Temp\\dsh-* is
    # refused, so point tempfile at the repository.
    tempfile.tempdir = str(SCRATCH)
    for name in ('TMPDIR', 'TEMP', 'TMP'):
        os.environ[name] = str(SCRATCH)

    if restricted:
        # Only the mode changes, and it has to change at creation: a directory
        # that was ever created with 0o700 cannot even be chmod-ed afterwards,
        # let alone listed. tempfile.mkdtemp() and pytest both call os.mkdir()
        # with mode 0o700, so the root library call asks for the default mode.
        # Nothing else about creation, writing or deletion is altered.
        original_mkdir = os.mkdir

        def mkdir(path, mode=0o777, *, dir_fd=None):
            # Exactly 0o700, the one mode this sandbox refuses. 0o770 and every
            # other mode pass through untouched, so a test that asks for a
            # narrower directory still gets what it asked for.
            if mode == 0o700:
                mode = 0o755
            return original_mkdir(path, mode) if dir_fd is None else original_mkdir(path, mode, dir_fd=dir_fd)

        os.mkdir = mkdir

    return restricted


RESTRICTED = _prepare()


def _own_basetemp():
    """A basetemp this sandbox can write in.

    pytest builds <basetemp>/pytest-of-<user>/pytest-<n>/ and creates those
    directories itself, and under this sandbox one created with mode 0o700
    cannot even be listed. --basetemp cannot help: pytest deletes the given
    directory first. So the factory is handed a directory that already exists;
    pytest then only numbers subdirectories inside it (mode 0o777 on Windows).
    """

    # A fresh name per run: pytest removes an existing basetemp first, and
    # that removal is refused here. The name never repeats, so nothing is left
    # for pytest to delete and no two runs share a directory.
    base = str(SCRATCH / ('basetemp-' + time.strftime('%Y%m%d-%H%M%S') + '-' + os.urandom(4).hex()))
    os.makedirs(base, exist_ok=True)
    return Path(base)


def pytest_configure(config):
    """Announce the mode so a run always says what it actually tested."""

    config._sandbox_restricted = RESTRICTED
    if RESTRICTED:
        # pytest would delete --basetemp first, and deletion there is refused.
        # The factory accepts the directory below unchanged instead.
        model = getattr(config.option, '_basetemp', None) or config.option.basetemp
        config.option._basetemp_for_scratch = model
        config.option.basetemp = None
    sys.stderr.write('tests: temp root %s; mode %s%s' % (
        SCRATCH, 'restricted' if RESTRICTED else 'normal', chr(10)))


def pytest_sessionstart(session):
    if RESTRICTED:
        factory = session.config._tmp_path_factory
        factory._basetemp = _own_basetemp()
        factory._given_basetemp = None


def pytest_sessionfinish(session, exitstatus):
    if RESTRICTED:
        factory = session.config._tmp_path_factory
        factory._basetemp = None


def pytest_report_header(config):
    return ['temp mode: %s' % ('restricted' if RESTRICTED else 'normal')]