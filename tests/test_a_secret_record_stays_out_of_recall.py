"""A secret written into memory must never come back to the hero."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.memory import Memory
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_a_secret_record_stays_out_of_recall(tmp_path):
    store = Store(tmp_path / "secret.sqlite3")
    memory = Memory(store)
    cid = store.create("Тайна", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(cid, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    event = store.history(cid)[-1]["id"]
    common = dict(campaign=cid, event=event, kind="fact",
                  provenance="scenario", truth="fact", location="",
                  importance=3, minutes=0)
    with store.connection(True) as db:
        hero = memory.entity(db, cid, "hero", "Герой")
        memory.put(db, **common, key="k-open",
                   scope="public", text="Двор пуст", recipients=(hero,))
        memory.put(db, **common, key="k-secret",
                   scope="secret", text="Ключ в нише", recipients=(hero,))
    found = memory.retrieve(cid, audience=[hero])["records"]
    texts = " ".join(str(r.get("content", "")) for r in found)
    assert "нише" not in texts, (
        "a secret record reached the hero: %r" % texts[:200])