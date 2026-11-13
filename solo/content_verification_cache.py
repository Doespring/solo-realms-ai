"""Metadata-indexed results of pinned SHA checks; not a hostile-filesystem sandbox.

No implicit profile/database. Without SOLO_DATA_DIR, or with a broken cache, verify
from bytes. Enumerate every tree on every call so additions/deletions are visible.
Changes in size/mtime_ns/ctime_ns/file identity invalidate prior verification.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import time
from collections import OrderedDict

_VERIFIED = OrderedDict()
_PINS = OrderedDict()
_JSON = OrderedDict()


def _remember(cache, key, value, limit):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:cache.popitem(last=False)


def read_json(path):
    """Stat-sensitive parse cache, not verification: callers still verify their pin."""
    path = Path(path)
    key = os.path.normcase(os.path.abspath(path))
    info = path.stat()
    stamp = _stamp(info)
    prior = _JSON.get(key)
    if stamp is not None and prior and prior[0] == stamp:
        return json.loads(prior[1])  # Detached objects: consumers cannot poison shared parse.
    raw = path.read_text(encoding='utf-8')
    parsed = json.loads(raw)
    if stamp is not None and _stamp(path.stat()) == stamp:
        _remember(_JSON, key, (stamp, raw), 4096)
    return parsed

SCHEMA = '''CREATE TABLE IF NOT EXISTS verified_files_v1(
 root TEXT NOT NULL,path TEXT NOT NULL,expected TEXT NOT NULL,size INTEGER NOT NULL,
 mtime_ns INTEGER NOT NULL,ctime_ns INTEGER NOT NULL,device TEXT NOT NULL,inode TEXT NOT NULL,
 PRIMARY KEY(root,path));'''


def cache_path():
    explicit = os.environ.get('SOLO_DATA_DIR')
    if not explicit or not Path(explicit).is_absolute():
        return None
    root = Path(explicit).resolve()
    # Never implicitly cache inside the repository's historical user data.
    protected = Path(__file__).resolve().parents[1] / 'data'
    if root == protected or protected in root.parents:
        return None
    return root / 'cache' / 'content-verification-v1.sqlite3'


def _stamp(info):
    values = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if any(type(v) is not int for v in values) or values[0] < 0:
        return None
    if any(v <= 0 or v > time.time_ns() + 5_000_000_000 for v in values[1:]):
        return None  # Invalid/future timestamps can never hit/populate metadata cache.
    return (*values, str(info.st_dev), str(info.st_ino))


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _inventory(root, suffix):
    result = {}
    def visit(directory, prefix=''):
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _linked(info):
                    raise ValueError('Linked/reparse content entry is not a pinned regular path')
                relative = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    visit(entry.path, relative + '/')
                elif entry.name.endswith(suffix):
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('Pinned content must be a regular file')
                    result[relative] = (entry.path, info)
    visit(root)
    return result


def _sha_file(path):
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
        raise ValueError('Content changed during SHA verification')
    return sha, after


def verify_tree(root, expected, *, suffix, database=None):
    """Verify the exact file set against caller-owned pin. Return a metadata token.

    SQLite contains only completed valid checks. A missing/malformed/changed row
    cannot skip SHA. Caller must still validate manifest edition/provenance/version.
    database=None chooses explicit profile cache; no profile means no persistence.
    """
    root = Path(root).resolve(strict=True)
    pin = hashlib.sha256(json.dumps(expected,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if (pin,suffix) not in _PINS:
        for name, sha in expected.items():
            path = PurePosixPath(name)
            if (not isinstance(name,str) or not name or '\\' in name or ':' in name
                    or path.is_absolute() or '..' in path.parts or str(path) != name
                    or not name.endswith(suffix) or not isinstance(sha,str) or len(sha)!=64
                    or any(c not in '0123456789abcdef' for c in sha)):
                raise ValueError('Invalid pinned relative path or SHA')
        _remember(_PINS,(pin,suffix),True,32)
    entries = _inventory(root, suffix)
    if entries.keys() != expected.keys():
        raise ValueError('Pinned file inventory changed (added/deleted paths)')
    path = Path(database) if database is not None else cache_path()
    memory_key = (str(root), pin, str(path))
    metadata = {name:(info.st_size,info.st_mtime_ns,info.st_ctime_ns,str(info.st_dev),str(info.st_ino))
                for name,(_,info) in entries.items()}
    previous = _VERIFIED.get(memory_key)
    if previous and previous[0] == metadata:
        return previous[1]
    connection = None
    cached = {}
    try:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=2)
            connection.executescript(SCHEMA)
            cached = {r[0]:r[1:] for r in connection.execute(
                'SELECT path,expected,size,mtime_ns,ctime_ns,device,inode FROM verified_files_v1 WHERE root=?', (str(root),))}
    except (sqlite3.Error, OSError):
        if connection is not None:connection.close()
        connection = None  # Cache availability never establishes trust.
    pending = []
    stamps = []
    try:
        for name, (filename, info) in entries.items():
            stamp = _stamp(info)
            row = cached.get(name)
            valid = (stamp is not None and row is not None and row == (expected[name], *stamp)
                     and all(type(v) is int for v in row[1:4]))
            if not valid:
                actual, checked = _sha_file(filename)
                if actual != expected[name]:
                    raise ValueError('Content differs from pinned SHA: ' + name)
                # Windows DirEntry metadata uses creation-time ctime and zero file IDs,
                # unlike fstat. Compare fields with matching semantics here; _sha_file
                # separately checks full fstat identity/timestamps across the read.
                if (info.st_size,info.st_mtime_ns) != (checked.st_size,checked.st_mtime_ns):
                    raise ValueError('Content changed after tree inventory')
                if stamp is not None:pending.append((str(root),name,expected[name],*stamp))
            stamps.append((name,expected[name],stamp))
        # No partial trust updates if any file failed validation above.
        if connection is not None and pending:
            try:
                with connection:
                    connection.executemany('INSERT OR REPLACE INTO verified_files_v1 VALUES(?,?,?,?,?,?,?,?)',pending)
                    stale=set(cached)-set(entries)
                    connection.executemany('DELETE FROM verified_files_v1 WHERE root=? AND path=?',[(str(root),p) for p in stale])
            except sqlite3.Error:
                pass  # We just verified the complete tree; unavailable persistence is safe.
    finally:
        if connection is not None:connection.close()
    token = hashlib.sha256(json.dumps((str(root),sorted(stamps)),separators=(',',':'),default=str).encode()).hexdigest()
    if all(stamp is not None for _,_,stamp in stamps):
        _remember(_VERIFIED,memory_key,(metadata,token),16)
    return token
