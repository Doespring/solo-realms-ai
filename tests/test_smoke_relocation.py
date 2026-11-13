"""Smoke helpers are explicit QA imports, not part of ordinary solo startup."""
import os
os.environ['SOLO_TEST']='1'

import json
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[1]
NAMES=('command_smoke','desktop_cloud_smoke','desktop_creation_smoke','smoke_fixture')


def test_desktop_import_does_not_load_qa_and_explicit_helpers_need_no_pytest(tmp_path):
    code='''
import sys,json,importlib
import desktop_main
names=('command_smoke','desktop_cloud_smoke','desktop_creation_smoke','smoke_fixture')
assert not any('scripts.'+n in sys.modules for n in names)
assert not any('solo.'+n in sys.modules for n in names)
for n in names:importlib.import_module('scripts.'+n)
assert 'pytest' not in sys.modules
assert not any(n.startswith('tests.') for n in sys.modules)
print(json.dumps(dict(ordinary_import_qa=False,explicit_modules=len(names),pytest=False)))
'''
    child=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,
        env={**os.environ,'SOLO_TEST':'1','PYTHONDONTWRITEBYTECODE':'1','LOCALAPPDATA':str(tmp_path/'unused-appdata')},timeout=20)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)==dict(ordinary_import_qa=False,explicit_modules=4,pytest=False)
    assert not (tmp_path/'unused-appdata').exists()
    assert all(not (ROOT/'solo'/(name+'.py')).exists() for name in NAMES)
    assert all((ROOT/'scripts'/(name+'.py')).is_file() for name in NAMES)


def test_relocated_command_cli_help_and_pointed_package_imports(tmp_path):
    child=subprocess.run([sys.executable,'-B','-m','scripts.command_smoke','--help'],cwd=ROOT,
        capture_output=True,text=True,env={**os.environ,'SOLO_TEST':'1','PYTHONDONTWRITEBYTECODE':'1'},timeout=20)
    assert child.returncode==0 and '--output' in child.stdout,child.stderr
    spec=(ROOT/'packaging/SoloRealmsAI.spec').read_text(encoding='utf8')
    assert all('scripts.'+name in spec for name in NAMES if name!='command_smoke')
    assert "collect_submodules('scripts')" not in spec
    assert "excludes=['pytest'" in spec
