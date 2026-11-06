"""A slow engine must be given a deadline, not waited on forever.

solo/nat20_api.py passes timeout=45 to subprocess.run. The existing tests
replace subprocess.run entirely, so they never look at that value: setting
timeout=None left them green. Section 9.2 asks that a long operation fail
clearly; without a deadline it hangs instead.
"""
import os
os.environ['SOLO_TEST']='1'

import subprocess

import pytest

import solo.nat20_api as api


def test_the_worker_call_carries_a_deadline(monkeypatch):
    seen = {}

    def fake_run(invocation, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd='worker', timeout=1)

    monkeypatch.setattr(api.subprocess, 'run', fake_run)
    with pytest.raises(ValueError):
        api.Nat20GameEngine._worker({'probe': True})
    assert seen.get('timeout'), 'no deadline: a hung engine waits forever'
    assert seen['timeout'] <= 120, 'the player would wait minutes'
