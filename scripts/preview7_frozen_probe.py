"""Exercise the packaged windowless Nat20 worker, not a system Python substitute."""
import json,pathlib,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from solo.nat20_api import Nat20GameEngine
from solo.nat20_campaign import load_definition
payload=dict(mode='initialize',name='Frozen probe',seed=5,conditions=[],second_wind_spent=0,
             definition=load_definition('fighter-standard-array-2024-v1').model_dump(mode='json'))
expected=Nat20GameEngine._worker(payload)
exe=ROOT/'dist/SoloRealmsAI/SoloRealmsAI.exe'
start=time.perf_counter()
process=subprocess.run([str(exe),'--nat20-worker'],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
result=dict(frozen_executable=str(exe),exit_code=process.returncode,elapsed_ms=round((time.perf_counter()-start)*1000,1),mock_ai=True,paid_calls=0)
try:
    actual=json.loads(process.stdout)
    result.update(ok=actual.get('ok') is True,state_equal=actual.get('state')==expected['state'],error=actual.get('error'))
except (ValueError,TypeError):result.update(ok=False,error='No UTF-8 JSON from frozen worker',stdout=(process.stdout or '')[:1000],stderr=(process.stderr or '')[:1000])
(ROOT/'reports/preview7/frozen-worker.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
if not result['ok'] or not result.get('state_equal'):raise SystemExit(1)
