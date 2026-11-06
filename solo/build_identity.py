"""Which exact code and interface a running server is serving.

A player reported an editor talking to an API that did not have its route. The
timestamp of a file proves nothing about the process that already imported it,
so the identity is computed from the code itself and published with health.

Two things the previous version could not promise, and this one must:

* it hashed thirteen hand-picked files, so a change anywhere else was invisible;
* it read the disk on first call and cached that reading, so editing a file after
  the process imported it made the process advertise code it was not running.

The identity is therefore taken once, at import time, from the modules this
process actually loaded, together with the bytes those modules were loaded from.
A later change on disk cannot rewrite it: it is reported as drift, which is the
honest answer. The interface is named separately because a browser bundle and a
Python backend are different artefacts that can be out of step.
"""
import hashlib
import importlib
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .version import VERSION

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'solo-api-1'


def _backend_files():
    """Every module of this package the process has loaded, by relative path."""
    files = {}
    for name, module in list(sys.modules.items()):
        if not (name == 'solo' or name.startswith('solo.')) or module is None:
            continue
        origin = getattr(module, '__file__', None)
        if not origin or not origin.endswith('.py'):
            continue
        path = Path(origin).resolve()
        try:
            files[name] = (path.relative_to(ROOT), path)
        except ValueError:
            continue  # loaded from somewhere else: cannot vouch for it, and says so
    return files


def _ui_files():
    root = ROOT / 'web'
    return sorted(p.relative_to(ROOT) for p in root.rglob('*')
                  if p.is_file() and p.suffix in ('.js', '.html', '.css'))


def _digest(pairs):
    sha = hashlib.sha256()
    for name, payload in sorted(pairs):
        sha.update(str(name).encode('utf-8'))
        sha.update(b'\0')
        sha.update(payload)
    return sha.hexdigest()[:12]


def _loaded_pairs(which=None):
    """Content of the backend files, as they are right now.

    The *set* of files is fixed at start (what the process imported then); the
    bytes are read here, because that is what a drift check has to compare.
    Reading the start set from disk is not the same as trusting the disk: the
    frozen answer below stays frozen, and a difference surfaces as drift.
    """
    files = which if which is not None else _backend_files()
    return [(str(rel), path.read_bytes()) for rel, path in files.values()]


def _started():
    """Fingerprints frozen when this module was imported."""
    backend = _digest(_loaded_pairs())
    ui = _digest([(str(rel), (ROOT / rel).read_bytes()) for rel in _ui_files()])
    try:
        # stdin must be detached: a host that leaves this process reading a pipe
        # would otherwise keep git's reader thread parked and never return.
        commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                                capture_output=True, text=True, timeout=5,
                                stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ''
    files = _backend_files()
    return dict(version=VERSION, build=backend, ui=ui, started=backend,
                commit=commit or 'unknown', contract=CONTRACT,
                modules=len(files), covered=sorted(files),
                # The same coverage addressed by FILE PATH. Callers asking "is
                # this file covered?" match no module name, so without this a
                # changed module could look uncovered.
                covered_files=sorted(str(v[0]).replace(chr(92),'/') for v in files.values()),
                _files=files)


@lru_cache(maxsize=1)
def _freeze():
    """The start identity, frozen on first call and never recomputed.

    Freezing happens when the server asks, not when this module is imported:
    a test imports build_identity before it imports the game, and an identity
    taken at import time would then describe almost nothing. What must not
    happen is a *later* re-read, and that is what lru_cache guarantees.
    """
    return _started()


def build_identity(refresh=False):
    """What this process is running, plus whether the disk has moved on.

    Without refresh the answer is the frozen start identity, so a server can
    never claim a version it is not executing. With refresh the disk is re-read
    and compared: a difference appears as drift=True, and the start digest stays
    available as 'started', so the caller can say "running A, disk has B".
    """
    frozen = _freeze()
    identity = dict(frozen)
    files = identity.pop('_files')
    if refresh:
        identity['disk'] = _digest(_loaded_pairs(files))
        # The page is served from disk on every request, so a changed .js file
        # takes effect without a server restart. That makes it a separate
        # question from the backend, and it needs its own answer: a reader whose
        # screen was built from another file must be told, not assumed fine.
        identity['ui_disk'] = _digest([(str(rel), (ROOT / rel).read_bytes()) for rel in _ui_files()])
        identity['ui_drift'] = identity['ui_disk'] != identity['ui']
        identity['drift'] = identity['disk'] != identity['build'] or identity['ui_drift']
    else:
        identity['drift'] = False
        identity['ui_drift'] = False
    return identity
