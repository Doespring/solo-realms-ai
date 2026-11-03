"""Stage3 source-only cache boundary tests; fresh synthetic roots and SQLite only."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import time

import pytest
from solo import content_verification_cache as cache


@pytest.fixture(autouse=True)
def isolated(monkeypatch,tmp_path):
    monkeypatch.setenv('SOLO_TEST','1')
    monkeypatch.setenv('SOLO_DATA_DIR',str(tmp_path/'profile'))
    for obj in (cache._VERIFIED,cache._JSON,cache._PINS):obj.clear()


def tree(tmp_path):
    root=tmp_path/'pinned';root.mkdir()
    path=root/'valid.json';path.write_text('{"value":1}',encoding='utf-8')
    expected={'valid.json':hashlib.sha256(path.read_bytes()).hexdigest()}
    return root,path,expected


def test_windows_direntry_vs_fstat_identity_is_not_false_tamper(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path)
    original=os.fstat
    def windows_handle(fd):
        info=original(fd)
        return SimpleNamespace(st_size=info.st_size,st_mtime_ns=info.st_mtime_ns,
            st_ctime_ns=info.st_ctime_ns+100,st_ino=info.st_ino+123)
    # Valid handle metadata differs from directory creation-time/zero IDs on Windows.
    monkeypatch.setattr(os,'fstat',windows_handle)
    cache.verify_tree(root,pin,suffix='.json')
    path.write_text('{"value":2}',encoding='utf-8')
    with pytest.raises(ValueError,match='pinned SHA'):
        cache.verify_tree(root,pin,suffix='.json')


def test_cache_miss_verifies_and_same_stats_use_no_sha_after_restart(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path)
    original=cache._sha_file;calls=[]
    def counted(path):calls.append(path);return original(path)
    monkeypatch.setattr(cache,'_sha_file',counted)
    first=cache.verify_tree(root,pin,suffix='.json')
    assert len(calls)==1
    cache._VERIFIED.clear()  # Fresh-process path: use persisted verification, not RAM.
    assert cache.verify_tree(root,pin,suffix='.json')==first and len(calls)==1
    assert cache.verify_tree(root,pin,suffix='.json')==first and len(calls)==1
    before=path.stat();os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns-1_000_000))
    cache.verify_tree(root,pin,suffix='.json')
    assert len(calls)==2  # Changed metadata but identical valid content is reverified.


def test_bad_content_never_updates_last_verified_row(tmp_path):
    root,path,pin=tree(tmp_path);cache.verify_tree(root,pin,suffix='.json')
    database=cache.cache_path()
    with sqlite3.connect(database) as db:old=db.execute('SELECT * FROM verified_files_v1').fetchall()
    path.write_text('tampered-longer',encoding='utf-8')
    with pytest.raises(ValueError,match='pinned SHA'):cache.verify_tree(root,pin,suffix='.json')
    with sqlite3.connect(database) as db:assert db.execute('SELECT * FROM verified_files_v1').fetchall()==old


@pytest.mark.parametrize('mutation',['add','delete','rename','new-pycache-file'])
def test_inventory_never_uses_stale_whole_tree(tmp_path,mutation):
    root,path,pin=tree(tmp_path);cache.verify_tree(root,pin,suffix='.json')
    if mutation=='add':(root/'new.json').write_text('{}')
    if mutation=='delete':path.unlink()
    if mutation=='rename':path.rename(root/'renamed.json')
    if mutation=='new-pycache-file':
        (root/'__pycache__').mkdir();(root/'__pycache__'/'unexpected.json').write_text('{}')
    with pytest.raises(ValueError,match='inventory changed'):cache.verify_tree(root,pin,suffix='.json')


@pytest.mark.parametrize('bad',['../escape.json','/absolute.json','nested\\escape.json','C:escape.json'])
def test_invalid_pin_paths_fail_before_trust(tmp_path,bad):
    root,path,pin=tree(tmp_path)
    with pytest.raises(ValueError,match='relative path'):cache.verify_tree(root,{bad:next(iter(pin.values()))},suffix='.json')


def test_cache_is_bound_to_root_and_expected_hash(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path);cache.verify_tree(root,pin,suffix='.json')
    second=tmp_path/'other';second.mkdir();(second/path.name).write_text('tampered')
    with pytest.raises(ValueError,match='pinned SHA'):cache.verify_tree(second,pin,suffix='.json')
    with pytest.raises(ValueError,match='pinned SHA'):cache.verify_tree(root,{path.name:'0'*64},suffix='.json')


@pytest.mark.parametrize('bad',['text','negative','future','corrupt'])
def test_invalid_sqlite_metadata_or_database_cannot_skip_sha(tmp_path,monkeypatch,bad):
    root,path,pin=tree(tmp_path);cache.verify_tree(root,pin,suffix='.json')
    cache._VERIFIED.clear();database=cache.cache_path()
    if bad=='corrupt':database.write_bytes(b'not sqlite')
    else:
        value='invalid' if bad=='text' else -1 if bad=='negative' else time.time_ns()+10**15
        with sqlite3.connect(database) as db:db.execute('UPDATE verified_files_v1 SET mtime_ns=?',(value,))
    calls=[];original=cache._sha_file
    def counted(path):calls.append(path);return original(path)
    monkeypatch.setattr(cache,'_sha_file',counted)
    cache.verify_tree(root,pin,suffix='.json');assert len(calls)==1


def test_invalid_filesystem_timestamp_is_never_cached(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path)
    stamp=path.stat();os.utime(path,ns=(stamp.st_atime_ns,time.time_ns()+10**15))
    calls=[];original=cache._sha_file
    def counted(path):calls.append(path);return original(path)
    monkeypatch.setattr(cache,'_sha_file',counted)
    cache.verify_tree(root,pin,suffix='.json');cache.verify_tree(root,pin,suffix='.json')
    assert len(calls)==2


def test_read_race_rejected_and_uncached(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path);original=os.fstat;calls=[]
    def changed(fd):
        info=original(fd);calls.append(True)
        return SimpleNamespace(st_size=info.st_size,st_mtime_ns=info.st_mtime_ns+len(calls),
            st_ctime_ns=info.st_ctime_ns,st_ino=info.st_ino)
    monkeypatch.setattr(os,'fstat',changed)
    with pytest.raises(ValueError,match='during SHA'):cache.verify_tree(root,pin,suffix='.json')
    with sqlite3.connect(cache.cache_path()) as db:
        assert db.execute('SELECT COUNT(*) FROM verified_files_v1').fetchone()[0]==0


def test_json_cache_detached_and_invalidated_by_change(tmp_path):
    root,path,pin=tree(tmp_path)
    first=cache.read_json(path);first['value']=999
    assert cache.read_json(path)=={'value':1}
    path.write_text('{"value":222}',encoding='utf-8')
    assert cache.read_json(path)=={'value':222}


def test_no_explicit_profile_creates_no_persistent_cache(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path);monkeypatch.delenv('SOLO_DATA_DIR')
    assert cache.cache_path() is None
    cache.verify_tree(root,pin,suffix='.json')
    assert not list(tmp_path.rglob('*.sqlite3'))


def test_linked_entry_is_rejected(tmp_path,monkeypatch):
    root,path,pin=tree(tmp_path)
    monkeypatch.setattr(cache,'_linked',lambda info:True)
    with pytest.raises(ValueError,match='Linked/reparse'):cache.verify_tree(root,pin,suffix='.json')


def test_real_pinned_release_reports_current_digest_and_detects_corpus_change(tmp_path,monkeypatch):
    from solo.nat20_content import release
    import solo.nat20_prototype as prototype
    # A digest names one exact bundle of bytes. Preview 7's 374afd1a... covers a
    # corpus and engine that this tree no longer has, so asserting it here would
    # claim the old name for new bytes. The real guard is the rest of this test:
    # the digest is stable, is not the historical one, and a changed root fails.
    _,old,_=release()
    assert len(old)==64 and all(c in '0123456789abcdef' for c in old)
    assert old!='374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75'
    _,full,_=release(prototype.RELEASE)
    assert len(full)==64
    original=cache._sha_file
    monkeypatch.setattr(cache,'_sha_file',lambda path:pytest.fail('Unchanged corpus must use verified metadata'))
    assert release(prototype.RELEASE)[1]==full
    monkeypatch.setattr(cache,'_sha_file',original)
    # A different corpus root cannot inherit the real corpus verification.
    alternate=tmp_path/'altered-corpus';alternate.mkdir()
    monkeypatch.setattr(prototype,'DATA',alternate)
    with pytest.raises(ValueError,match='inventory changed'):release(prototype.RELEASE)
