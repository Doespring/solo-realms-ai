
import os, sys, asyncio, tempfile, time
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
from solo.app import create_app
from solo.llm import MockLLM

async def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app(tmp/'y.sqlite3', MockLLM())
    game = app.state.game
    lock = game.operation
    t0 = time.perf_counter()
    ev = []
    def stamp(*a): ev.append((round(time.perf_counter()-t0, 3), *a))
    orig_acq, orig_rel = lock.acquire, lock.release
    async def acq(*a, **k):
        stamp('acquire:wait', asyncio.current_task().get_name())
        r = await orig_acq(*a, **k)
        stamp('acquire:got', asyncio.current_task().get_name(), 'locked', lock.locked())
        return r
    def rel(*a, **k):
        stamp('release', asyncio.current_task().get_name())
        return orig_rel(*a, **k)
    lock.acquire, lock.release = acq, rel

    async def survivor():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            stamp('A:inside')
            async with game.operation_without('c'):
                await asyncio.sleep(0.5)
            stamp('A:after-op_without', 'locked', lock.locked())
            await asyncio.sleep(0.2)
    async def bystander():
        await asyncio.sleep(0.05)
        stamp('B:about-to-acquire')
        async with game.operation:
            stamp('B:inside')
            game._operation_owner = asyncio.current_task()
            await asyncio.sleep(0.15)
            stamp('B:leaving')

    await asyncio.gather(survivor(), bystander())
    for e in ev: print("   ", e)
    print("final locked:", lock.locked(), "owner None:", game._operation_owner is None)

asyncio.run(main())