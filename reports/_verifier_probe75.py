
import subprocess, sys, os
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
orig = T.read_bytes()
T.write_bytes(orig + b"\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n")
try:
    print('BEFORE pytest: disk has insert ->', b'sys.path.insert' in T.read_bytes())
    # Run pytest with a conftest-like hook injected via -p to print what the test sees.
    plug = ROOT/'reports'/'_probe75_plug.py'
    plug.write_text(
        "def pytest_collection_finish(session):\n"
        "    from pathlib import Path\n"
        "    T = Path(r'%s')/'solo'/'chronicles.py'\n"
        "    print('DURING pytest: disk has insert ->', 'sys.path.insert' in T.read_text(encoding='utf-8'))\n"
        "    import tests.test_copy_does_not_reach_back as m\n"
        "    print('DURING pytest: sources() n =', len(m.sources()))\n"
        "    hits=[p.name for p in m.sources() if 'sys.path.insert' in p.read_text(encoding='utf-8',errors='replace')]\n"
        "    print('DURING pytest: offenders =', hits)\n" % str(ROOT), encoding='utf-8')
    env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT)+os.pathsep+str(ROOT/'reports'), PYTHONDONTWRITEBYTECODE='1')
    r = subprocess.run([sys.executable,'-X','utf8','-B','-m','pytest',
        'tests/test_copy_does_not_reach_back.py::test_the_package_does_not_edit_sys_path',
        '-q','-p','no:cacheprovider','-p','_probe75_plug','-s'],
        capture_output=True, text=True, cwd=str(ROOT), env=env)
    print(r.stdout[-1800:]); print('ERR', r.stderr[-600:])
finally:
    T.write_bytes(orig)
print('restored', T.read_bytes()==orig)
