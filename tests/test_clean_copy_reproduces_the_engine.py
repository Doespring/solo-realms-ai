"""A clean copy must be able to reproduce Nat20, not just Solo's own modules.

The audit (reports/INDEPENDENT_PROJECT_AUDIT-2026-10-23.md, section 9.1) found
that of the vendored backend only PATCHES.json was tracked: a fresh clone got
Solo without the engine it calls. The mechanism chosen here keeps the sources in
history and keeps their provenance checkable, so a copy can be verified rather
than trusted.

This test is the check a clean copy runs: every vendored file that differs from
the pinned upstream commit is a declared patch with a pinned hash, and the
dependency list that names the engine is itself in history.
"""
import os
os.environ['SOLO_TEST']='1'

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'nat20'


def _tracked(path):
    out = subprocess.run(['git', 'ls-files', '--error-unmatch', str(path)], cwd=ROOT,
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return out.returncode == 0


def test_the_engine_sources_are_in_history():
    assert _tracked('vendor/nat20/UPSTREAM.json')
    assert _tracked('vendor/nat20/packages/dnd5e-engine/pyproject.toml')
    assert _tracked('requirements-nat20.txt'), 'the dependency list names the engine'


def test_every_divergence_from_upstream_is_a_declared_patch():
    """Правки бывают двух родов, и оба обязаны быть заявлены.

    `PATCHES.json` принимает только `.py` — это правки КОДА. Исправленная
    запись КОРПУСА объявляется в `CORPUS.json:corrections` с причиной и
    собственным отпечатком. Тест читал только `PATCHES.json`, поэтому любая
    объявленная правка данных выглядела как незаявленная: fireball.json и
    phantasmal-killer.json попадали в провал при полностью корректном учёте.
    """
    upstream = json.loads((VENDOR / 'UPSTREAM.json').read_text(encoding='utf-8'))
    patches = json.loads((VENDOR / 'PATCHES.json').read_text(encoding='utf-8'))
    declared = {item['path']: item for item in patches['files']}
    corrections = json.loads((VENDOR / 'CORPUS.json').read_text(encoding='utf-8')).get('corrections', {})
    for record in corrections.values():
        declared[record['path']] = record
    divergent = []
    for rel, want in upstream['files'].items():
        target = VENDOR / rel
        if not target.is_file():
            divergent.append(rel)
            continue
        if hashlib.sha256(target.read_bytes()).hexdigest() != want:
            divergent.append(rel)
    undeclared = [rel for rel in divergent if rel not in declared]
    assert not undeclared, 'files changed without a declared patch: %r' % (undeclared,)
    # Объявленная правка данных обязана называть причину и совпадать с байтами.
    for record in corrections.values():
        assert record.get('reason'), record['path']
        assert hashlib.sha256((VENDOR / record['path']).read_bytes()).hexdigest() == record['sha256'], record['path']

def test_each_declared_patch_matches_its_pinned_hash():
    patches = json.loads((VENDOR / 'PATCHES.json').read_text(encoding='utf-8'))
    for item in patches['files']:
        target = VENDOR / item['path']
        assert target.is_file(), item['path']
        if item.get('kind') == 'added':
            continue
        got = hashlib.sha256(target.read_bytes()).hexdigest()
        assert got == item['sha256'], 'patch content drifted: %s' % item['path']


def test_upstream_identity_is_pinned_not_described():
    upstream = json.loads((VENDOR / 'UPSTREAM.json').read_text(encoding='utf-8'))
    assert len(upstream['commit']) == 40, 'a commit, not a branch'
    assert upstream['version']
    assert upstream['files'], 'individual file hashes make a copy verifiable'
