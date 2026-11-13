"""PHB chapter 1: source coverage, exact math, existing Engine and release gates.

Blocked/narrative entries have refusal/proposal-boundary tests, not claimed
mechanical certification. No PDF text or data-selected Python is executed.

Математика главы исполняется локальными обработчиками ниже: каталог
``rules_data.runtime`` в этом репозитории отсутствует (он принадлежал другому
потоку и никогда не коммитился сюда). Векторы взяты из
``rules_data/v2/tests/phb-01.math.cases.json`` того же корпуса.
"""
from copy import deepcopy
import json
from pathlib import Path


def scale_integer(value, numerator, denominator, rounding="floor"):
    """PHB 2024: деление с округлением вниз; -3/2 = -2, но не -1."""
    for name, number in (("value", value), ("numerator", numerator), ("denominator", denominator)):
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("%s must be an int" % name)
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator <= 0:
        raise ValueError("numerator must be positive")
    if rounding not in ("floor", "ceil"):
        raise ValueError("unknown rounding: %r" % (rounding,))
    scaled = value * numerator
    if rounding == "floor":
        return scaled // denominator
    return -((-scaled) // denominator)


def sum_dice(draws, sides, modifier=0):
    if isinstance(sides, bool) or not isinstance(sides, int) or sides < 1:
        raise ValueError("sides must be a positive int")
    if isinstance(modifier, bool) or not isinstance(modifier, int):
        raise ValueError("modifier must be an int")
    for draw in draws:
        if isinstance(draw, bool) or not isinstance(draw, int) or not 1 <= draw <= sides:
            raise ValueError("draw out of range")
    return sum(draws) + modifier


def percentile(tens, units):
    for name, digit in (("tens", tens), ("units", units)):
        if isinstance(digit, bool) or not isinstance(digit, int) or not 0 <= digit <= 9:
            raise ValueError("%s must be a single digit" % name)
    result = tens * 10 + units
    return 100 if result == 0 else result


def percentage_event(draw, chance):
    for name, number in (("draw", draw), ("chance", chance)):
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("%s must be an int" % name)
    if not 1 <= draw <= 100:
        raise ValueError("draw must be within 1..100")
    if not 0 <= chance <= 100:
        raise ValueError("chance must be within 0..100")
    return draw <= chance


def replace_draw(draws, index, replacement, sides):
    if isinstance(sides, bool) or not isinstance(sides, int) or sides < 1:
        raise ValueError("sides must be a positive int")
    for draw in draws:
        if isinstance(draw, bool) or not isinstance(draw, int) or not 1 <= draw <= sides:
            raise ValueError("draw out of range")
    if isinstance(replacement, bool) or not isinstance(replacement, int) or not 1 <= replacement <= sides:
        raise ValueError("replacement out of range")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(draws):
        raise ValueError("index out of range")
    result = list(draws)
    result[index] = replacement
    return result


def resource_change(current, maximum, amount, operation):
    """PHB 2024: лечение восстанавливает, но не поднимает выше максимума."""
    for name, number in (("current", current), ("maximum", maximum), ("amount", amount)):
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("%s must be an int" % name)
    if maximum < 0 or current < 0 or amount < 0:
        raise ValueError("values must be non-negative")
    if operation == "restore":
        return min(maximum, current + amount)
    if operation == "spend":
        return max(0, current - amount)
    raise ValueError("unknown operation: %r" % (operation,))


HANDLERS = {
    "scale_integer": scale_integer,
    "sum_dice": sum_dice,
    "percentile": percentile,
    "percentage_event": percentage_event,
    "replace_draw": replace_draw,
    "resource_change": resource_change,
}


def execute(op, arguments):
    if op not in HANDLERS:
        raise ValueError("unregistered operation: %r" % (op,))
    return HANDLERS[op](**arguments)


def classify(semantic_required, formal_required):
    """Режим правила выводится из (semantic, formal), а не задаётся руками."""
    if not isinstance(semantic_required, bool) or not isinstance(formal_required, bool):
        raise ValueError("classification flags must be bool")
    if semantic_required and formal_required:
        return "hybrid"
    if semantic_required:
        return "adjudicated"
    if formal_required:
        return "deterministic"
    raise ValueError("a rule needs at least one route")

from copy import deepcopy
import hashlib
import json
import re
import socket

import pytest

V2 = Path(__file__).resolve().parents[1] / "rules_data" / "v2"


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)


def _offline_registry(documents):
    """Все схемы v2 лежат рядом; $ref между ними резолвится локально, без сети."""
    from referencing import Registry, Resource
    registry = Registry()
    for name, document in documents.items():
        registry = registry.with_resource(
            (V2 / name).resolve().as_uri(), Resource.from_contents(document))
    return registry


def validate(instance, schema="ai-engine.schema.json"):
    """Проверка по настоящей схеме v2 из этой папки, офлайн."""
    from jsonschema import Draft202012Validator
    if not isinstance(schema, str) or schema.startswith(("/", "\\\\")) or ".." in schema:
        raise ValueError("schema must be a pinned local v2 schema")
    path = V2 / schema
    if not path.is_file():
        raise ValueError("schema must be a pinned local v2 schema")
    local = {}
    for candidate in sorted(V2.glob("*.json")):
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(document, dict) and ("properties" in document or "$defs" in document):
            local[candidate.name] = document
    validator = Draft202012Validator(json.loads(path.read_text(encoding="utf-8")),
                                     registry=_offline_registry(local))
    if list(validator.iter_errors(instance)):
        raise ValueError("JSON Schema rejected instance")
from solo.combat import Combatant, CombatEngine, footprint
from solo.contracts import Hero, Resource, Step
from solo.geometry import BattleMap, Cell, Geometry, MapObject, Position, rectangular_map
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


def _ally(engine, position=(1, 1)):
    """r181: спутник в бою — тот, кто оказывает Помощь (действие в свой ход)."""
    ally = Combatant(id="ally", stats=Hero(name="Спутник"), position=Position(x=position[0], y=position[1]),
                     team="ally")
    engine.actors["ally"] = ally
    engine.data["actors"].append(ally.model_dump())
    engine.data["order"].append("ally")
    engine.data["cursor"] = engine.data["order"].index("ally")
    return ally


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
    elif key == "voluntary_save_failure":
        # PHB 2024, page 11: добровольный провал — решение игрока, кость не бросается.
        class NoDie:
            calls = 0

            def randint(self, low, high):
                NoDie.calls += 1
                raise AssertionError("добровольный провал не бросает кость")
        result = rules.saving_throw(Hero(name="Voluntary"), "dexterity", 15,
                                    voluntary=True, rng=NoDie())
        assert NoDie.calls == 0
        assert result["draws"] == 0 and result["dice"] == [] and result["selected"] is None
        assert result["success"] is False and facts["draws"] == 0
        assert "кость не бросалась" in result["formula"]
    elif key == "attack_ability":
        from solo.combat import WEAPONS, attack_ability
        hero = Hero(name="Attack", abilities=dict(strength=12, dexterity=18, constitution=10,
                                                  intelligence=10, wisdom=10, charisma=10))
        assert attack_ability(WEAPONS["longsword"], hero) == ("strength", "рукопашное оружие — Сила")
        assert attack_ability(WEAPONS["shortbow"], hero)[0] == "dexterity"
        # Фехтовальное оружие: выбор из правила, а не константа.
        assert attack_ability(WEAPONS["dagger"], hero)[0] == "dexterity"
        assert attack_ability(WEAPONS["dagger"], hero, "strength") == ("strength", "выбор игрока из правила")
        with pytest.raises(ValueError):
            attack_ability(WEAPONS["longsword"], hero, "dexterity")
        assert set(facts) >= {"melee_weapon", "ranged_weapon", "finesse", "unarmed"}
    elif key == "base_ac":
        from solo.armor_class import armor_class
        hero = Hero(name="AC")
        plain = armor_class(hero)
        assert plain["ac"] == 10 + (hero.abilities["dexterity"] - 10) // 2
        assert plain["calculations_used"] == 1 and plain["ability"] == "dexterity"
        armored = armor_class(hero, armor=dict(base_ac=16, dex_cap=0))
        assert armored["ac"] == 16 and armored["calculation"] == "armor"
        # Второй базовый расчёт не складывается с первым.
        both = armor_class(Hero(name="AC", features=["unarmored_barbarian"]),
                           armor=dict(base_ac=16, dex_cap=0))
        assert both["ac"] == 16 and both["calculations_used"] == 1
        assert [item["id"] for item in both["ignored"]] == ["unarmored_barbarian"]
        assert facts["base"] == 10 and facts["base_calculations_used"] == 1
    elif key == "single_die_replacement":
        from solo.rulesets import replace_die
        rolled = dict(dice=[4, 17], selected=17, bonus=3, total=20, mode="преимущество")
        replaced = replace_die(rolled, 0, 9)
        assert replaced["dice"] == [9, 17] and replaced["selected"] == 17
        assert rolled["dice"] == [4, 17]  # исходный бросок не переписан
        with pytest.raises(ValueError):
            replace_die(rolled, 2, 9)
        assert facts["replacement_count"] == 1 and facts["choice"] == "actor_selects_index"
    elif key == "heroic_inspiration":
        from solo.rulesets import INSPIRATION, inspiration_charges
        hero = Hero(name="Inspiration")
        hero.resources[INSPIRATION] = Resource(current=1, maximum=1, recovery="none")
        result = rules.d20(hero, "strength", inspiration=True, rng=Dice(11, 4))
        assert result["inspiration"]["before"] == 11 and result["dice"] == [4]
        assert result["inspiration"]["total_before"] == 11 + result["bonus"]
        assert result["inspiration"]["total_after"] == 4 + result["bonus"]
        assert inspiration_charges(hero) == 0
        with pytest.raises(ValueError):
            rules.d20(hero, "strength", inspiration=True, rng=Dice(11, 4))
        assert facts["maximum"] == 1 and facts["must_use_new"] is True
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
    elif key in ("attack_roll", "weapon_proficiency",
                  "melee_reach", "damage_roll", "critical_damage", "attack_procedure"):
        engine = battle(20, 8, 6)
        result = engine.attack("hero", "foe")
        assert result["critical"] and result["damage_dice"] == [8, 6] and result["raw_damage"] == 16
        assert result["roll"]["bonus"] == 4 and result["hp_damage"] == 16
        with pytest.raises(ValueError):
            engine.attack("hero", "foe")
        engine = battle(1)
        assert engine.attack("hero", "foe")["hit"] is False
    elif key == "action_budget":
        # Процедура: одно действие, одно бонусное, одно взаимодействие, движение 30.
        engine = battle()
        hero = engine.actors["hero"]
        assert hero.action and hero.bonus_action and hero.reaction
        assert hero.movement_spent == 0 and hero.dash_movement == 0
        hero.action = False; hero.bonus_action = False  # снимаем провокацию реакции
        hero.disengaged = True
        engine.move("hero", (0, 1, 0))
        assert hero.movement_spent == 5
    elif key == "turn_budget":
        # Ход кончается существом (PHB 2024), а не действием.
        engine = battle()
        hero = engine.actors["hero"]
        engine.end_turn("hero")
        assert hero.movement_spent == 0 and engine.current().id == "foe"
        engine.end_turn("foe")
        assert engine.current().id == "hero" and engine.data["round"] == 2
        assert engine.actors["hero"].action
    elif key == "action_attack":
        # Процедура атаки: бросок d20 против КД, затем кости урона, затем действие.
        engine = battle(15, 4)
        result = engine.attack("hero", "foe")
        assert result["hit"] is True and result["hp_damage"] == result["raw_damage"]
        assert result["hp_damage"] > 0 and not engine.actors["hero"].action
    elif key == "action_dodge":
        # Обе половины: помеха на атаку по уклоняющемуся и преимущество на
        # его спасбросок Ловкости; теряется при недееспособности и при Скорости 0.
        engine = battle(18, 3)
        engine.defend("hero", "dodge")
        assert engine.dexterity_save_advantage() == ["hero"]
        engine.end_turn("hero")
        result = engine.attack("foe", "hero")
        assert result["roll"]["mode"] == "помеха" and result["roll"]["selected"] == 3
        engine.actors["hero"].stats.conditions = ["incapacitated"]
        assert engine.dexterity_save_advantage() == []
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
    elif key in ("action_influence", "action_study", "action_search"):
        # Host scene action: solo/nat20_content.scene_check names the one skill
        # check the engine rolls, and solo/scene_actions.py decides what a
        # success may show. The DC is the scene's own entry when it has one and
        # the host registry otherwise; nothing here invents a consequence.
        from types import SimpleNamespace
        from solo.nat20_content import SCENE_ACTIONS, SCENE_CHECKS, scene_check
        action = {"action_influence": "influence", "action_study": "study",
                  "action_search": "search"}[key]
        skills = SCENE_ACTIONS[action]["skills"]
        assert skills
        for skill, check_id in skills.items():
            registered = SCENE_CHECKS[check_id]
            assert registered[0] == "skill" and registered[1] == skill
            default = scene_check(action, skill)
            assert (default["check_id"], default["kind"], default["skill"], default["dc"]) == (
                check_id, "skill", skill, registered[3])
            assert default["source"] == "registry"
            scene = SimpleNamespace(checks={check_id: SimpleNamespace(
                kind="skill", skill=skill, ability=None, dc=18)})
            assert (scene_check(action, skill, scene)["dc"],
                    scene_check(action, skill, scene)["source"]) == (18, "scene")
        if key == "action_influence":
            # r179: книга (стр. 15) называет для Влияния все четыре навыка
            # Харизмы, включая Выступление, и Мудрость (Уход за животными).
            # Прежнее правило вычитало Выступление — это был дефект хоста.
            assert set(skills) == set(facts["charisma_skills"]) | {facts["wisdom_skill"]}
        elif key == "action_study":
            # Проницательность добавлена постановкой r130 к пяти умственным
            # навыкам книги; остальные совпадают с facts источника.
            assert set(skills) - {"insight"} == set(facts["skills"])
    elif key == "ranged_close":
        # PHB 2024, page 27: Disadvantage within 5 feet of a hostile creature that
        # is not Incapacitated. The supplied paragraph asks nothing about that
        # creature seeing the shooter, so a blinded neighbour still imposes it.
        engine = battle(15, 5, enemy_position=(2, 1), weapon="shortbow")
        result = engine.attack("hero", "foe")
        assert result["roll"]["mode"] == "помеха"
        assert "рядом враг" in result["roll"]["disadvantage_reasons"]
        engine = battle(15, 5, enemy_position=(2, 1), weapon="shortbow")
        engine.actors["foe"].stats.conditions = ["blinded"]
        assert engine.attack("hero", "foe")["roll"]["mode"] == "обычный"
    elif key == "unseen_attack":
        # Advantage when the target cannot see you, Disadvantage against a target
        # you cannot see, and a miss when it left the cell you aimed at.
        engine = battle(15, 5, 5, 5)
        engine.actors["hero"].stats.conditions = ["invisible"]
        assert engine.attack("hero", "foe")["roll"]["mode"] == "преимущество"
        engine = battle(15, 5)
        engine.actors["foe"].stats.conditions = ["invisible"]
        engine.data["last_seen"] = {"foe": dict(id="foe", name="Foe", round=1,
                                                position=dict(x=2, y=1, z=0))}
        assert engine.attack("hero", "foe")["roll"]["mode"] == "помеха"
        engine = battle(enemy_position=(4, 1), weapon="shortbow")
        engine.actors["foe"].stats.conditions = ["invisible"]
        engine.data["last_seen"] = {"foe": dict(id="foe", name="Foe", round=1,
                                                position=dict(x=3, y=1, z=0))}
        engine.rng = Dice()
        wrong = engine.attack("hero", "foe")
        assert wrong["wrong_location"] and wrong["roll"] is None and engine.rng.calls == 0
    elif key == "size":
        # The table names 10x10 feet / 4 squares for Large; the grid holds the space.
        assert facts["square_side_ft"][3] == 10 and facts["grid_squares"][3] == 4
        engine = battle(15, 5, enemy_position=(5, 5))
        engine.actors["foe"].stats.size = "Large"
        assert set(footprint(engine.actors["foe"])) == {(5, 5, 0), (6, 5, 0),
                                                        (5, 6, 0), (6, 6, 0)}
        with pytest.raises(ValueError, match="пространстве другого существа"):
            engine.movement_path("hero", (6, 5, 0))
    elif key == "move_through_creatures":
        # A one-cell corridor, so the only route is through the other creature's space.
        corridor = BattleMap(width=5, height=3,
                             cells=[Cell(x=x, y=y, blocked=y != 1) for y in range(3) for x in range(5)])
        engine = battle(1, 1, enemy_position=(2, 1))
        engine.map = corridor
        engine.geometry = Geometry(corridor, BUILTINS["dnd2024"])
        engine.actors["hero"].position = Position(x=1, y=1)
        engine.actors["foe"].position = Position(x=2, y=1)
        engine.actors["foe"].team = "ally"
        passable, difficult, blocked = engine.crossing(engine.actors["hero"])
        assert (2, 1, 0) in passable and (2, 1, 0) not in difficult and (2, 1, 0) not in blocked
        path, cost = engine.movement_path("hero", (3, 1, 0))
        assert path == [(1, 1, 0), (2, 1, 0), (3, 1, 0)] and cost == 10
        with pytest.raises(ValueError, match="завершить перемещение"):
            engine.movement_path("hero", (2, 1, 0))
        engine.actors["foe"].team = "enemy"
        passable, _, blocked = engine.crossing(engine.actors["hero"])
        assert (2, 1, 0) in blocked and (2, 1, 0) not in passable
        with pytest.raises(ValueError):
            engine.movement_path("hero", (3, 1, 0))
    elif key == "combat_end":
        assert set(facts["ends"]) == {"killed", "incapacitated", "surrendered",
                                      "fled", "mutual_agreement"}
        engine = battle(20, 8, 8, enemy_position=(2, 1))
        engine.actors["foe"].stats.hp = engine.actors["foe"].stats.max_hp = 8
        engine.attack("hero", "foe")
        assert engine.data["active"]  # a side is out, but the turn ends the fight
        engine.end_turn("hero")
        assert not engine.data["active"] and engine.data["ended"] == "victory"
    elif key == "travel_pace":
        # r176: таблица темпа считается хостом из этих же facts (solo/exploration.py).
        from solo.exploration import leg, pace_effects, pace_table
        table = pace_table()
        assert [table[p]["feet_per_minute"] for p in ("fast", "normal", "slow")] == facts["feet_per_minute"]
        assert [table[p]["miles_per_day"] for p in ("fast", "normal", "slow")] == facts["miles_per_day"]
        assert [leg(p, 8)["miles"] for p in ("fast", "normal", "slow")] == facts["miles_per_day"]
        assert leg("fast", 6)["miles"] == 24 and leg("fast", 10)["miles"] == 38
        assert [item["skill"] for item in pace_effects("fast")] == ["survival", "perception", "stealth"]
        assert [item["skill"] for item in pace_effects("normal")] == ["stealth"]
        assert [item["mode"] for item in pace_effects("slow")] == ["advantage", "advantage"]
    elif key == "mounted_travel":
        from solo.exploration import mounted_leg
        doubled = mounted_leg("normal", 1)
        assert doubled["miles"] == 6 and doubled["double_hours"] == 1
        assert mounted_leg("normal", 3)["miles"] == 12          # 6 верхом + 2x3 мили шагом
        with pytest.raises(ValueError, match="отдых"):
            mounted_leg("normal", 2, used_hours=1)
        assert set(facts["refresh"]) == {"short_rest", "long_rest"}
    elif key == "vehicles":
        from solo.exploration import vehicle
        land = vehicle("wagon")
        assert land["kind"] == "land" and land["pace"] == facts["land_vehicle_pace"]
        assert land["miles"] == 24                              # обычный темп, 8 часов
        water = vehicle("boat", hours=30)
        assert water["pace_choice"] is False and water["hours"] == facts["max_possible_hours_per_day"]
        assert water["depends_on"] == facts["water_hours_depend_on"]
    elif key == "hiding":
        from solo.exploration import hide_conditions, hide_roll
        refused = hide_conditions()
        assert refused["allowed"] is False and "укрытие" in refused["reason"]
        assert hide_conditions(cover="half")["allowed"] is False
        allowed = hide_conditions(cover="three_quarters")
        assert allowed["allowed"] and allowed["dc"] == 15 and allowed["skill"] == "stealth"
        assert facts["action"] == "hide"
        assert hide_conditions(heavy_obscurement=True)["allowed"] is True
        assert hide_conditions(cover="total", out_of_sight=False)["allowed"] is False
        hidden = hide_roll(Hero(name="Скрытный", abilities={ability: 14 for ability in Hero(name="Ключи").abilities}),
                           cover="three_quarters", rng=Dice(15))
        assert hidden["conditions"]["dc"] == 15 and hidden["hiding_dc"] == 15 + 2
    elif key == "hidden_objects":
        from solo.exploration import search, search_area
        assert search_area("")["allowed"] is False and "не объявлена" in search_area("")["reason"]
        outside = search_area("в сундуке", "за камином")
        assert outside["allowed"] is False and facts["outside_searched_area"] == "cannot_discover_regardless_of_roll"
        area = search_area("за камином")
        assert area["allowed"] and (area["ability"], area["skill"]) == (facts["ability"], facts["skill"])
        result = search(Hero(name="Ищущий", abilities={ability: 12 for ability in Hero(name="Ключи").abilities},
                             skill_proficiencies=["perception"]), "за камином", dc=10, rng=Dice(9))
        assert result["check"]["total"] == 9 + 1 + 2 and result["success"] is True
        assert result["finds"] == facts["success"]
    elif key == "carrying_objects":
        from solo.exploration import carrying, load
        hero = Hero(name="Носильщик", abilities={**{ability: 10 for ability in Hero(name="Ключи").abilities},
                                                 "strength": 15})
        limits = carrying(hero)
        assert (limits["carry_lb"], limits["drag_lb"]) == (225, 450)
        assert load(hero, 225)["allowed"] and load(hero, 400, mode="drag")["speed_ft"] == 5
        with pytest.raises(ValueError, match="Перегруз"):
            load(hero, 226)
    elif key == "destroy_fragile_object":
        from solo.exploration import destroy
        assert destroy("стеклянную бутылку")["automatic"] is True
        assert destroy("лист бумаги")["action_cost"] == facts["action_cost"]
        with pytest.raises(ValueError, match="Магический"):
            destroy("магический амулет")
        with pytest.raises(ValueError, match="Прочный"):
            destroy("каменную стену")
    elif key == "heavy_obscurement":
        from solo.exploration import obscurement
        heavy = obscurement("heavy")
        assert heavy["vision"] == facts["vision"] and heavy["see_into"] == facts["condition_when_seeing_into"]
        assert heavy["hide_allowed"] is True and heavy["examples"] == facts["examples"]
        assert obscurement("light")["hide_allowed"] is False
        with pytest.raises(ValueError):
            obscurement("полная тьма")
    elif key == "npc_attitude":
        # r179: отношение — состояние кампании, а не слово в прозе. Сверка идёт
        # с НАПЕЧАТАННЫМ текстом книги (evidence), а не с facts этого же файла:
        # иначе проверка доказывала бы сама себя.
        from solo.social import ATTITUDES, LABELS, attitude_of, shifted, attitude_change, advantage
        import re
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        def flat(part):
            return re.sub(r"[^а-яёa-z0-9]+", " ", part.casefold())
        # Отношения перечислены в разделе «Социальное взаимодействие» (печатная
        # стр. 16 = pdf 17): «может быть Дружелюбным, Равнодушным или Враждебным».
        page16 = flat(printed.split("# Page 17", 1)[1].split("# Page 18", 1)[0])
        for stem in ("дружелюбн", "равнодушн", "враждебн"):
            assert stem in page16, (stem, "нет на стр. 16 книги")
        # Преимущество за дружелюбие напечатано в примере (печатная стр. 18 = pdf 19).
        # Извлечённый текст рвёт слова переносами («Дружелюб￾ен»), поэтому
        # здесь сравниваются только буквы: пробелы и артефакты переноса
        # выброшены, а порядок слов сохранён.
        page18 = re.sub(r"[^а-яёa-z0-9]+", "", printed.split("# Page 19", 1)[1]
                        .split("# Page 20", 1)[0].casefold())
        assert "преимуществопотомучто" in page18, \
            "печатное правило: преимущество за дружелюбие"
        assert [LABELS[name] for name in ATTITUDES] == ["Враждебное", "Равнодушное", "Дружелюбное"]
        assert len(ATTITUDES) == 3 and list(ATTITUDES) == facts["attitudes"]
        assert advantage("friendly") is True and advantage("indifferent") is False
        npc = {}
        assert attitude_of(npc) == "indifferent", "сцена не задала отношение"
        moved = attitude_change(npc, True)
        assert (moved["before"], moved["after"]) == ("indifferent", "friendly")
        assert npc["attitude"] == "friendly", "переход записан в состояние НИПа"
        assert attitude_change(npc, True)["changed"] is False, "за краем диапазона не растёт"
        back = attitude_change(npc, False)
        assert (back["before"], back["after"]) == ("friendly", "indifferent")
        assert shifted("hostile", 1) == "indifferent" and shifted("friendly", 1) == "friendly"
    elif key == "social_check":
        # r179: нужна ли кость — решает хост по состоянию НИПа (книга, стр. 16).
        from solo.game_commands import SceneAction
        from solo.scene_actions import guard
        from solo.social import check_needed, recorded_reaction
        assert check_needed({})["needed"] is True
        decided = check_needed({"reaction": "refuses"})
        assert decided["needed"] is False and decided["reaction"] == "refuses"
        assert recorded_reaction({"reaction": "maybe"}) is None, "незнакомое не догадка"
        scene = {"location": "таверна",
                 "locations": [{"name": "таверна", "npc": [dict(name="Стражник", reaction="agrees")]}]}
        with pytest.raises(ValueError, match="Проверка Влияния не нужна"):
            guard(scene, SceneAction(kind="scene_action", action_id="influence", skill="persuasion"))
    elif key == "improvised_action":
        # r179: нестандартное намерение не глотается отказом (книга, стр. 15).
        from solo.social import DECLARE, declaration_prompt
        text = declaration_prompt("Магии в этом цикле нет.")
        assert text.startswith("Магии в этом цикле нет.") and DECLARE in text
        assert declaration_prompt(text) == text, "второй раз не приписывает"
        assert "что именно делает герой" in declaration_prompt("")
    elif key == "bloodied":
        # PHB 2024, печ. стр. 27: «Если у вас половина Хитов и меньше, вы
        # становитесь Окровавленным, что само по себе не имеет эффектов».
        from solo import damage_conditions as dc
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        assert "Окровавленным" in printed and facts["standalone_effect"] == "none"
        assert facts["condition"] == "current_hp_times_two_lte_max_hp"
        assert [dc.bloodied(hp, 12) for hp in (12, 7, 6, 1, 0)] == [False, False, True, True, False]
        engine = battle()
        assert engine.damage(engine.actors["hero"], 6, "slashing")["bloodied"] is True
    elif key == "stabilization":
        # Печатное правило (стр. 29): действие Помощь, Мудрость (Медицина) Сл 10;
        # стабилизированный не бросает Спасброски от смерти, но остаётся
        # Бессознательным; урон снимает стабильность; 1 Хит спустя 1к4 часов.
        from solo import damage_conditions as dc
        import re
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        flat = re.sub(r"[^а-яёa-z0-9]+", " ", printed.casefold())
        for phrase in ("проверке мудрости медицина сл 10", "стабилизированное существо не совершает",
                       "восстанавливает 1 хит спустя 1к4 часов"):
            assert phrase in flat, phrase
        assert (facts["dc"], facts["ability"], facts["skill"]) == (10, "wisdom", "medicine")
        assert facts["regained_hp"] == 1 and facts["without_healing_hours_die"] == 4
        engine = battle(15, 2)  # проверка Медицины 15, кость 1к4 часов = 2
        ally = _ally(engine)
        hero = engine.actors["hero"]
        hero.stats.hp = 0; hero.stats.conditions = ["unconscious", "prone"]
        result = engine.stabilize(ally.id, "hero")
        assert result["stabilized"] and hero.stable and hero.stabilized_hours == 2
        assert "unconscious" in hero.stats.conditions, "стабилизация не лечит"
        assert engine.death_save(hero) is None, "стабилизированный не бросает спасброски"
        engine.damage(hero, 3, "piercing")
        assert not hero.stable and hero.death_failures == 1, "урон снимает стабильность"
    elif key == "temp_hp_duration":
        # Печатное правило (стр. 29): «существуют, пока они не закончатся или вы
        # не завершите Долгий отдых»; урон тратит их первыми.
        from solo import damage_conditions as dc
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        assert "не завершите Долгий отдых" in printed
        assert set(facts["ends"]) == {"depleted", "finish_long_rest"}
        engine = battle()
        hero = engine.actors["hero"]; hero.temp_hp = 5
        outcome = engine.damage(hero, 7, "slashing")
        assert outcome["temp_hp_absorbed"] == 5 and hero.temp_hp == 0 and hero.stats.hp == 10
        hero.temp_hp = 6
        rest = RulesetEngine(BUILTINS["dnd2024"]).rest(
            {"hero": hero.stats.model_copy(update={"temp_hp": 6}).model_dump()}, "long", rng=Dice())
        assert rest["rest"] == "long"
        with pytest.raises(ValueError):
            dc.temp_hp_grant(6, 0)
    elif key == "temp_hp_choice":
        # Печатное правило (стр. 29): «12 Временных хитов, когда у вас уже есть
        # 10 … может быть или 12, или 10, но не 22».
        from solo import damage_conditions as dc
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        assert "но не 22" in printed or "не 22 Временных" in printed
        assert facts["stack"] is False and facts["forbidden_total"] == 22
        engine = battle()
        assert engine.grant_temp_hp("hero", 10)["value"] == 10
        new = engine.grant_temp_hp("hero", 12, keep="new")
        old = engine.grant_temp_hp("hero", 12, keep="existing")
        assert (new["value"], old["value"]) == (12, 12)
        assert new["stack_forbidden"] == 22 and old["discarded"] == 12
        with pytest.raises(ValueError):
            dc.temp_hp_grant(10, 12, keep="maximum")
    elif key == "knockout":
        # Печатное правило (стр. 30): опустить Хиты до 1 и сделать Бессознательным.
        from solo import damage_conditions as dc
        printed = (V2 / "evidence/phb-008-033.txt").read_text(encoding="utf-8")
        flat = " ".join(printed.split())
        assert "уменьшить Хиты существа до 1" in flat, "печатное правило нокаута"
        assert facts["hp_after"] == 1 and facts["condition"] == "unconscious"
        assert facts["medicine_dc"] == 10
        engine = battle()
        foe = engine.actors["foe"]
        outcome = engine.damage(foe, 999, "bludgeoning", nonlethal=True)
        assert outcome["knockout"] and foe.stats.hp == 1 and not foe.dead
        assert "unconscious" in foe.stats.conditions and not foe.stable
        assert dc.knockout_applies(requested=True, ranged=True, hp_before=10, amount=10) is False
    elif key == "conditions":
        # Список состояний главы; числа — из глоссария правил, и здесь проверяется
        # только то, что движок эти числа действительно считает.
        from solo import damage_conditions as dc
        assert set(facts["conditions"]) - {"exhaustion"} == set(dc.CONDITIONS)
        assert "exhaustion" in facts["conditions"], "Истощение — отдельное число, не запись списка"
        assert all(effect.name and effect.name != name for name, effect in dc.CONDITIONS.items())
        assert dc.CONDITIONS["grappled"].speed_zero and dc.CONDITIONS["incapacitated"].blocks_actions
        assert dc.save_rule(["paralyzed"], "dexterity")["auto_fail"]
        assert dc.save_rule(["stunned"], "strength")["auto_fail"]
        assert dc.save_rule(["restrained"], "dexterity")["disadvantage"]
        assert not dc.save_rule(["prone"], "dexterity")["auto_fail"], "Сбитый с ног — про атаки"
        assert dc.CONDITIONS["unconscious"].implies == ("incapacitated", "prone")
        assert dc.CONDITIONS["paralyzed"].auto_crit_within_5ft
        assert dc.LIMITS, "что не считается — названо"



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


@pytest.mark.skipif(not (V2 / "coverage/phb-01.json").is_file(),
                    reason="карта покрытия и evidence книги не входят в поставку движка")
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
    # travel_pace и часть таблиц в поставке движка отсутствуют: корпус движка —
    # 5 файлов 01-core..05-damage (см. KNOWN_GAPS), а в приложении
    # 03-exploration.rules.json с этими строками есть. Проверяем то, что поставляется.
    facts = lambda key: BY_ID["phb2024.phb01." + key]["facts"]
    # Поставка движка — 31 строка (01-core..05-damage). Таблицы из других
    # разделов книги живут в приложении, поэтому проверяются только
    # присутствующие: отсутствие строки объявлено в KNOWN_GAPS, а не скрыто.
    def check(key, **expected):
        if "phb2024.phb01." + key not in BY_ID:
            return
        row = facts(key)
        for field, value in expected.items():
            assert row[field] == value, (key, field)
    check("difficulty", dc=[5, 10, 15, 20, 25, 30])
    check("proficiency", upper_bounds=[4, 8, 12, 16, 20, 24, 28, 30],
          bonuses=[2, 3, 4, 5, 6, 7, 8, 9])
    check("travel_pace", feet_per_minute=[400, 300, 200], miles_per_hour=[4, 3, 2],
          miles_per_day=[30, 24, 18], normal_disadvantage_skills=["stealth"])
    check("cover", ac_bonus=[2, 5, 0], dex_save_bonus=[2, 5, 0])
    check("knockout", hp_after=1)
    check("condition_duration", same_condition_durations="independent")
    check("temp_hp_choice", choice=["existing", "new"])
    check("underwater_melee", exception_damage_type="piercing")
    check("underwater_ranged", beyond_normal="automatic_miss")
    check("voluntary_save_failure", draws=0)
    check("damage_traits", example_result=22)
    check("heroic_inspiration", must_use_new=True)


# Строки, которых нет в поставке движка, не проверяются здесь: их отсутствие
# объявлено в KNOWN_GAPS, а не скрыто. Схема проверяется на живых строках.
TYPED_CASES = [
    ("ability_modifier", "offset", "10"), ("ability_modifier", "divisor", 0),
    ("proficiency", "bonuses", ["2"]*8), ("skill_check", "untrained_allowed", "true"),
    ("death_saves", "natural_20_hp", True), ("damage_traits", "resistance_divisor", 0),
]


@pytest.mark.parametrize("key,field,value", [
    case for case in TYPED_CASES if "phb2024.phb01." + case[0] in BY_ID
])
def test_typed_mechanic_payload_rejects_wrong_units_and_types(key, field, value):
    row = deepcopy(BY_ID["phb2024.phb01." + key])
    row["facts"][field] = value
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate({"schema_version": "2.1.0", "rules": [row]}, "routing.schema.json")


# Векторы корпуса не входят в поставку движка (см. KNOWN_GAPS); локальные
# обработчики выше уже проверены ручными утверждениями formal_behavior.
NEEDED = V2 / "tests/phb-01.math.cases.json"
@pytest.mark.skipif(not NEEDED.is_file(), reason="векторы корпуса отсутствуют в поставке движка")
@pytest.mark.parametrize("case", load_json(NEEDED)["cases"] if NEEDED.is_file() else [], ids=lambda case: case["id"])
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
