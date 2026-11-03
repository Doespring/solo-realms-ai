"""A worker that runs out of time must fail in the player's language.

Nat20GameEngine._worker calls subprocess.run(..., timeout=45). When the engine
hangs, the timeout escaped as TimeoutExpired and its message ("Command 'worker'
timed out after 45 seconds") reached the caller untouched - English, and about a
process the player never started. Section 9.2 asks long operations to fail
clearly rather than hang or leak internals.
"""
import os
os.environ['SOLO_TEST']='1'

import subprocess

import pytest

import solo.nat20_api as api


def _run_with(monkeypatch, behaviour):
    def fake_run(invocation, **kwargs):
        raise behaviour()
    monkeypatch.setattr(api.subprocess, 'run', fake_run)
    return api


def test_a_timed_out_worker_speaks_russian(monkeypatch):
    _run_with(monkeypatch, lambda: subprocess.TimeoutExpired(cmd='worker', timeout=45))
    with pytest.raises(ValueError) as failed:
        api.Nat20GameEngine._worker({'probe': True})
    message = str(failed.value)
    assert 'timed out' not in message.lower(), message
    assert any('а' <= ch <= 'я' or ch in 'ёЁ' for ch in message), message


def test_a_timed_out_worker_says_nothing_changed(monkeypatch):
    _run_with(monkeypatch, lambda: subprocess.TimeoutExpired(cmd='worker', timeout=45))
    with pytest.raises(ValueError) as failed:
        api.Nat20GameEngine._worker({'probe': True})
    assert 'не измен' in str(failed.value).lower() or 'не измен' in str(failed.value), str(failed.value)
