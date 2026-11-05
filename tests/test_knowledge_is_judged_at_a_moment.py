"""Knowledge is judged as of a moment, not as of now."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.memory import Memory
from solo.rulesets import BUILTINS
from solo.storage import Store


def test_a_later_lesson_is_not_known_earlier(tmp_path):
    store = Store(tmp_path / "knows.sqlite3")
    memory = Memory(store)
    cid = store.create("Время", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(cid, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    first = store.history(cid)[-1]["id"]
    store.record_scenario(cid, "walk-2", "Дальше",
                          lambda state: [dict(type="scripted", text="Темно.")])
    second = store.history(cid)[-1]["id"]
    with store.connection(True) as db:
        hero = memory.entity(db, cid, "hero", "Герой")
        record = memory.put(db, campaign=cid, event=second, key="k-late",
                            kind="fact", provenance="scenario", truth="fact",
                            scope="public", location="", importance=3,
                            minutes=0, text="Поздняя весть",
                            recipients=(hero,))
    earlier = store.event(first)["seq"]
    assert memory.knows(cid, hero, record, as_of=earlier) is False, (
        "the hero knew a lesson recorded after the moment")
    assert memory.knows(cid, hero, record) is True