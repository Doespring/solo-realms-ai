"""Stop only this project's exact uvicorn command, including a venv redirector child."""
from pathlib import Path
import sys,psutil
ROOT=Path(__file__).resolve().parents[1]
EXPECTED=['-m','uvicorn','solo.app:app','--host','127.0.0.1','--port','8000']
def owned(process):
    try:return Path(process.cwd()).resolve()==ROOT and process.cmdline()[1:]==EXPECTED
    except (psutil.NoSuchProcess,psutil.AccessDenied):return False
def main():
    if any(name!='app' for name in sys.argv[1:]):raise SystemExit('Only app is supported')
    candidates=[p for p in psutil.process_iter() if owned(p)]
    for process in candidates:
        try:process.terminate()
        except psutil.NoSuchProcess:pass
    gone,alive=psutil.wait_procs(candidates,timeout=10)
    for process in alive:
        if owned(process):process.kill()
    (ROOT/'data/app.pid').unlink(missing_ok=True)
    print('app: stopped.' if candidates else 'app: owned server not found.')
if __name__=='__main__':main()
