"""Exercise the actual windowless EXE and refuse pending even with stand flags."""
import hashlib, json, os, pathlib, subprocess, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from solo.nat20_api import Nat20GameEngine
from solo.nat20_campaign import load_definition
from solo.game_commands import GameCommand

out = ROOT/'reports/preview8'
exe = ROOT/'dist/SoloRealmsAI/SoloRealmsAI.exe'
old = json.loads((ROOT/'tests/fixtures/nat20-preview7-fighter-combat.json').read_text(encoding='utf-8'))
state = old['campaign']['state']['nat20']
pending = json.loads((ROOT/'integration/nat20/releases/bless-self-2024-v1/cleric-bless-2024-v1.json').read_text(encoding='utf-8'))
cases = {
    'initialize_cyrillic':dict(mode='initialize',name='Проверка EXE',seed=5,conditions=[],second_wind_spent=0,
        definition=load_definition('fighter-standard-array-2024-v1').model_dump(mode='json')),
    'preview7_replay':dict(mode='verify',state=state),
    'preview7_continue':dict(mode='execute',state=state,session_id=old['campaign']['id'],
        command=GameCommand(request_id='frozen-preview8',expected_version=old['campaign']['version'],action={'kind':'end_turn'}).model_dump(mode='json')),
    'pending_environment_attack':dict(mode='initialize',name='Forbidden caster',seed=5,conditions=[],second_wind_spent=0,definition=pending),
}
results = []
for name,payload in cases.items():
    expected = None if name.startswith('pending') else Nat20GameEngine._worker(payload)
    start = time.perf_counter()
    child = subprocess.run([str(exe),'--nat20-worker'],input=json.dumps(payload,ensure_ascii=False),
        capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ,'SOLO_TEST':'1','SOLO_NAT20_BLESS_STAND':'1'})
    actual = json.loads(child.stdout)
    correct = (actual.get('ok') is False and 'activation pending' in actual.get('error','')) if expected is None else actual == expected
    results.append(dict(case=name,passed=correct,exit_code=child.returncode,elapsed_ms=round((time.perf_counter()-start)*1000,1),
        error=actual.get('error'),state_equal=actual.get('state')==expected.get('state') if expected else None))
result = dict(ok=all(r['passed'] for r in results),exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
    mock_ai=True,paid_calls=0,cases=results)
(out/'frozen-worker.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
if not result['ok']: raise SystemExit(1)
