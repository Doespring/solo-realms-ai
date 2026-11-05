"""PHB chapter 1: source coverage, exact math, existing Engine and release gates.

Blocked/narrative entries have refusal/proposal-boundary tests, not claimed
mechanical certification. No PDF text or data-selected Python is executed.
"""
from copy import deepcopy
import hashlib
import json
import re
import socket

import pytest

from rules_data.runtime.classifier import classify
from rules_data.runtime.math_handlers import execute
from rules_data.testing.schema_validation import V2, load_json, validate
from solo.combat import Combatant, CombatEngine
from solo.contracts import Hero, Step
from solo.geometry import Geometry, MapObject, Position, rectangular_map
from solo.hybrid_rules import CheckCommand, CheckProposal, verified_pack
from solo.rulesets import BUILTINS, RulesetEngine, SKILLS

FILES = sorted((V2 / "chapters/phb-01").glob("*.json"))
RULES = [row for path in FILES for row in load_json(path)["rules"]]
BY_ID = {row["id"]: row for row in RULES}


class Dice:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def randint(self, low, high):
        value = next(self.values)
        assert low <= value <= high
        self.calls += 1
        return value


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("chapter rules verification is offline")
    monkeypatch.setattr(socket.socket, "connect", denied)


def battle(*draws, enemy_position=(2, 1), weapon="longsword"):
    hero = Combatant(id="hero", stats=Hero(name="PHB fixture", weapon_proficiencies=["martial"]),
                       position=Position(x=1, y=1), team="hero", weapon=weapon, ammo=5)
    foe = Combatant(id="foe", stats=Hero(name="Foe", hp=100, max_hp=100, ac=10),
                      position=Position(x=enemy_position[0], y=enemy_position[1]), team="enemy", weapon="club")
    state = {"hero": hero.stats.model_dump(), "ruleset": BUILTINS["dnd2024"].model_dump(),
             "combat": {"active": True, "map": rectangular_map(70, 6).model_dump(),
                        "actors": [hero.model_dump(), foe.model_dump()], "order": ["hero", "foe"],
                        "cursor": 0, "round": 1, "lethal_risk_declared": True}}
    return CombatEngine(state, rng=Dice(*draws))


def formal_behavior(key, facts):
    """Manually reviewed assertions; this closed test switch never reads code names."""
    rules = RulesetEngine(BUILTINS["dnd2024"])
    if key == "rounding":
        assert execute("scale_integer", dict(value=-3, numerator=1, denominator=2)) == -2
    elif key == "dice_sum":
        assert execute("sum_dice", dict(draws=[2, 5, 8], sides=8, modifier=5)) == 20
    elif key == "percentile":
        assert execute("percentile", dict(tens=0, units=0)) == 100
        assert execute("percentile", dict(tens=7, units=1)) == 71
    elif key == "d3":
        assert [execute("scale_integer", dict(value=n, numerator=1, denominator=2, rounding="ceil"))
                for n in range(1, 7)] == [1, 1, 2, 2, 3, 3]
    elif key == "percentage_chance":
        assert execute("percentage_event", dict(draw=5, chance=5)) is True
        assert execute("percentage_event", dict(draw=6, chance=5)) is False
    elif key == "abilities":
        assert set(Hero(name="Test").abilities) == set(facts["abilities"])
        with pytest.raises(ValueError):
            Hero(name="Missing", abilities={"strength": 10})
    elif key == "ability_modifier":
        expected = [-5, -4, -4, -3, -3, -2, -2, -1, -1, 0, 0, 1, 1, 2, 2,
                    3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10]
        for score, bonus in enumerate(expected, 1):
            hero = Hero(name="Scores", abilities={ability: score for ability in Hero(name="Keys").abilities})
            assert rules.d20(hero, "strength", rng=Dice(10))["bonus"] == bonus
    elif key == "ability_check":
        for draw, dc, expected in [(20, 40, False), (1, 1, True), (13, 15, True), (12, 15, False)]:
            result = rules.check(Hero(name="Test"), Step(kind="check", text="Test", ability="strength", dc=dc), Dice(draw))
            assert result["success"] is expected
    elif key in ("saving_throw", "save_proficiency"):
        hero = Hero(name="Save", saving_throw_proficiencies=["strength"])
        assert rules.saving_throw(hero, "strength", 15, rng=Dice(11))["success"] is True
        assert rules.saving_throw(hero, "wisdom", 15, rng=Dice(11))["success"] is False
    elif key == "proficiency":
        assert [rules.proficiency(level) for level in range(1, 21)] == [2]*4 + [3]*4 + [4]*4 + [5]*4 + [6]*4
    elif key in ("skill_check", "proficiency_once"):
        hero = Hero(name="Skill", skill_proficiencies=["athletics"], expertise=["athletics"])
        assert rules.skill(hero, "athletics", 15, rng=Dice(9))["bonus"] == 6
        assert rules.skill(hero, "history", 15, rng=Dice(20))["bonus"] == 0
    elif key == "skills_table":
        assert facts == SKILLS and len(SKILLS) == 18
    elif key in ("advantage", "cancellation"):
        for advantage, disadvantage, draws, selected in [(True, False, [3, 18], 18),
                (False, True, [3, 18], 3), (True, True, [7], 7), (False, False, [7], 7)]:
            result = rules.d20(Hero(name="Pool"), "strength", advantage=advantage,
                               disadvantage=disadvantage, rng=Dice(*draws))
            assert result["selected"] == selected and result["dice"] == draws
    elif key in ("action_budget", "turn_budget", "action_attack", "attack_roll", "weapon_proficiency",
                  "melee_reach", "damage_roll", "critical_damage", "attack_procedure"):
        engine = battle(20, 8, 6)
        result = engine.attack("hero", "foe")
        assert result["critical"] and result["damage_dice"] == [8, 6] and result["raw_damage"] == 16
        assert result["roll"]["bonus"] == 4 and result["hp_damage"] == 16
        with pytest.raises(ValueError):
            engine.attack("hero", "foe")
        engine = battle(1)
        assert engine.attack("hero", "foe")["hit"] is False
    elif key == "action_dash":
        engine = battle()
        engine.defend("hero", "dash")
        assert engine.actors["hero"].dash_movement == 30 and not engine.actors["hero"].action
    elif key == "action_disengage":
        engine = battle()
        engine.defend("hero", "disengage")
        assert engine.move("hero", (0, 1, 0)) and engine.rng.calls == 0
    elif key == "bonus_action":
        engine = battle()
        with pytest.raises(ValueError):
            engine.bonus("hero", "dash")
        hero = engine.actors["hero"]
        hero.stats.class_name = "rogue"; hero.stats.level = 2; hero.stats.features = ["cunning_action"]
        engine.bonus("hero", "dash")
        assert hero.action and not hero.bonus_action and hero.dash_movement == 30
        with pytest.raises(ValueError):
            engine.bonus("hero", "disengage")
    elif key == "reaction":
        engine = battle(1)
        engine.attack("hero", "foe", reaction=True)
        assert engine.actors["hero"].action and not engine.actors["hero"].reaction
        with pytest.raises(ValueError):
            engine.attack("hero", "foe", reaction=True)
        engine.end_turn("hero"); engine.end_turn("foe")
        assert engine.actors["hero"].reaction
    elif key in ("initiative", "surprise"):
        hero = Hero(name="Initiative")
        result = rules.initiative(hero, surprised=True, rng=Dice(18, 3))
        assert result["selected"] == 3 and not result["skip_first_turn"]
        assert rules.initiative(hero, rng=Dice(9))["total"] == 11
    elif key in ("combat_order", "turn_inactivity"):
        engine = battle()
        engine.end_turn("hero"); engine.end_turn("foe")
        assert engine.current().id == "hero" and engine.data["round"] == 2
        assert engine.state["elapsed_minutes"] == 0.1 and engine.actors["hero"].action
    elif key in ("movement", "split_movement"):
        engine = battle(enemy_position=(20, 1))
        engine.move("hero", (3, 1, 0))
        engine.defend("hero", "disengage")
        engine.move("hero", (7, 1, 0))
        assert engine.actors["hero"].movement_spent == 30
        with pytest.raises(ValueError):
            engine.move("hero", (8, 1, 0))
    elif key in ("grid", "difficult_terrain"):
        board = rectangular_map(4, 4)
        geometry = Geometry(board, BUILTINS["dnd2024"])
        geometry.cells[(2, 2, 0)].move_cost = 2
        assert geometry.edge_cost((1, 1, 0), (2, 2, 0)) == 10
        assert geometry.distance((0, 0, 0), (3, 3, 0)) == 15
        geometry.cells[(1, 0, 0)].blocked = True
        assert (1, 1, 0) not in list(geometry.neighbors((0, 0, 0)))
    elif key == "cover":
        geometry = Geometry(rectangular_map(5, 3), BUILTINS["dnd2024"])
        geometry.cells[(1, 1, 0)].cover = 2; geometry.cells[(2, 1, 0)].cover = 5
        assert geometry.cover((0, 1, 0), (4, 1, 0)) == 5
        geometry.cells[(2, 1, 0)].opaque = True
        assert geometry.cover((0, 1, 0), (4, 1, 0)) == 100
    elif key == "ranged_range":
        engine = battle(18, 2, enemy_position=(19, 1), weapon="shortbow")
        assert engine.attack("hero", "foe")["roll"]["mode"] == "помеха"
        engine = battle(enemy_position=(67, 1), weapon="shortbow")
        with pytest.raises(ValueError):
            engine.attack("hero", "foe")
        assert engine.rng.calls == 0
    elif key == "opportunity_attack":
        engine = battle(1)
        engine.move("hero", (0, 1, 0))
        assert engine.rng.calls == 1 and not engine.actors["foe"].reaction
        assert engine.actors["hero"].position.x == 0
    elif key == "illumination":
        geometry = Geometry(rectangular_map(3, 3), BUILTINS["dnd2024"])
        for x, level in enumerate(("bright", "dim", "dark")):
            geometry.cells[(x, 0, 0)].light = level
            assert geometry.illumination((x, 0, 0)) == level
    elif key == "light_obscurement":
        engine = battle(18, 3)
        engine.geometry.cells[(1, 1, 0)].light = "dim"
        result = engine.search("hero", (1, 1, 0))
        assert result["roll"]["dice"] == [18, 3] and result["roll"]["selected"] == 3
    elif key == "free_interaction":
        engine = battle()
        engine.map.objects.append(MapObject(id="lamp", name="Lamp", kind="light", position=Position(x=1, y=1)))
        engine.interact("hero", "lamp")
        assert not engine.map.objects[0].enabled
        with pytest.raises(ValueError):
            engine.interact("hero", "lamp")
    elif key in ("hit_points", "zero_hp"):
        engine = battle(); hero = engine.actors["hero"]
        engine.damage(hero, 11, "fire")
        assert hero.stats.hp == 1 and hero.action
        engine.damage(hero, 1, "fire")
        assert hero.stats.hp == 0 and "unconscious" in hero.stats.conditions and not hero.dead
    elif key == "damage_traits":
        engine = battle(); foe = engine.actors["foe"]
        foe.resistances = ["fire", "fire"]; foe.vulnerabilities = ["fire", "fire"]
        assert engine.damage(foe, 23, "fire")["damage_after_traits"] == 22
    elif key in ("damage_types", "immunity"):
        engine = battle(); foe = engine.actors["foe"]; foe.immunities = ["fire"]
        assert engine.damage(foe, 17, "fire")["hp_damage"] == 0
        assert engine.damage(foe, 5, "cold")["hp_damage"] == 5
    elif key == "half_damage":
        assert execute("scale_integer", dict(value=23, numerator=1, denominator=2)) == 11
    elif key == "healing":
        assert execute("resource_change", dict(current=14, maximum=20, amount=8, operation="restore")) == 20
    elif key == "death_saves":
        engine = battle(10, 9, 10, 9, 10); hero = engine.actors["hero"]; hero.stats.hp = 0
        for _ in range(5):
            engine.death_save(hero)
        assert hero.stable and hero.death_successes == hero.death_failures == 0
        engine = battle(20); hero = engine.actors["hero"]; hero.stats.hp = 0
        engine.death_save(hero)
        assert hero.stats.hp == 1
        engine = battle(1, 9); hero = engine.actors["hero"]; hero.stats.hp = 0
        assert engine.death_save(hero)["failures"] == 2
        assert engine.death_save(hero)["failures"] == 3 and hero.dead
    elif key == "damage_at_zero":
        engine = battle(); hero = engine.actors["hero"]; hero.stats.hp = 0
        engine.damage(hero, 1, "fire", critical=True)
        assert hero.death_failures == 2
        engine.damage(hero, 1, "fire")
        assert hero.dead
    elif key == "temp_hp_absorption":
        engine = battle(); hero = engine.actors["hero"]; hero.temp_hp = 5
        result = engine.damage(hero, 7, "fire")
        assert result["temp_hp_absorbed"] == 5 and result["hp_damage"] == 2 and hero.stats.hp == 10
    elif key == "temp_hp_not_healing":
        engine = battle(); hero = engine.actors["hero"]; hero.stats.hp = 0
        hero.stats.conditions = ["unconscious"]; hero.temp_hp = 5
        engine.damage(hero, 3, "fire")
        assert hero.stats.hp == 0 and "unconscious" in hero.stats.conditions and hero.temp_hp == 2
    elif key == "rest_overview":
        state = {"hero": Hero(name="Rest", hp=1).model_dump()}
        assert rules.rest(state, "short", hit_dice=1, rng=Dice(4))["duration_minutes"] == 60
        assert rules.rest(state, "long")["duration_minutes"] == 480 and state["hero"]["hp"] == 12
    else:
        raise AssertionError("No behavioral certification for " + key)


@pytest.mark.parametrize("row", RULES, ids=lambda row: row["id"])
def test_catalog_behavior(row):
    key = row["id"].split(".")[-1]
    readiness = row["readiness"]
    assert classify(row["classification"]["semantic_required"], row["classification"]["formal_required"]) == row["classification"]["mode"]
    assert row["behavior_tests"] == ["phb01." + key]
    if readiness["status"] == "blocked" or readiness["handler"] is None:
        # Boundary test only. It must not upgrade an unsupported rule to ready.
        assert row["id"] not in {item.id for item in verified_pack()[0]}
        if readiness["status"] == "blocked":
            assert readiness["blockers"]
        for model in (CheckCommand, CheckProposal):
            with pytest.raises(ValueError):
                model.model_validate({"operation": row["id"], "state_delta": {"hp": 999}})
    else:
        formal_behavior(key, row["facts"])


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_chapter_schema(path):
    validate(load_json(path), "routing.schema.json")


@pytest.mark.parametrize("patch", [
    lambda row: row.update(revision=0),
    lambda row: row.pop("dependencies"),
    lambda row: row["readiness"].update(status="ready_without_test"),
    lambda row: row["facts"].update(raw_code={"eval": "state.clear()"}),
    lambda row: row["formal_operations"][0].update(op="exec"),
    lambda row: row["source"].update(sha256="0"*64),
    lambda row: row.update(behavior_tests=[]),
])
def test_schema_rejects_invalid_new_definitions(patch):
    document = {"schema_version": "2.1.0", "rules": [deepcopy(RULES[0])]}
    patch(document["rules"][0])
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate(document, "routing.schema.json")


def test_coverage_accounts_for_every_bookmark_rule_page_and_dependency():
    coverage = load_json(V2 / "coverage/phb-01.json")
    validate(coverage, "coverage.schema.json")
    assert len(BY_ID) == len(RULES) == coverage["rule_count"]
    source = (V2 / coverage["evidence"]["text_file"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == coverage["evidence"]["text_sha256"]
    assert [int(page) for page in re.findall(rb"# Page (\d+)", source)] == list(range(8, 34))
    toc = (V2 / "evidence/phb-toc.txt").read_text(encoding="utf-8")
    chapter = toc.split("Правила игры -> 8", 1)[1].split("Создание персонажа -> 34", 1)[0]
    bookmarks = re.findall(r"\[[^\]]+\] (.*?) -> (\d+)", chapter)
    assert [(item["title"], str(item["pdf_page"])) for item in coverage["bookmarks"]] == bookmarks
    assert all(item["rule_ids"] for item in coverage["bookmarks"])
    referenced = {id for item in coverage["subsections"] for id in item["rule_ids"]}
    assert referenced == set(BY_ID)
    assert {row["id"] for row in coverage["rules"]} == set(BY_ID)
    external = set(coverage["external_dependencies"])
    legacy = {"dnd2024.d20." + name for name in ("advantage", "disadvantage", "cancellation")}
    visiting, visited = set(), set()
    def visit(id):
        assert id not in visiting, "cyclic rule dependency"
        if id in visited:
            return
        visiting.add(id)
        for dependency in BY_ID[id]["dependencies"]:
            if dependency in BY_ID:
                visit(dependency)
            else:
                assert dependency in external or dependency in legacy
        visiting.remove(id); visited.add(id)
    for id in BY_ID:
        visit(id)
    assert coverage["extraction_complete"] and not coverage["execution_complete"]
    assert coverage["unaccounted_bookmarks"] == []
    assert set(coverage["adapter_rule_ids"]) == {item.id for item in verified_pack()[0] if ".phb01." in item.id}


def test_source_tables_and_exceptions_are_not_replaced_by_engine_defaults():
    facts = lambda key: BY_ID["dnd2024.phb01." + key]["facts"]
    assert facts("difficulty")["dc"] == [5, 10, 15, 20, 25, 30]
    assert facts("proficiency")["upper_bounds"] == [4, 8, 12, 16, 20, 24, 28, 30]
    assert facts("proficiency")["bonuses"] == [2, 3, 4, 5, 6, 7, 8, 9]
    assert facts("travel_pace")["feet_per_minute"] == [400, 300, 200]
    assert facts("travel_pace")["miles_per_hour"] == [4, 3, 2]
    assert facts("travel_pace")["miles_per_day"] == [30, 24, 18]
    assert facts("travel_pace")["normal_disadvantage_skills"] == ["stealth"]
    assert facts("cover")["ac_bonus"] == facts("cover")["dex_save_bonus"] == [2, 5, 0]
    assert facts("knockout")["hp_after"] == 1
    assert facts("condition_duration")["same_condition_durations"] == "independent"
    assert facts("temp_hp_choice")["choice"] == ["existing", "new"]
    assert facts("underwater_melee")["exception_damage_type"] == "piercing"
    assert facts("underwater_ranged")["beyond_normal"] == "automatic_miss"
    assert facts("voluntary_save_failure")["draws"] == 0
    assert facts("damage_traits")["example_result"] == 22
    assert facts("heroic_inspiration")["must_use_new"] is True


@pytest.mark.parametrize("key,field,value", [
    ("ability_modifier", "offset", "10"), ("ability_modifier", "divisor", 0),
    ("proficiency", "bonuses", ["2"]*8), ("skill_check", "untrained_allowed", "true"),
    ("death_saves", "natural_20_hp", True), ("damage_traits", "resistance_divisor", 0),
])
def test_typed_mechanic_payload_rejects_wrong_units_and_types(key, field, value):
    row = deepcopy(BY_ID["dnd2024.phb01." + key])
    row["facts"][field] = value
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate({"schema_version": "2.1.0", "rules": [row]}, "routing.schema.json")


@pytest.mark.parametrize("case", load_json(V2 / "tests/phb-01.math.cases.json")["cases"], ids=lambda case: case["id"])
def test_new_mathematical_vectors(case):
    if case.get("reject"):
        with pytest.raises(ValueError):
            execute(case["op"], case["arguments"])
    else:
        arguments = deepcopy(case["arguments"])
        result = execute(case["op"], arguments)
        assert type(result) is type(case["expected"]) and result == case["expected"]
        assert arguments == case["arguments"]


@pytest.mark.parametrize("target,darkvision,draws", [
    ((1, 1, 0), 0, [18, 3]), ((1, 2, 0), 0, [18, 3]), ((1, 2, 0), 30, [18]),
])
def test_dim_search_requires_an_actual_darkvision_sense(target, darkvision, draws):
    engine = battle(*draws)
    engine.actors["hero"].darkvision_ft = darkvision
    engine.geometry.cells[target].light = "dim"
    assert engine.search("hero", target)["roll"]["dice"] == draws
