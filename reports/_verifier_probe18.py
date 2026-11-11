
import os, sys, asyncio, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM

async def main():
    tmp = Path(tempfile.mkdtemp())
    app = create_app(tmp/'t.sqlite3', MockLLM())
    game = app.state.game
    obs = []

    async def bystander(tag):
        # This is exactly what /api/health-adjacent read routes and a second
        # campaign's turn do: 'async with game.operation' from a task that does
        # NOT own it.
        await asyncio.sleep(0.05)
        try:
            async with game.operation:
                obs.append((tag, 'acquired'))
                await asyncio.sleep(0.01)
            obs.append((tag, 'corrupted-owner', game._operation_owner))
        except RuntimeError as e:
            obs.append((tag, 'RuntimeError', str(e)))

    async def holder():
        async with game.operation:
            game._operation_owner = asyncio.current_task()
            async with game.operation_without('c1'):
                await asyncio.sleep(0.3)

    await asyncio.gather(holder(), bystander('B1'))

    # After the holder finishes, does the lock really belong to nobody?
    unlocked = not game.operation.locked()
    print("operation.locked() after the sequence:", game.operation.locked())
    print("bystander observations:", obs)
    # Now the decisive test: can a NON-owner release the lock again?
    async def wrong_release():
        game._operation_owner = asyncio.current_task()   # what game.py states now
        game.operation.release()
    try:
        await wrong_release()
        print("WRONG RELEASE SUCCEEDED -> lock now locked():", game.operation.locked())
    except RuntimeError as e:
        print("wrong release refused:", e)

asyncio.run(main())
