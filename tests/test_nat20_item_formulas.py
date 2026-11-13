"""Активация предмета с формулами: `@mod` и `@scaling` не роняют движок.

Два дефекта, найденные сплошным прогоном 706 активностей предметов:

1. Dragon Orb делегирует Cure Wounds, чей текст прибавляет `@mod`.
   `activities/cast.py` наследует способность из родительского контекста,
   но для `use_item` её никто не заполнял — `ValueError: @mod requires a
   governing ability but none was supplied`. То же у любого предмета,
   чья активность ссылается на заклинание с модификатором.
2. Hat of Many Spells: «DC 10 plus the spell's level» — формула `10 +
   @scaling`. Без названного уровня формула не решается, а движок не
   подставляет число за игрока (п.8): отказ `scaling_amount_required`
   ДО траты. С названным уровнем `@scaling` берётся из `slot_level`.
"""
import asyncio

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader

ORB = "dragon-orb"
ORB_CAST = "gU19MBO5R6a6xhIX"

HAT = "hat-of-many-spells"
HELM = "helm-of-brilliance"
HELM_SAVE = "yCKpY8iNj7IoLdHN"
MAGI = "staff-of-the-magi"
MAGI_ICE_STORM = "afXaXdDq9syhL5dh"


def test_a_delegated_spell_formula_reads_the_base_level():
    """Ice Storm через Staff of the Magi: `(@item.level - 2)d10` дробящего.

    `@item.level` — уровень, на котором предмет читает заклинание (4),
    а не выбранный уровень: апкаст через предмет — отдельный дефект данных
    (`charges_to_spend` отвергается `invalid_charge_spend`), а не повод
    подставлять чужое число.
    """
    events = _use(_wizard(MAGI), MAGI, MAGI_ICE_STORM, target_id="m1")
    assert [e.reason for e in events if e.type == "cast_failed"] == [], [e.type for e in events]
    by_type = {(e.damage_type, e.amount) for e in events if e.type == "damage_applied"}
    # 4d6 холода и 2d10 дробящего; цель провалила спасбросок — урон полный.
    assert len(by_type) == 2, by_type
    cold = next(a for t, a in by_type if t == "cold")
    blunt = next(a for t, a in by_type if t == "bludgeoning")
    assert 4 <= cold <= 24, cold
    assert 2 <= blunt <= 20, blunt


def test_an_item_damage_formula_uses_the_named_amount():
    """Helm of Brilliance: «taking Radiant damage equal to the number of gems

    in the helm» — `custom.formula` равно `@scaling`, и `enabled` у неё
    `false`: `dice.py` всё равно берёт custom-ветку, когда формула заполнена.
    На успешном спасброске урон половинный.
    """
    for gems, expected in ((7, 3), (12, 6)):
        events = _use(_wizard(HELM), HELM, HELM_SAVE, target_id="m1", pool_points=gems)
        damage = [e for e in events if e.type == "damage_applied"]
        assert len(damage) == 1, [e.type for e in events]
        assert damage[0].amount == expected, (gems, damage[0].amount)
        assert damage[0].damage_type == "radiant"


def test_an_item_damage_formula_without_the_amount_is_refused():
    events = _use(_wizard(HELM), HELM, HELM_SAVE, target_id="m1")
    assert [e.reason for e in events if e.type == "cast_failed"] == ["scaling_amount_required"]
HAT_CHECK = "DLxZ5QtUv2iqJrUl"


def _wizard(*items):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="acolyte", level=17,
        ability_scores=AbilityScores(
            strength=16, dexterity=14, constitution=16,
            intelligence=18, wisdom=16, charisma=16,
        ),
    )
    member = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=200, hp_current=100),
        loader=loader,
    )
    member = member.model_copy(update={
        "equipment": list(getattr(member, "equipment", []) or []) + list(items),
        "attuned_items": tuple(getattr(member, "attuned_items", ()) or ()) + tuple(items),
    })
    handle = asyncio.run(start_combat(
        session_id="s", party=[member],
        encounter=[EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=999, hp_max=999, ac=12, zone_id="1,0")],
        grid_scene=GridScene(width=10, height=10), rng_seed=5,
    )).handle
    drain_pending_events(handle)
    return handle


def _use(handle, item_id, activity_id, **kw):
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(
            intent_type="use_item", item_id=item_id, activity_id=activity_id, **kw
        ),
    ))
    return drain_pending_events(handle)


def test_an_items_delegated_cast_resolves_its_modifier():
    """Раньше здесь был ValueError из публичного вызова, а не событие."""
    events = _use(_wizard(ORB), ORB, ORB_CAST, target_id="pc1")
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    healing = [e for e in events if e.type == "healing_applied"]
    assert len(healing) == 1, [e.type for e in events]
    assert healing[0].amount > 0


def test_a_scaling_dc_without_a_named_amount_is_refused_by_name():
    events = _use(_wizard(HAT), HAT, HAT_CHECK)
    failures = [e for e in events if e.type == "cast_failed"]
    assert [f.reason for f in failures] == ["scaling_amount_required"]


def test_a_refused_scaling_dc_spends_no_action():
    from dnd5e_engine.orchestrator import _get_live

    handle = _wizard(HAT)
    def action():
        live = _get_live(handle)
        return next(c for c in live.initiative if c.entity_id == "pc1").action_available

    assert action() is True
    _use(handle, HAT, HAT_CHECK)
    assert action() is True


def test_a_named_spell_level_resolves_the_scaling_dc():
    """`@scaling` берётся из названного уровня: DC = 10 + 3."""
    events = _use(_wizard(HAT), HAT, HAT_CHECK, slot_level=3)
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    checks = [e for e in events if e.type == "check_rolled"]
    assert len(checks) == 1, [e.type for e in events]
    assert checks[0].dc == 13
