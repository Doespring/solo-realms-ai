"""Single targeted routing gate; source only, no player profile or paid APIs."""
import os
os.environ.update(SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1')
import sys,json,hashlib,socket
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
if (OUT/'tests.xml').exists():raise SystemExit('Targeted gate already recorded')
owned=['solo/command_engine.py','solo/command_backend.py','solo/nat20_api.py',
       'tests/test_nat20_common_routing.py','tests/test_preview7_acceptance.py','tests/test_nat20_command_cycle.py']
(OUT/'tested-source-sha256.json').write_text(json.dumps({n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in owned},indent=2))
connect=socket.socket.connect
def local(sock,address):
    if isinstance(address,tuple) and address[0] not in ('localhost','127.0.0.1','::1'):raise AssertionError('No external network')
    return connect(sock,address)
socket.socket.connect=local
import pytest
targets=['tests/test_nat20_common_routing.py',
    'tests/test_preview7_acceptance.py::test_zero_hp_advances_native_death_saves_and_keeps_checkpoint',
    'tests/test_nat20_command_cycle.py::test_native_undo_abandoned_replay_and_backend_guard',
    'tests/test_nat20_command_cycle.py::test_http_native_cycle_save_new_process_continue_and_memory',
    'tests/test_command_cycle.py::test_memory_projection_failure_rolls_back_command_state_and_journal',
    'tests/test_command_cycle.py::test_engine_rejects_rest_in_combat_and_early_finish']
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp='+str(OUT/'tmp-targeted'),
    '--junitxml='+str(OUT/'tests.xml'),*targets]))
