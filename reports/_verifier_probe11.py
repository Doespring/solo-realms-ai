
import os, sys, json, time, tempfile, asyncio
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from solo.app import create_app
from solo.contracts import Hero
from solo.storage import dump
from test_dialogue_paging_api import MockLLM
tmp = Path(tempfile.mkdtemp())

app = create_app(tmp/'l.sqlite3', MockLLM())
store = app.state.store
cid = store.create('K', Hero(name='M', hp=12, max_hp=12))['id']
game = app.state.game

async def main():
    # The exact write that _play does AFTER the model wait:
    #   state = read under lock BEFORE model; after model: open_settings.save / stage+apply
    current = store.campaign(cid)
    version = current['version']

    class SlowModel(MockLLM):
        async def interpret(self, *a, **k):
            await asyncio.sleep(0.15)
            return await super().interpret(*a, **k)
    game.llm = SlowModel()

    # Two different mutating paths racing for the same campaign version:
    a_started = asyncio.Event()
    async def other_turn():
        await a_started.wait()
        # a competing writer while player B is thinking
        st = store.campaign(cid)
        st['state']['turn'] = st['state']['turn'] + 1
        store.save(cid, st['state'])
    async def thinker():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            snapshot = store.campaign(cid)          # read under lock
            a_started.set()
            async with game.operation_without(cid):
                await game.llm.interpret('x', {})    # model wait, lock released
            # the OLD read is used to decide, exactly as _play does
            after = store.campaign(cid)
            return ('snapshot_version', snapshot['version'], 'now', after['version'])

    t1 = asyncio.create_task(thinker())
    t2 = asyncio.create_task(other_turn())
    print(await t1)
    await t2
    print("final version", store.campaign(cid)['version'])

    # Now the dangerous one: does _play re-check the version after the model wait?
    import inspect
    src = inspect.getsource(type(game)._play)
    after_wait = src.split('operation_without(campaign)',2)[-1][:2600]
    import re
    checks = [l.strip() for l in after_wait.splitlines() if 'current' in l and 'version' in l]
    print()
    print("version re-checks AFTER the model wait in _play:", checks)
    has_reload = 'current = self.store.campaign(campaign)' in after_wait or "current=self.store.campaign(campaign)" in after_wait
    print("does _play reload 'current' after the model wait?", has_reload)

asyncio.run(main())
