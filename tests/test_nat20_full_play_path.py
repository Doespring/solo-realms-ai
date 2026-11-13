"""Полный игровой путь через штатный хост-воркер.

Закрепляет то, что проверялось вручную в DELIVERY.md 14.117-14.118:
инициализация персонажа, начало боя, досягаемость, атака, победа,
награда, реплей-сверка и способность второго дыхания.

Запуск:
    set PYTHONUTF8=1
    set SOLO_TEST=1
    <VENV>\Scripts\python.exe -m pytest tests/test_nat20_full_play_path.py
"""
import asyncio
import os
import sys

import pytest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_root, "vendor", "nat20", "packages", "dnd5e-engine", "src"))
sys.path.insert(0, os.path.join(_root, "vendor", "nat20", "packages", "dnd5e-srd-data", "src"))
sys.path.insert(0, _root)

from solo.nat20_campaign import load_definition  # noqa: E402
from solo.nat20_worker import execute  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_library_loader():
    """Не давать этому файлу оставлять свой загрузчик соседним тестам.

    `solo.nat20_content.release()` устанавливает прод-загрузчик в глобальный
    синглтон `_LIB_LOADER`, где всего три предмета и нет классов. Без сброса
    такие тесты, как tests/test_nat20_ac_formula.py (ждут полный корпус, в том
    числе класс 'wizard'), в общем прогоне падают с
    `ValueError: unknown class: 'wizard'`.
    """
    from dnd5e_engine.lib_loader import set_lib_loader_for_tests
    set_lib_loader_for_tests(None)
    yield
    set_lib_loader_for_tests(None)


def _run(steps):
    return asyncio.run(steps())


class _Driver:
    def __init__(self, state):
        self.state = state
        self.n = 0

    async def cmd(self, kind, **kwargs):
        self.n += 1
        out = await execute({
            "mode": "execute",
            "state": self.state,
            "session_id": "full-path",
            "command": {
                "request_id": "req-%03d" % self.n,
                "expected_version": 0,
                "action": dict(kind=kind, **kwargs),
            },
        })
        self.state = out["state"]
        return out

    def combat(self):
        return self.state["combat"]["view"]

    def hero_turn(self):
        view = self.combat()
        return view["initiative"][view["current_turn_index"]]["entity_id"] == "hero"

    async def wait_hero(self, limit=6):
        for _ in range(limit):
            if self.hero_turn():
                return True
            await self.cmd("end_turn")
        return self.hero_turn()


async def _start(seed=3):
    state = (await execute({
        "mode": "initialize",
        "name": "Герой",
        "seed": seed,
        "definition": load_definition().model_dump(mode="json"),
    }))["state"]
    driver = _Driver(state)
    await driver.cmd("start_combat", encounter_id="basic")
    return driver


def test_hero_sheet_matches_rules():
    """AC 18 и 12 HP собираются из снаряжения, а не задаются константой."""
    async def steps():
        state = (await execute({
            "mode": "initialize",
            "name": "Герой",
            "seed": 3,
            "definition": load_definition().model_dump(mode="json"),
        }))["state"]
        hero = state["hero"]
        assert hero["ac"] == 18, "кольчуга 16 + щит 2"
        assert hero["hp_max"] == 12
        assert hero["equipment"][:3] == ["longsword", "chain-mail", "shield"]
    _run(steps)


def test_move_spends_movement_and_attack_works():
    """Перемещение тратит движение, атака по врагу разрешается."""
    async def steps():
        driver = await _start()
        assert await driver.wait_hero()
        hero = next(a for a in driver.combat()["initiative"] if a["entity_id"] == "hero")
        before = hero["movement_remaining"]
        await driver.cmd("move", position={"x": 3, "y": 1, "z": 0})
        after = next(a for a in driver.combat()["initiative"] if a["entity_id"] == "hero")
        assert after["movement_remaining"] < before, "перемещение должно тратить движение"
        assert after["action_available"], "перемещение не тратит действие"
        await driver.cmd("attack", target="goblin")
    _run(steps)


def test_full_battle_reaches_victory_with_reward():
    """Бой до победы: смерть врага, награда, закрытие боя."""
    async def steps():
        driver = await _start(seed=3)
        for _ in range(30):
            view = driver.combat()
            if view.get("ended") or "goblin" in (view.get("dead_ids") or []):
                break
            if not driver.hero_turn():
                await driver.cmd("end_turn")
                continue
            hero = next(a for a in view["initiative"] if a["entity_id"] == "hero")
            if not hero["action_available"]:
                await driver.cmd("end_turn")
                continue
            await driver.cmd("attack", target="goblin")
        view = driver.combat()
        assert "goblin" in view["dead_ids"], "гоблин должен пасть: %r" % (view["tracked_hp"],)
        assert view["tracked_hp"]["hero"] > 0
        out = await driver.cmd("end_combat")
        result = out.get("result") or {}
        outcome = result.get("outcome") or {}
        assert outcome.get("ended_reason") == "victory"
        assert outcome.get("xp_awarded") == {"hero": 50}
        assert driver.state["experience"] >= 50
    _run(steps)


def test_replay_verify_matches_mid_combat():
    """verify пересобирает бой из истории команд и сверяет дайджест."""
    async def steps():
        driver = await _start(seed=3)
        await driver.wait_hero()
        await driver.cmd("move", position={"x": 3, "y": 1, "z": 0})
        checked = await execute({"mode": "verify", "state": driver.state})
        assert checked["state"]["combat"]["view"]["tracked_hp"] == driver.combat()["tracked_hp"]
    _run(steps)


def test_tampered_history_is_rejected():
    """Подмена записи команды ломает сверку дайджеста."""
    async def steps():
        driver = await _start(seed=3)
        await driver.wait_hero()
        await driver.cmd("move", position={"x": 3, "y": 1, "z": 0})
        driver.state["combat"]["commands"][-1]["action"]["position"] = {"x": 0, "y": 1, "z": 0}
        with pytest.raises(ValueError, match="replay diverged|does not match"):
            await execute({"mode": "verify", "state": driver.state})
    _run(steps)


def test_second_wind_heals_and_records_feature_use():
    """Способность тратит заряд и оставляет счётчик, который снимет отдых."""
    async def steps():
        driver = await _start(seed=3)
        await driver.wait_hero()
        driver.state["hero"]["hp_current"] = 5
        before = driver.state["hero"]["hp_current"]
        await driver.cmd("use_feature", feature_id="second-wind")
        hero = driver.state["hero"]
        assert hero["hp_current"] > before
        assert hero["hp_current"] <= hero["hp_max"]
        counters = hero.get("custom_counters") or {}
        assert "feature_use:second-wind" in counters
    _run(steps)