"""The engine worker must run this copy's code, not a foreign one.

solo/nat20_api.py:129-134 builds the worker environment and removes PYTHONPATH
and PYTHONSAFEPATH: an inherited PYTHONPATH pointing at another checkout would
let 'python -m solo.nat20_worker' import that copy's package, so the application
would run code from a folder it does not own. Both removals were untested:
deleting either one left every copy test green.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

import solo.nat20_api as api


class Stop(Exception):
    """Raised by the recorder to leave _worker after the environment is built."""


def _worker_environment(monkeypatch, extra):
    seen = {}

    def fake_run(invocation, **kwargs):
        seen.update(kwargs.get('env') or {})
        seen['__invocation__'] = invocation
        seen['__cwd__'] = str(kwargs.get('cwd'))
        raise Stop()

    monkeypatch.setattr(api.subprocess, 'run', fake_run)
    for name, value in extra.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(Stop):
        api.Nat20GameEngine._worker({'probe': True})
    return seen


def test_a_foreign_pythonpath_is_not_handed_to_the_worker(monkeypatch):
    env = _worker_environment(monkeypatch, {'PYTHONPATH': 'C:\\other\\checkout'})
    assert 'PYTHONPATH' not in env, 'the worker could import a foreign copy'
    assert env.get('SOLO_TEST') == '1', 'the worker keeps its offline marker'


def test_an_inherited_safepath_is_not_handed_to_the_worker(monkeypatch):
    env = _worker_environment(monkeypatch, {'PYTHONSAFEPATH': '1'})
    assert 'PYTHONSAFEPATH' not in env, 'the worker could shadow a real module'


def test_the_worker_runs_from_the_project_root(monkeypatch):
    env = _worker_environment(monkeypatch, {})
    assert env['__cwd__'] == str(api.ROOT), 'the worker must start in this project'
