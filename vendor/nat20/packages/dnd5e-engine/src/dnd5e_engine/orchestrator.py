"""The combat seam — open a combat, drive it turn by turn, close it.

This module owns the engine's stateful combat loop and every piece of runtime
state behind it. Four coroutines are the whole public contract:

- ``start_combat`` — roll initiative, materialize runtime state, return a
  ``CombatHandle`` you thread through every later call.
- ``submit_player_intent`` — validate and resolve one PC intent.
- ``advance_monster_turn`` — run the built-in monster AI for one turn.
- ``end_combat`` — close the encounter and project a ``CombatOutcome``.

Each call emits typed ``CombatEvent`` objects, which
you read with ``narration_events`` (streaming) or ``drain_pending_events``
(pull). Live state is readable only through ``get_live``, which returns an
immutable ``LiveCombatView`` snapshot — the private
``_LiveCombat`` dataclass is never handed out.

How an intent resolves
----------------------
``submit_player_intent`` reads the intent's ``intent_type`` to pick an asset
reference (``weapon_id`` / ``spell_id`` / ``item_id`` / ``feature_id``), fetches
that typed entity through ``get_lib_loader``, and
walks its activities via the per-kind resolvers in
``activities``. Before resolving, it projects the actor's
current active effects and conditions into the resolution context, so passive
attack/damage/save modifiers land uniformly regardless of which game object
triggered the activity.

Scope and constraints worth knowing up front
--------------------------------------------
- **Determinism.** Every in-combat die is drawn from the ``random.Random``
  seeded by ``start_combat(rng_seed=...)``. Same seed + same intent sequence
  reproduces the same combat exactly, independent of global ``random`` state.
- **Movement is one route per intent.** A ``"move"`` intent names any cell;
  the engine walks the fewest-cells legal route there and prices the whole
  route up front.
- **Reactions are pre-armed.** The engine never pauses mid-resolution to ask a
  host "do you want to react?". A reactor arms a reaction on its own turn with
  a ``"ready"`` intent, and the engine fires it automatically when the trigger
  occurs.
- **Effects are combat-scoped.** They live in memory for the encounter and are
  discarded at ``end_combat``; persisting anything across combats is the host's
  job.

See ``docs/capabilities.md`` for the per-mechanic matrix of what is and is not
resolved today.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import random
import re
from collections import deque
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields as dataclasses_fields, is_dataclass as dataclasses_is_dataclass, replace
from typing import Any, Final, Literal, Mapping

from dnd5e_srd_data.schema.common import (
    UtilityActivity,
    ActivationBlock,
    AttackActivity,
    AttackDamageBlock,
    CastActivity,
    DamageActivity,
    DamagePartBlock,
    EnchantActivity,
    HealActivity,
    SaveActivity,
)
from dnd5e_srd_data.schema.item import ArmorCategory, Weapon, WeaponProperty
from dnd5e_srd_data.schema.monster import Monster, MonsterAction, MonsterTraitMechanic
from dnd5e_srd_data.schema.spell import (
    CastingTimeUnit,
    Spell,
    SpellComponent,
    SpellDurationUnits,
    SpellRangeUnits,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dnd5e_engine.activities.actor_stats import (
    ABILITY_CODES,
    ability_modifier_of,
    check_modifier,
    proficiency_bonus_of,
    save_modifier,
    skill_ability,
)
from dnd5e_engine.activities.attack import (
    attacker_advantage_flags,
    sneak_attack_dice,
    sneak_attack_triggers,
)
from dnd5e_engine.activities.build_context import build_activity_context, spell_attack_magnitudes
from dnd5e_engine.activities.conjuration import (
    CONJURATION_ALLOWLIST,
    CONSTRUCTS,
    ENCHANTED_WEAPON_FLAG,
    SUMMONS,
    TRANSFORM_FORM_FLAG,
    TRANSFORM_RIDERS,
    ConjurationCarrier,
    StatBlockMagnitudes,
    SummonRequest,
    SummonRollData,
    TransformSource,
    construct_attack_activity,
    enchant_ability_override,
    enchant_weapon,
    evaluate_summon_formula,
    summon_attack_count,
    summon_magnitudes,
    uses_summon_roll_data,
    wild_shape_tier,
)
from dnd5e_engine.activities.context import ActivityResolutionContext
from dnd5e_engine.activities.d20 import AdvantageSources, roll_d20_test
from dnd5e_engine.activities.dice import roll_damage_part
from dnd5e_engine.activities.effects import (
    apply_activity_effects,
    is_condition_immune,
    passive_effect_to_active_effect,
)
from dnd5e_engine.activities.forced_movement import FORCED_MOVEMENT_RIDERS
from dnd5e_engine.activities.mastery import _earns_mastery
from dnd5e_engine.activities.monster_actions import (
    expand_action_to_activities,
    expand_action_to_parts,
    multiattack_count,
    rank_monster_actions,
)
from dnd5e_engine.activities.passive_stats import (
    CombatantMovementModes,
    CombatantSenses,
    interpret_passive_stats,
)
from dnd5e_engine.activities.resolver import resolve_activity
from dnd5e_engine.activities.scale import build_scale_values, feature_owners
from dnd5e_engine.areas import (
    AreaAim,
    AreaTemplate,
    area_activity,
    area_cells,
    area_template,
    best_aim,
    creature_count,
    is_choice,
    is_harmful,
    select_affected,
)
from dnd5e_engine.death_saves import DeathSaveState, roll_death_save
from dnd5e_engine.events import (
    Ability,
    ActorMoved,
    AdvantageMode,
    AdvantageSource,
    AreaTargeted,
    AttackFailed,
    AttackRolled,
    AdjudicationRequested,
    CastFailed,
    CastFailedReason,
    CheckRolled,
    CombatantJoined,
    CombatantLeft,
    CombatantLeftReason,
    CombatantMoved,
    CombatEnded,
    CombatEvent,
    ConcentrationCheck,
    ConcentrationDropped,
    ConditionApplied,
    ConditionRemoved,
    ConditionType,
    DamageApplied,
    DashTaken,
    Death,
    EffectApplied,
    EffectExpired,
    EffectExpiryReason,
    HealingApplied,
    IntentSubmitted,
    IntentType,
    LegendaryActionUsed,
    LegendaryResistanceUsed,
    MoveFailed,
    ReactionTriggered,
    RechargeRolled,
    RoundStarted,
    SaveRolled,
    SpellCast,
    TempHpApplied,
    TurnEnded,
    TurnPhase,
    TurnStarted,
    Unconscious,
)
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.outcome import (
    CombatOutcome,
    DeathRecord,
    LootDrop,
)
from dnd5e_engine.rest import (
    FEATURE_USE_COUNTER_PREFIX,
    ITEM_USE_COUNTER_PREFIX,
    SLOTLESS_CAST_COUNTER_PREFIX,
)
from dnd5e_engine.rules.character import (
    ac_floor_from_changes,
    extra_attack_count,
    hit_die_size,
    leveled_feature_slugs,
    styles_from_feats,
)
from dnd5e_engine.rules.conditions import (
    Condition,
    active_condition_names,
    conditions_block_actions,
    conditions_grant_disadvantage_on_ability_checks,
    d20_test_penalty,
    exhaustion_level_of,
    is_condition_active,
    project_passive_check_modifiers,
    project_passive_damage_modifiers,
    project_passive_save_modifiers,
    project_speed,
)
from dnd5e_engine.rules.dice import ability_modifier, proficiency_bonus
from dnd5e_engine.rules.uses import UsesRollData, evaluate_uses_formula
from dnd5e_engine.spatial import GridTopology, SpatialTopology, cell_id, parse_cell
from dnd5e_engine.specs import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
)
from dnd5e_engine.spellcasting import (
    count_scales_with_cast_level,
    resolve_target_count,
    spell_component_metadata,
)
from dnd5e_engine.turn_lifecycle import (
    TurnLifecycle,
    run_round_start,
    run_turn_end,
    run_turn_start,
)
from dnd5e_engine.types.combat import BehaviorProfile, Combatant, MonsterActionUses, WornArmor
from dnd5e_engine.types.conditions import ActiveCondition
from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange, ActiveEffectDuration
from dnd5e_engine.activities.passive_stats import CombatantMovementModes
from dnd5e_engine.views import LiveCombatView

_LOGGER = logging.getLogger(__name__)


# ── Typed boundary-input models ─────────────────────────────────────────────
#
# PartyMemberSpec, EncounterMemberSpec, GridScene live in
# ``dnd5e_engine.specs`` (imported above). They are pure value-typed payloads
# the host passes into ``start_combat`` and have no app.* dependencies.

# SRD §Reactions — the closed set of trigger conditions the pre-armed reaction
# queue recognizes. Typed-semantics rule: a field over
# a closed set is a Literal, never bare str. These three are the exact values
# ``PlayerIntent.reaction_trigger``'s own docstring already named as its
# intended examples.
ReactionTrigger = Literal["cast_spell", "hit_by_attack", "targeted_by_magic_missile"]

# SRD 5.2 granted dice a creature spends on its own roll. One today: "That
# creature gains one of your Bardic Inspiration dice."
GrantedDie = Literal["feature_grant:bardic-inspiration"]


class PlayerIntent(BaseModel):
    """A PC's submitted intent for the current turn.

    The seam carries the union of optional asset references the intent-
    to-IR resolver consumes. The orchestrator chooses the right slot by
    ``intent_type`` (e.g. ``"attack"`` consumes ``weapon_id``;
    ``"cast_spell"`` consumes ``spell_id``; ``"use_item"`` consumes
    ``item_id``); ``feature_id`` rides alongside for class-feature
    activations (``"use_feature"``).
    """

    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    spell_id: str | None = None
    target_id: str | None = None
    # C17 — SRD 5.2 "one creature or several": per-instance targets for a spell
    # whose activity carries a ``target.affects.count`` formula (Magic Missile
    # darts, Hold Person's extra Humanoids; duplicates = several darts on one
    # creature), or the creatures an area of "up to N creatures" affects (Slow,
    # Mass Cure Wounds: at most N, no repeats). Ignored (with ``target_id`` used)
    # for activities without a count.
    target_ids: tuple[str, ...] | None = None
    # SRD 5.2 spells whose damage the CASTER types: Chromatic Orb ("choose
    # the damage type"), Sorcerous Burst, Prismatic Spray, Spirit Guardians
    # ("necrotic or radiant"), Dragons' Breath. A part listing several types
    # resolves to this choice; without it the engine used to take the first
    # silently, which is a decision the book gives the player, not the
    # engine (§5). A type outside the activity's list is refused.
    damage_type: str | None = None
    item_id: str | None = None
    weapon_id: str | None = None
    feature_id: str | None = None
    # SRD §Channel Divinity — the specific activity to resolve when a
    # USE_FEATURE names a multi-activity feature that is a repertoire of
    # ALTERNATIVES (Channel Divinity: Divine Spark Heal vs Save vs Turn Undead;
    # Cunning Strike's four options). Names one of the feature's activity ids.
    # ``None`` (the common case) leaves single-activity features unchanged and
    # keeps the safe no-op reject for a multi-activity feature (never guess).
    activity_id: str | None = None
    slot_level: int | None = None
    # Charges to spend on a variable-cost item invocation (wand upcast).
    # Validated by the use_item charge gate against consumption.scaling.
    charges_to_spend: int | None = Field(default=None, ge=1)
    # SRD 5.2 Lay on Hands: "draw power from the pool of healing to restore a
    # number of Hit Points to that creature, up to the maximum amount remaining
    # in the pool." The points a ``use_feature`` draws from an activity whose
    # own-pool cost scales by amount (Foundry ``consumption.scaling``): both its
    # ``@scaling`` value and what it spends. Omitted → 1. Refused with
    # ``CastFailed(reason="invalid_charge_spend")`` on an activity that doesn't
    # scale by amount, or above the points left. Ignored by other intent types.
    pool_points: int | None = Field(default=None, ge=1)
    # SRD 5.2 Bardic Inspiration: "Once within the next hour when the creature
    # fails a D20 Test, the creature can roll the Bardic Inspiration die and add
    # the number rolled to the d20, potentially turning the failure into a
    # success." On an ``attack``: roll the die the attacker holds if the attack
    # roll misses — a hit or a natural 1 keeps it banked. No die to roll →
    # ``AttackFailed(reason="no_granted_die")`` before anything is spent.
    # Ignored by other intent types (saves and checks carry no such choice).
    redeem_granted_die: GrantedDie | None = None
    # SRD §Reactions — the trigger condition a ``"ready"`` intent pre-arms
    # 's pending-reaction queue). Consumed by
    # ``_pop_pending_reaction`` / ``_drain_targeted_reactions`` when a
    # matching triggering intent is later submitted by any combatant.
    reaction_trigger: ReactionTrigger | None = None
    # SRD §Movement — the destination cell id (``cell_id(col, row)``) of a
    # ``"move"``; also the space a conjuration names (Spiritual Weapon's force,
    # Summon Dragon's spirit).
    target_zone_id: str | None = None
    # C16 — SRD 5.2 §Areas of Effect: a Cone / Line / Cube "extends … in a
    # direction its creator chooses". Grid offset vector ``(dcol, drow)``; only
    # the sign of each component matters (one of the 8 grid directions). When
    # omitted for a directional template the orchestrator aims from the caster
    # through ``target_id``. Ignored for sphere / cylinder and non-AoE intents.
    direction: tuple[int, int] | None = None
    # SRD 5.2 "Each creature of your choice in a 5-foot-radius Sphere" — the
    # creatures an area of your choice spares. ``None`` (the default) spares
    # the actor's allies and the actor itself when the area harms (a save or
    # damage), and its enemies when it helps; ``()`` spares nobody, so the
    # actor opts itself in. Refused with ``target_invalid`` before anything is
    # spent when an id is not in the combat, or when an attack, cast, item use
    # or feature use resolves no area of your choice. Other intents ignore it.
    excluded_target_ids: tuple[str, ...] | None = None
    # SRD §Combat — Dash / Disengage budget choice. False → Action (default).
    # True → Bonus Action: for ``dash`` and ``disengage`` only with Cunning
    # Action among the granted features (SRD 5.2 Rogue 2), else
    # ``IntentRejectedError("no_action_economy")``.
    # On an Unarmed Strike ``attack`` by an attacker whose Martial Arts is
    # active it asks for SRD 5.2's "Bonus Unarmed Strike. You can make an
    # Unarmed Strike as a Bonus Action."; without Martial Arts it changes
    # nothing there (an Attack-action swing).
    use_bonus_action: bool = False
    # SRD 5.2 Unarmed Strike — Shove: "you either push it 5 feet away or
    # cause it to have the Prone condition" — the shover's pre-declared
    # choice (no player-facing choice prompt exists at this seam). False
    # (default) -> Prone; True -> a 5-ft forced push via ``push_combatant``.
    # Duck-typed hosts that never set this field keep the default Prone
    # behaviour unaffected.
    shove_push: bool = False
    # SRD 5.2 Versatile property — "The weapon deals that damage when used
    # with two hands to make a melee attack." The attacker's pre-declared
    # grip choice for THIS attack (no player-facing choice prompt exists at
    # this seam). False (default) keeps the one-handed die. Ignored unless
    # the weapon carries ``WeaponProperty.VERSATILE`` and the attack is an
    # actual melee swing (a two-handed grip declared on a thrown/ranged use
    # of the same weapon is ignored, per SRD "to make a melee attack").
    two_handed: bool = False
    # SRD 5.2 §Rituals: "To cast a spell as a Ritual, a spellcaster must have
    # it prepared" — the Ritual version "takes 10 minutes longer to cast than
    # normal, but it doesn't expend a spell slot." The turn economy has no
    # room to host that extra 10 minutes, so in combat this flag is a hard
    # reject (``CastFailed(reason="ritual_in_combat")``, slot untouched).
    # Out-of-combat rituals resolve via ``spellcasting.resolve_ritual_cast``.
    as_ritual: bool = False
    # SRD 5.2 Wild Shape: "you shape-shift into a Beast form that you have
    # learned for this feature"; Polymorph: "That form can be any Beast you
    # choose that has a Challenge Rating equal to or less than the target's".
    # The corpus monster slug of the chosen form, for a ``use_feature`` of
    # Wild Shape or a ``cast_spell`` of Polymorph; a missing or illegal form is
    # refused with ``CastFailed(reason="invalid_form")`` before anything is
    # spent. Ignored by other intents.
    form_id: str | None = None
    # SRD 5.2 Wild Shape: "Your game statistics are replaced by the Beast's
    # stat block". An action slug on the actor's current stat block (its Beast
    # form, or a monster's own): an ``attack`` that makes one attack with that
    # action instead of a weapon. An action the stat block lacks, or one that
    # makes no attack roll, is refused with
    # ``AttackFailed(reason="action_unavailable")``. Ignored by other intents.
    stat_block_action_id: str | None = None
    #: Answers to the DM adjudications this intent depends on, keyed by the
    #: ``AdjudicationRequested.input_key`` the engine asked under (e.g.
    #: ``{"create-undead:night": True}``). An intent that needs an answer it
    #: does not carry is refused with ``AdjudicationRequested`` in the same
    #: resolution and changes nothing: the engine asks rather than assuming
    #: the circumstances the book leaves to the DM.
    adjudications: dict[str, bool] | None = None

    @field_validator("direction")
    @classmethod
    def _direction_nonzero(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and value == (0, 0):
            raise ValueError("direction must be a nonzero grid vector")
        return value

    @field_validator("excluded_target_ids")
    @classmethod
    def _excluded_ids_distinct(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and ("" in value or len(set(value)) != len(value)):
            raise ValueError("excluded_target_ids must be distinct, non-empty ids")
        return value


# ── Public handle ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CombatHandle:
    """Opaque handle to a running combat (registry key)."""

    handle_id: str


# ── Typed seam exceptions ───────────────────────────────────────────────────


class CombatSeamError(Exception):
    """Base class for typed errors raised by the public combat seam."""


class UnknownHandleError(CombatSeamError):
    """Raised when a seam call references a handle not in the registry."""


class IntentRejectedError(CombatSeamError):
    """Raised when ``submit_player_intent`` rejects an intent.

    Carries a typed ``reason`` so callers can branch on the rejection
    cause without re-parsing the error message.
    """

    RejectionReason = Literal[
        "actor_not_in_initiative",
        "not_actor_turn",
        "combat_ended",
        "no_action_economy",
        "actor_incapacitated",
        "target_invalid",
        # SRD 5.2 Unarmed Strike — Grapple/Shove: the target must be within
        # reach (5 ft). Mirrors ``CastFailedReason``'s "out_of_range" but as
        # a direct raise (like Help's "target_invalid") since neither option
        # has a dedicated ``...Failed`` event.
        "out_of_range",
        # SRD 5.2 Prone, Restricted Movement — ``stand_up`` has no dedicated
        # ``...Failed`` event (like Grapple/Help above), so its two failure
        # modes raise directly. Mirrors ``MoveFailed.reason``'s identically
        # named members (a separate Literal on the move-intent event) —
        # same SRD rule, different seam.
        "speed_zero",
        "insufficient_movement",
        # C18 §Monster action economy — ``advance_monster_turn(legendary=True)``
        # when no encounter member currently qualifies (see
        # ``_eligible_legendary_actor``).
        "no_legendary_action",
        # C18 §Monster action economy — ``resolve_legendary_resistance``
        # when ``entity_id`` is not an encounter member with the Legendary
        # Resistance trait, or has no unarmed use left in its per-day pool.
        "no_legendary_resistance",
        # SRD 5.2 Alert — Initiative Swap (``swap_initiative``): a named
        # refusal for each way the swap can be unavailable.
        "alert_swap_unavailable",
        "not_a_willing_ally",
        "alert_swap_disabled",
        "alert_swap_used",
        "unknown_combatant",
    ]

    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _weapon_attack_range_ft(weapon: Weapon | None) -> tuple[int, int] | None:
    """Resolve the effective attack range BANDS for a typed weapon, in feet.

    Returns ``(normal, max)`` — SRD 5.2 §Range: "Your attack roll has
    Disadvantage when your target is beyond normal range, and you can't
    attack a target beyond long range." A distance ``<= normal`` rolls
    plain; ``normal < distance <= max`` rolls with Disadvantage (a LEGAL
    attack — ``"range:long"`` in ``attack.py``); ``distance > max`` is
    illegal (``AttackFailed(reason="out_of_range")``).

    Reads the typed ``Weapon.range`` block (lib loader):

      * a melee weapon (``range.kind == "melee"``) WITHOUT the ``thrown``
        property reaches 5ft, or 10ft when it carries the ``reach``
        property (glaive/halberd/pike) — both bands equal, so an ordinary
        melee swing never rolls the disadvantage tier. ``range.value`` is
        NOT the melee reach — Foundry leaves it ``None`` for standard melee
        and reuses it for the THROWN range on thrown weapons (dagger=20,
        handaxe=20), so deriving reach from the ``reach`` property
        reproduces the old wrapper's ``reach_ft`` (5/10) faithfully;
      * a ranged weapon (crossbow/bow) carries ``range.value`` as its
        normal band and ``range.long`` as its max band (falling back to
        ``range.value`` when a weapon carries no distinct long band);
      * a MELEE weapon WITH the ``thrown`` property (dagger, handaxe) can
        also be thrown (SRD §Thrown): within melee reach it's an ordinary
        melee swing, and beyond reach out to ``range.value`` it's an
        ordinary (un-penalized) thrown attack, so its normal band is
        ``max(reach, range.value)``; the disadvantage tier then runs from
        there out to ``range.long`` (falling back to ``range.value`` — or
        ``reach`` if that too is absent). SRD §Thrown also pins the
        governing ability: "use the same ability modifier for the attack
        and damage rolls that you use for a melee attack with that
        weapon" — automatic here, since ``_weapon_default_ability`` keys
        off ``weapon_category``/``finesse``, not distance.

    Returns ``None`` when the weapon is missing or carries no usable
    range — the orchestrator skips the gate in that case.
    """
    if weapon is None:
        return None
    rng = weapon.range
    if rng.kind == "melee":
        reach = _weapon_melee_reach_ft(weapon)
        if WeaponProperty.THROWN not in weapon.properties:
            return reach, reach
        thrown_normal = rng.value if isinstance(rng.value, int) and rng.value > 0 else None
        if thrown_normal is None:
            return reach, reach
        thrown_band = max(reach, thrown_normal)
        thrown_long = rng.long if isinstance(rng.long, int) and rng.long > 0 else thrown_normal
        return thrown_band, max(thrown_band, thrown_long)
    ranged_normal = rng.value if isinstance(rng.value, int) and rng.value > 0 else None
    if ranged_normal is None:
        return None
    long_band = rng.long if isinstance(rng.long, int) and rng.long > 0 else ranged_normal
    return ranged_normal, max(ranged_normal, long_band)


def _weapon_melee_reach_ft(weapon: Weapon) -> int:
    """SRD 5.2 Reach property: "This weapon adds 5 feet to your reach when you
    attack with it" — 10 ft for a Reach weapon, 5 ft for any other."""
    return 10 if WeaponProperty.REACH in weapon.properties else 5


def _versatile_grip_applies(weapon: Weapon | None, distance_ft: int | None) -> bool:
    """SRD 5.2 Versatile — "The weapon deals that damage when used with two
    hands to make a melee attack."

    ``True`` only when ``weapon`` carries ``WeaponProperty.VERSATILE`` AND
    this particular swing is an actual melee attack, not a ranged one. A
    Versatile weapon is always melee-kind, but a handful (Spear, Trident)
    ALSO carry Thrown, so the same weapon can be thrown at range — reuses
    the reach-band classification from ``_weapon_attack_range_ft``: beyond
    melee reach (5ft, or 10ft with Reach) the swing is a thrown attack, and
    a two-handed grip declared for it is ignored (SRD "to make a melee
    attack"). ``distance_ft is None`` (an untracked position) is treated as
    within reach.
    """
    if weapon is None or WeaponProperty.VERSATILE not in weapon.properties:
        return False
    if WeaponProperty.THROWN in weapon.properties and distance_ft is not None:
        reach = 10 if WeaponProperty.REACH in weapon.properties else 5
        if distance_ft > reach:
            return False
    return True


def _target_beyond_normal_range_map(
    live: _LiveCombat, actor_id: str, weapon: Weapon | None, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """Per-target SRD 5.2 §Range disadvantage flag — True iff the live
    attacker→target distance is beyond the weapon's NORMAL band (but, by
    construction, never beyond its MAX band: ``_pc_attack_out_of_range``
    already rejected that case upstream, before this map is even built).
    Threaded into ``ActivityResolutionContext.target_beyond_normal_range``
    so ``attack.py`` can append the ``"range:long"`` disadvantage source
    without importing the spatial seam — mirrors ``_target_distance_map``.

    ``None`` bands (no weapon, or a weapon with no usable range) and an
    unreachable/untracked pair simply produce an empty map (the row stays
    inert), matching every other per-target sidecar in this module.
    """
    bands = _weapon_attack_range_ft(weapon)
    if bands is None:
        return {}
    normal, _max_band = bands
    caster_zone = live.actor_zone.get(actor_id)
    if caster_zone is None:
        return {}
    out: dict[str, bool] = {}
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        distance = live.topology.distance_ft(caster_zone, target_zone)
        if distance is not None:
            out[target.entity_id] = distance > normal
    return out


def _monster_attack_range_ft(activities: Sequence[Any], melee_reach_ft: int) -> int | None:
    """Resolve a monster turn's effective attack range from typed activities.

    The range gate keys off the FIRST offensive activity the turn will resolve
    (multiattack fans out to homogeneous sub-attacks, so the first activity's
    range governs the whole turn — matching the legacy single ``range_ft`` the
    loader wrapper carried). Only an explicit ``AttackActivity`` yields a
    finite reach the movement gate should honor:

      * an explicit numeric ``units == "ft"`` range (e.g. a ``"80"`` shortbow
        band) is used verbatim;
      * Foundry melee attacks ship ``units == "self"`` / no value (reach is
        implied), so they fall back to the monster's ``Combatant.melee_reach_ft``
        (5 by default, 10 for reach creatures) — reproducing the old
        ``range_ft == 5`` melee wrappers.

    A non-``AttackActivity`` offensive activity (a ``SaveActivity``)
    splits two ways:

      * a self-centered AoE (breath weapon: ``range.units == "self"`` OR a
        populated ``target.template.type``) carries NO movement reach — the
        monster resolves the save/effect from its current position, so we
        return ``None`` and the caller skips the gate (treating a self/template
        AoE as melee reach was the regression that forced dragons to close to
        5ft);
      * a ranged single-target save (giant-spider web ~60ft, mummy
        dreadful-glare ~30ft: ``range.units == "ft"`` with a real positive
        value and no measured template) is a genuine ranged gate — the monster
        must be within that range and closes the distance if it is not.

    Returns ``None`` when no offensive activity carries a usable finite reach —
    the caller then skips the movement gate (the legacy ``range_ft`` absence
    did the same).
    """
    for activity in activities:
        if not isinstance(activity, (AttackActivity, SaveActivity)):
            continue
        if not isinstance(activity, AttackActivity):
            # A non-attack offensive activity (SaveActivity). Two shapes:
            #   * self-centered AoE (breath weapon): ``range.units == "self"``
            #     OR a measured ``target.template.type`` — resolves from
            #     position, NO movement gate (return None);
            #   * ranged single-target save (giant-spider web ~60ft, mummy
            #     dreadful-glare ~30ft): ``range.units == "ft"`` with a real
            #     positive value and no measured template — a real ranged gate
            #     the monster must close to satisfy.
            rng = activity.range
            template_type = activity.target.template.type
            if rng.units == "self" or template_type:
                return None
            if rng.units == "ft" and rng.value is not None:
                try:
                    parsed = int(rng.value)
                except ValueError:
                    parsed = 0
                if parsed > 0:
                    return parsed
            return None
        rng = activity.range
        if rng.units == "ft":
            value = rng.value
            if value is not None:
                try:
                    parsed = int(value)
                except ValueError:
                    parsed = 0
                if parsed > 0:
                    return parsed
            # ``units == "ft"`` with an empty/zero value is an explicit "no
            # range" datum, not a melee attack — fall through to reach.
        # Foundry melee (``units == "self"``) or an unusable ft value: the
        # monster's reach governs.
        return melee_reach_ft if melee_reach_ft > 0 else None
    return None


def _recharge_threshold(notation: str) -> int:
    """``"5-6"`` -> 5, ``"6"`` -> 6 (the low end of the SRD 5.2 X–Y range)."""
    return int(notation.split("-", 1)[0])


def _hydrate_monster_action_uses(monster: Monster) -> dict[str, MonsterActionUses]:
    """Build the initial ``MonsterActionUses`` map for one monster template.

    One entry per action slug (in ``monster.actions`` / ``legendary_actions``)
    that carries either a ``recharge`` notation or a typed N/Day activity
    ``uses.max``. Actions with neither are omitted — nothing to track.
    """
    uses: dict[str, MonsterActionUses] = {}
    for action in (*monster.actions, *monster.legendary_actions):
        entry = MonsterActionUses()
        for activity in action.activities:
            max_raw = activity.uses.max
            if max_raw.strip().isdigit():
                entry.uses_remaining[f"{action.slug}:{activity.id}"] = int(max_raw)
        if action.recharge or entry.uses_remaining:
            uses[action.slug] = entry
    return uses


def _legendary_resistance_max(monster: Monster) -> int:
    """SRD 5.2 Legendary Resistance's "N/Day" pool size.

    Prefers the typed ``Monster.legendary_resistance_uses`` (the dataset's
    translation of Foundry ``system.resources.legres.max`` — 3 for most
    bundled bearers, 4 for the non-gold ancient dragons, Kraken, Lich, Pit
    Fiend and Solar, 6 for the Tarrasque); then a typed ``uses_per_day`` on
    the trait; then a ``"N/Day"`` match in the trait's name; and only when
    none is present (a host-supplied template without the typed count)
    defaults to 3. ``0`` when the monster has no Legendary Resistance trait.
    """
    for trait in monster.special_abilities:
        if trait.mechanic == MonsterTraitMechanic.LEGENDARY_RESISTANCE:
            if monster.legendary_resistance_uses:
                return monster.legendary_resistance_uses
            if trait.uses_per_day:
                return int(trait.uses_per_day)
            match = re.search(r"(\d+)/Day", trait.name)
            return int(match.group(1)) if match else 3
    return 0


def _sync_legendary_resistance(live: _LiveCombat, pre_event_count: int) -> None:
    """C18 §Monster action economy — reconcile Legendary Resistance state
    after a resolution that may have rolled a save.

    Scans ``live.event_log[pre_event_count:]`` for ``LegendaryResistanceUsed``
    (the "read the events" pattern the reaction drains use) and, for each
    one not yet processed, decrements the AUTHORITATIVE
    ``Combatant.legendary_resistances_remaining`` and
    ``live.legendary_resistance_armed`` — the per-resolution hydration
    payload the primary conversion (``save_primitive.roll_save``) actually
    mutated was a disposable COPY (``_build_hydration_payload``), so this is
    the one place those two canonical stores are written down for THAT path.

    Idempotent: every processed event's log index is recorded in
    ``live.legendary_resistance_applied_event_indices`` and never processed
    again. The orchestrator-level save paths
    (``_emit_legendary_resistance_used``) record their index at emit time —
    they already decremented both stores directly — so a scan window that
    wraps one of them (the concentration check fires from INSIDE
    ``resolve_activity``) skips it, and two overlapping windows can never
    double-decrement either. The set is bounded by the combat's total
    Legendary Resistance uses.

    A no-op when the resolution converted nothing (the common case — every
    currently-legal combat is byte-identical).
    """
    for index, ev in enumerate(live.event_log[pre_event_count:], start=pre_event_count):
        if not isinstance(ev, LegendaryResistanceUsed):
            continue
        if index in live.legendary_resistance_applied_event_indices:
            continue
        live.legendary_resistance_applied_event_indices.add(index)
        armed = live.legendary_resistance_armed.get(ev.actor_id, 0)
        if armed > 0:
            live.legendary_resistance_armed[ev.actor_id] = armed - 1
        bearer = _find_combatant(live, ev.actor_id)
        if bearer is not None:
            bearer.legendary_resistances_remaining = ev.uses_remaining


def _consume_armed_legendary_resistance(live: _LiveCombat, target: Combatant) -> int | None:
    """SRD 5.2 Legendary Resistance ("If the monster fails a saving throw, it
    can choose to succeed instead") for the orchestrator-level save rolls
    that bypass the typed activity resolver entirely (the end-of-turn repeat
    save, the concentration check, and the Grapple/Shove unarmed-option save)
    — ``activities/save_primitive.roll_save`` never runs for any of these, so
    its conversion never sees them. Death saves are OUT OF SCOPE by design —
    a death save has no DC/pass-fail shape this gate applies to.

    Mirrors the primary conversion's gate exactly: a no-op (``None``) unless
    ``target`` has an armed use (``live.legendary_resistance_armed``) AND at
    least one use remains (``Combatant.legendary_resistances_remaining``). On
    a hit, decrements both AUTHORITATIVE stores directly (no disposable
    hydration copy is in play on these paths) and returns the NEW remaining
    count. It emits nothing: the caller flips its own ``succeeded`` flag,
    emits its roll (a ``SaveRolled``, or the concentration check's
    ``ConcentrationCheck``) with the converted outcome, THEN calls
    ``_emit_legendary_resistance_used`` — the event's documented "after the
    roll it converts" order. Draws no dice.
    """
    armed = live.legendary_resistance_armed.get(target.entity_id, 0)
    if armed <= 0 or target.legendary_resistances_remaining <= 0:
        return None
    live.legendary_resistance_armed[target.entity_id] = armed - 1
    target.legendary_resistances_remaining -= 1
    return target.legendary_resistances_remaining


def _emit_legendary_resistance_used(live: _LiveCombat, target_id: str, uses_remaining: int) -> None:
    """Emit the ``LegendaryResistanceUsed`` for a conversion
    ``_consume_armed_legendary_resistance`` already applied, and record its
    log index in ``live.legendary_resistance_applied_event_indices`` so an
    enclosing ``_sync_legendary_resistance`` window (the concentration check
    fires from INSIDE ``resolve_activity``) never decrements it a second
    time. Callers invoke this AFTER their own ``SaveRolled`` /
    ``ConcentrationCheck``.
    """
    _emit(live, LegendaryResistanceUsed(actor_id=target_id, uses_remaining=uses_remaining))
    live.legendary_resistance_applied_event_indices.add(len(live.event_log) - 1)


def _legendary_action_uses_max(monster: Monster) -> int:
    """SRD 5.2 Legendary Action Uses pool size.

    Prefers the typed ``Monster.legendary_action_uses`` (Foundry
    ``system.resources.legact.max`` — 3 for every bundled legendary-action
    monster); a stat block with a non-empty ``legendary_actions`` list but
    no typed count (a host-supplied template) falls back to 3. ``0`` when
    the monster has no legendary actions.
    """
    if not monster.legendary_actions:
        return 0
    return monster.legendary_action_uses or 3


def _run_monster_turn_start(live: _LiveCombat, current: Combatant) -> None:
    """SRD 5.2 "at the start of each of its turns" monster mechanics.

    Runs once per driven monster turn (idempotent on
    ``(round_number, entity_id)``): legendary-action pool reset,
    recharge rolls, then regeneration — in that fixed order. Lives here
    rather than as a ``turn_lifecycle`` ``turn_start`` hook because the
    engine emits ``TurnStarted`` at the PREVIOUS turn's end, before the
    legendary-action window a host drives afterwards; running
    from a ``turn_start`` hook would fire these too early relative to that
    window. Incapacitated and fleeing monsters still run all three — the SRD
    ties them to "the start of its turn", not to whether it acts. A DEAD
    monster (in ``live.dead_ids``, not alive, or at 0 HP) has no turn start
    at all: nothing runs, no recharge die is drawn and no ``RechargeRolled``
    is emitted for a corpse.
    """
    key = (live.round_number, current.entity_id)
    if live.monster_turn_start_done == key:
        return
    live.monster_turn_start_done = key
    if current.entity_id in live.dead_ids or not current.is_alive or current.hp_current <= 0:
        return
    if current.legendary_actions_max:
        current.legendary_actions_remaining = current.legendary_actions_max
    slug = live.monster_slug_by_entity.get(current.entity_id)
    monster = get_lib_loader().get_monster(slug) if slug else None
    if monster is None:
        return
    _roll_recharges(live, current, monster)
    _apply_regeneration(live, current, monster)


def _roll_recharges(live: _LiveCombat, current: Combatant, monster: Monster) -> None:
    """SRD 5.2 "Recharge X–Y": roll 1d6 for each SPENT recharge action.

    Only actions whose tracked ``MonsterActionUses.recharge_spent`` is True
    draw a die — an unspent part is never rolled for (Foundry parity).
    """
    uses = live.monster_action_uses_by_entity.get(current.entity_id, {})
    for action in monster.actions:
        entry = uses.get(action.slug)
        if not action.recharge or entry is None or not entry.recharge_spent:
            continue
        roll = live.rng.randint(1, 6)
        succeeded = roll >= _recharge_threshold(action.recharge)
        if succeeded:
            entry.recharge_spent = False
        _emit(
            live,
            RechargeRolled(
                monster_id=current.entity_id,
                action_slug=action.slug,
                roll=roll,
                threshold=action.recharge,
                succeeded=succeeded,
            ),
        )


def _apply_regeneration(live: _LiveCombat, current: Combatant, monster: Monster) -> None:
    """SRD 5.2 Regeneration: "regains N Hit Points at the start of each of
    its turns if it has at least 1 Hit Point," capped at ``hp_max``.

    Reuses the ``HealingApplied`` emission path (``_emit`` ->
    ``_emit_apply_healing``) for the tracked-HP write instead of a second HP
    updater; emits nothing when the trait isn't present, the monster is at
    or below 0 HP, or it is already at full HP (no heal actually occurs).
    """
    if MonsterTraitMechanic.REGENERATION not in current.trait_mechanics:
        return
    if not current.is_alive or current.hp_current < 1:
        return
    if current.hp_current >= current.hp_max:
        return
    # C18 §Monster action economy — SRD 5.2 stat-block trait "Swarm": "The
    # swarm can't regain Hit Points or gain Temporary Hit Points." A
    # Regeneration bearer that is ALSO a Swarm (no bundled monster combines
    # the two today) never heals from either trait.
    if MonsterTraitMechanic.SWARM in current.trait_mechanics:
        _LOGGER.info("swarm_no_hp_gain target_id=%s", current.entity_id)
        return
    for trait in monster.special_abilities:
        if trait.mechanic != MonsterTraitMechanic.REGENERATION:
            continue
        for activity in trait.activities:
            if not isinstance(activity, HealActivity):
                continue
            amount = roll_damage_part(activity.healing, live.rng)
            healed = min(amount, current.hp_max - current.hp_current)
            if healed <= 0:
                return
            _emit(live, HealingApplied(target_id=current.entity_id, amount=healed))
            return


#: The effect id ``passive_effect_to_active_effect`` gives the corpus effect
#: named "Turned" (Channel Divinity: Turn Undead), sourced at
#: ``canonical/features/channel-divinity-cleric.json``.
_TURNED_EFFECT_ID: Final = "effect:turned"


def _is_turned(live: _LiveCombat, entity_id: str) -> bool:
    """True while ``entity_id`` carries a live "Turned" effect — SRD 5.2
    Turn Undead: "it must spend its turns trying to move as far away from you
    as it can". The effect lasts a minute (``duration.seconds`` 60, ten
    rounds at the caster's turn end), so the forced flight ends with it.
    """
    return any(
        e.id == _TURNED_EFFECT_ID for e in live.active_effects.get(entity_id, [])
    )


def _monster_is_fleeing(monster: Combatant) -> bool:
    """Replicate the legacy behavior-based flee / low-HP gate.

    Faithful port of ``monster_ai.select_monster_action`` (monster_ai.py:163-171):
    an AGGRESSIVE monster passes below 10% HP, a RANGED one below 25%; DEFENSIVE
    monsters never flee. The typed selector (``select_typed_monster_action``)
    takes only the static ``Monster`` and so lost access to live HP — this gate
    re-applies it against the runtime ``Combatant`` before selection.
    """
    try:
        profile = BehaviorProfile(monster.behavior_profile)
    except ValueError:
        profile = BehaviorProfile.AGGRESSIVE
    hp_ratio = monster.hp_current / monster.hp_max if monster.hp_max > 0 else 0.0
    flee_threshold = 0.25 if profile == BehaviorProfile.RANGED else 0.10
    return profile != BehaviorProfile.DEFENSIVE and hp_ratio < flee_threshold


def _in_range_with_los(topology: SpatialTopology, a: str, b: str, range_ft: int) -> bool:
    """True iff ``b`` is within ``range_ft`` of ``a``, ``a`` has line of sight to
    ``b``, AND ``b`` does not have total cover from ``a``.

    The single range+LoS+cover predicate every attack/cast gate routes
    through. SRD 5.2 §Cover: a target with total cover "can't be targeted
    directly" — reuses the same rejection surface (``AttackFailed(reason=
    "out_of_range")``) every other range/LoS rejection already uses. On a
    backend/scene with no wall or cover geometry, ``has_line_of_sight`` is
    always True and ``cover_between`` is always ``"none"``, so this is
    behaviour-identical to a bare ``within_range`` (byte-for-byte preserved).
    """
    return (
        topology.within_range(a, b, range_ft)
        and topology.has_line_of_sight(a, b)
        and topology.cover_between(a, b) != "total"
    )


def _occupied_cells(live: _LiveCombat, *, exclude: Collection[str]) -> set[str]:
    """Cells currently occupied by alive combatants other than ``exclude``
    (entity ids). SRD 5.2 §Cover — "another creature" is a half-cover source;
    §Moving Around Other Creatures — an enemy's space blocks movement."""
    excluded = set(exclude)
    return {
        zone
        for c in live.initiative
        if c.is_alive
        and c.entity_id not in live.dead_ids
        and c.entity_id not in excluded
        and (zone := live.actor_zone.get(c.entity_id)) is not None
    }


def _target_cover_map(
    live: _LiveCombat,
    caster_id: str,
    targets: Sequence[Combatant],
    *,
    origin_cell: str | None = None,
) -> dict[str, str]:
    """SRD 5.2 §Cover — per-target cover degree between the point of origin and
    each target, from scene geometry AND creature occupancy (every other alive
    combatant's cell grants half cover when it lies on the line; ally or enemy
    makes no difference).

    The origin is ``caster_id``'s own cell for an attack or a single-target
    cast. For an AREA of effect the SRD measures cover from the area's point of
    origin instead — a Fireball centred forty feet away shields its victims
    from the BURST POINT, not from the wizard — so an area passes its resolved
    point of origin as ``origin_cell`` (see ``_area_cover_origin``).
    When ``origin_cell`` is given the caster is no longer assumed to stand on
    the origin, so it is no longer excluded from the occupancy sweep: a caster
    standing between the burst point and a victim grants that victim half cover
    like any other creature. ``cover_between`` skips the origin cell itself, so
    a self-origin template (cone, Thunderwave's cube) is unaffected.

    DEGENERATE CASE — the burst point coincides with the one creature it
    affects (a small sphere centred on a lone target, e.g. Acid Splash cast
    at a single foe): ``origin == target_zone``, so ``cover_between``'s line
    walk (which always excludes its own ``a`` endpoint) would see nothing at
    all and silently drop a cover tag sitting on that shared cell. SRD 5.2
    §Cover ("an object that covers at least half of the target") still
    shields a creature standing in or behind an obstruction in its own
    space, so this case reads the tag directly off that cell
    (``cover_on_cell``) instead of walking a line to itself. A genuine
    multi-target burst (the origin differs from at least one victim's cell)
    is unaffected — each OTHER target still measures cover from the true
    burst point via the ordinary line walk.

    Threaded into ``ActivityResolutionContext.target_cover`` so
    ``activities/attack.py`` (AC) and ``activities/save_primitive.py``
    (Dexterity saves) can fold the SRD +2/+5 bonus without either resolver
    importing the spatial seam directly. A caster or target with no tracked
    cell contributes no entry (no cover).
    """
    origin = origin_cell if origin_cell is not None else live.actor_zone.get(caster_id)
    if origin is None:
        return {}
    out: dict[str, str] = {}
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        if origin_cell is not None and origin == target_zone:
            out[target.entity_id] = live.topology.cover_on_cell(target_zone)
            continue
        exclude = (target.entity_id,) if origin_cell is not None else (caster_id, target.entity_id)
        occupied = _occupied_cells(live, exclude=exclude)
        out[target.entity_id] = live.topology.cover_between(origin, target_zone, occupied)
    return out


def _target_visibility_maps(
    live: _LiveCombat, caster: Combatant, targets: Sequence[Combatant]
) -> tuple[dict[str, bool], dict[str, bool]]:
    """SRD 5.2 "Unseen Attackers and Targets" — per target, (attacker cannot
    see target, target cannot see attacker), from ``SpatialTopology.can_see``
    with each viewer's own ``Combatant.senses``. Untracked positions ⇒ seen.

    Threaded into ``ActivityResolutionContext.target_unseen`` /
    ``.attacker_unseen_by`` so ``activities/attack.py`` can add the ``"unseen"``
    disadvantage / advantage source without importing the spatial seam. A
    scene with no lighting data yields all-False maps ⇒ ``normal``.
    """
    caster_zone = live.actor_zone.get(caster.entity_id)
    target_unseen: dict[str, bool] = {}
    attacker_unseen_by: dict[str, bool] = {}
    if caster_zone is None:
        return target_unseen, attacker_unseen_by
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        target_unseen[target.entity_id] = not live.topology.can_see(
            caster_zone, target_zone, caster.senses
        )
        attacker_unseen_by[target.entity_id] = not live.topology.can_see(
            target_zone, caster_zone, target.senses
        )
    return target_unseen, attacker_unseen_by


def _target_distance_map(
    live: _LiveCombat, caster_id: str, targets: Sequence[Combatant]
) -> dict[str, int]:
    """Per-target attacker→target distance in feet (``SpatialTopology.distance_ft``),
    threaded into ``ActivityResolutionContext.target_distance_ft`` for the
    distance-aware condition rows (SRD 5.2 Prone). A participant without a
    tracked position, or an unreachable pair, is simply absent (the rows stay
    inert) — mirrors ``_target_cover_map``.
    """
    caster_zone = live.actor_zone.get(caster_id)
    if caster_zone is None:
        return {}
    out: dict[str, int] = {}
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        distance = live.topology.distance_ft(caster_zone, target_zone)
        if distance is not None:
            out[target.entity_id] = distance
    return out


def _side_of(live: _LiveCombat, entity_id: str) -> set[str] | None:
    """The side (``live.party_ids`` or ``live.encounter_ids``) ``entity_id``
    belongs to, or ``None`` for an unregistered id. A summon fights on its
    owner's side without joining its side set (C21). SRD 5.2 Summon Dragon:
    "The creature is an ally to you and your allies."
    """
    summon = live.summons.get(entity_id)
    if summon is not None:
        return _side_of(live, summon.owner_id)
    if entity_id in live.party_ids:
        return live.party_ids
    if entity_id in live.encounter_ids:
        return live.encounter_ids
    return None


def _allied_ids(live: _LiveCombat, entity_id: str) -> set[str]:
    """Every combatant in the initiative order on ``entity_id``'s side, itself
    included: its side set's members and the summons they own (C21). Empty
    for an unregistered id. Without summons it is exactly the side set."""
    side = _side_of(live, entity_id)
    if side is None:
        return set()
    return {c.entity_id for c in live.initiative if _side_of(live, c.entity_id) is side}


def _is_enemy(live: _LiveCombat, entity_id: str, other_id: str) -> bool:
    """``other_id`` fights on the other side from ``entity_id``
    (``live.party_ids`` / ``live.encounter_ids``)."""
    side = _side_of(live, entity_id)
    other_side = _side_of(live, other_id)
    return side is not None and other_side is not None and other_side is not side


def _target_help_advantage_map(
    live: _LiveCombat, attacker_id: str, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """SRD 5.2 §Actions in Combat — Help, Assist an Attack Roll:
    per-TARGET, does an outstanding ``live.help_grants`` entry against this
    target belong to an ALLY of ``attacker_id`` (same side: both in
    ``party_ids`` or both in ``encounter_ids``, or a summon of that side —
    ``_allied_ids``)? ``target == attacker_id``
    never qualifies — Help assists an ALLY's attack roll, not the
    helped-against target's own. Threaded into
    ``ActivityResolutionContext.target_help_advantage``; the one-use pop
    happens after resolution (``_pop_help_grant``) — this is a read-only
    projection.
    """
    allies = _allied_ids(live, attacker_id)
    if not allies:
        return {}
    out: dict[str, bool] = {}
    for target in targets:
        if target.entity_id == attacker_id:
            continue
        helpers = live.help_grants.get(target.entity_id)
        if helpers and any(h in allies for h in helpers):
            out[target.entity_id] = True
    return out


def _pop_help_grant(
    live: _LiveCombat, attacker_id: str, targets: Sequence[Combatant], pre_event_count: int
) -> None:
    """One-use pop for a consumed Help grant (SRD 5.2 "giving Advantage to
    the NEXT attack roll"). For each target ``attacker_id`` attacked this
    resolution under an ally-of-a-helper grant, if an ``AttackRolled``
    against that target by ``attacker_id`` with ``"help"`` among its
    sources landed in this resolution's event slice, remove exactly ONE
    matching helper from that target's grant list.

    Consumed even on a miss, and even when the advantage was cancelled to
    normal by a disadvantage source: ``roll_d20_test`` folds every
    ``AdvantageSources.advantage`` entry into ``AttackRolled.sources``
    regardless of the resolved mode, so "help" is present whenever the
    grant applied to the roll at all — the SRD reading is "the next attack
    roll" (singular, unconditional), not "the next attack roll that
    resolves with advantage".
    """
    allies = _allied_ids(live, attacker_id)
    if not allies:
        return
    recent = live.event_log[pre_event_count:]
    for target in targets:
        helpers = live.help_grants.get(target.entity_id)
        if not helpers:
            continue
        fired = any(
            isinstance(ev, AttackRolled)
            and ev.target_id == target.entity_id
            and ev.attacker_id == attacker_id
            and "help" in ev.sources
            for ev in recent
        )
        if not fired:
            continue
        for idx, helper_id in enumerate(helpers):
            if helper_id in allies:
                del helpers[idx]
                break
        if not helpers:
            del live.help_grants[target.entity_id]


def _attacker_vex_advantage_map(
    live: _LiveCombat, attacker_id: str, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """SRD 5.2 §Weapon Mastery — Vex: per-TARGET, does
    ``attacker_id`` hold a live Vex grant against that target
    (``live.vex_grants[attacker_id]``)? Threaded into
    ``ActivityResolutionContext.attacker_vex_advantage``; the one-use pop
    happens after resolution (``_pop_vex_grants``) — this is a read-only
    projection, mirrors ``_target_help_advantage_map``.
    """
    grants = live.vex_grants.get(attacker_id)
    if not grants:
        return {}
    return {t.entity_id: True for t in targets if t.entity_id in grants}


def _pop_vex_grants(
    live: _LiveCombat, attacker_id: str, targets: Sequence[Combatant], pre_event_count: int
) -> None:
    """One-use pop for a consumed Vex grant (SRD "your NEXT attack roll
    against that creature"). For each target ``attacker_id`` held a live
    grant against, if an ``AttackRolled`` by ``attacker_id`` against that
    target landed in this resolution's event slice, the grant is spent
    (removed) regardless of hit/miss/cancellation — mirrors
    ``_pop_help_grant``.
    """
    grants = live.vex_grants.get(attacker_id)
    if not grants:
        return
    recent = live.event_log[pre_event_count:]
    for target in targets:
        if target.entity_id not in grants:
            continue
        fired = any(
            isinstance(ev, AttackRolled)
            and ev.attacker_id == attacker_id
            and ev.target_id == target.entity_id
            for ev in recent
        )
        if fired:
            del grants[target.entity_id]
    if not grants:
        del live.vex_grants[attacker_id]


def _pop_sap_mark(live: _LiveCombat, attacker_id: str, pre_event_count: int) -> None:
    """One-use pop for a consumed Sap mark (SRD "its NEXT attack roll").

    If ``attacker_id`` (the potentially-sapped creature) rolled ANY attack
    in this resolution's event slice, its outstanding mark is spent —
    mirrors ``_pop_help_grant`` / ``_pop_vex_grants``.
    """
    if attacker_id not in live.sap_marks:
        return
    recent = live.event_log[pre_event_count:]
    fired = any(isinstance(ev, AttackRolled) and ev.attacker_id == attacker_id for ev in recent)
    if fired:
        del live.sap_marks[attacker_id]


def _consume_attack_roll_grants(
    live: _LiveCombat,
    current: Combatant,
    target_list: list[Combatant],
    pre_event_count: int,
) -> None:
    """SRD 5.2 §Actions in Combat — Help; §Weapon Mastery — Vex / Sap: pop
    the one-use grants an ATTACK ROLL this resolution may have consumed.

    Shared by the mundane monster-attack site and a monster stat-block cast
    whose resolved spell includes an ``AttackActivity``, so the two
    branches cannot drift apart. A save-only resolution
    must never call this: the three pop helpers only remove a grant when an
    ``AttackRolled`` by ``current`` actually fired in this resolution's
    event slice, so calling them here is a correctness-relevant gate, not
    a redundant no-op.

    Mastery-proc FOLDING (``_fold_mastery_procs``) is deliberately NOT
    included here — it needs the resolved ``ActivityResolutionContext`` and
    only ever matters for an actual ``Weapon`` swing, which neither a
    monster attack nor a monster cast ever has; callers fold it separately.
    """
    _pop_help_grant(live, current.entity_id, target_list, pre_event_count)
    _pop_vex_grants(live, current.entity_id, target_list, pre_event_count)
    _pop_sap_mark(live, current.entity_id, pre_event_count)


def _fold_mastery_procs(
    live: _LiveCombat, attacker_id: str, ctx: ActivityResolutionContext
) -> None:
    """SRD 5.2 §Weapon Mastery — proc writeback. Fold
    every ``(mastery_slug, target_id)`` the pure resolver
    appended to ``ctx.mastery_procs`` this resolution into live combat
    state:

    * ``"vex"`` -> ``live.vex_grants[attacker_id][target_id] = 2, or 1 for a
      proc on another creature's turn`` ("before the end of your NEXT turn"
      — decremented at the attacker's own turn end, see
      ``_end_turn_and_advance``).
    * ``"sap"`` -> ``live.sap_marks[target_id] = attacker_id`` ("before the
      start of your next turn" — cleared at the attacker's own next
      ``TurnStarted``, see ``_emit_apply_turn_started``).
    * ``"slow"`` -> ``live.slow_marks[target_id] |= {attacker_id}``
      then ``_clamp_movement_budget`` on the target (a slowed creature
      mid-turn loses budget above its new cap). Flat -10 ft while ANY mark
      is outstanding — never stacks (SRD "doesn't exceed 10 feet").
    * ``"push"`` -> ``push_combatant(live, target_id, <attacker's
      cell>, 10)`` — always the full 10 ft straight away from the attacker,
      a no-op when the attacker has no tracked
      cell or the target is boxed in.
    * ``"cleave"`` -> the chain FIRED marker: flip the attacker's
      ``cleave_spent_this_turn`` ("only once per turn"). Not a target
      effect — ``attack.py`` already resolved the chained roll.

    Non-stacking: a re-proc REFRESHES the grant/mark (overwrite, not
    append/increment).
    """
    attacker_cell = live.actor_zone.get(attacker_id)
    for mastery_slug, target_id in ctx.mastery_procs:
        if mastery_slug == "vex":
            # "before the end of your next turn": on the attacker's own turn
            # that is two of its turn ends; on another creature's turn (an
            # opportunity attack) only its next one.
            live.vex_grants.setdefault(attacker_id, {})[target_id] = (
                2 if live.current_actor_id == attacker_id else 1
            )
        elif mastery_slug == "sap":
            live.sap_marks[target_id] = attacker_id
        elif mastery_slug == "slow":
            live.slow_marks.setdefault(target_id, set()).add(attacker_id)
            _clamp_movement_budget(live, target_id)
        elif mastery_slug == "push":
            if attacker_cell is not None:
                push_combatant(live, target_id, attacker_cell, 10)
        elif mastery_slug == "cleave":
            _set_cleave_spent(live, attacker_id)


def _set_cleave_spent(live: _LiveCombat, actor_id: str) -> None:
    """SRD 5.2 §Weapon Mastery — Cleave: "You can make this extra attack only
    once per turn." Flip the actor's per-turn cap via the standard
    initiative-list model_copy write (reset at the actor's own TurnStarted).
    """
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={"cleave_spent_this_turn": True})
            break


def _cleave_candidate(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon | None, targets: Sequence[Combatant]
) -> Combatant | None:
    """SRD 5.2 §Weapon Mastery — Cleave: the deterministic
    "second creature within 5 feet of the first that is also within your
    reach": a LIVING hostile (opposite side of
    ``attacker``) other than the first target, within 5 ft of the FIRST
    target AND within the attacker's melee reach (5 ft, or 10 ft with the
    Reach property); among several, the one NEAREST TO THE ATTACKER, ties
    broken by ascending ``entity_id``. ``None`` when the weapon is not a
    cleave weapon, positions are untracked, or nothing qualifies. A pure
    spatial read owned here so ``attack.py`` never touches the seam.
    """
    if weapon is None or weapon.mastery != "cleave" or not targets:
        return None
    allies = _allied_ids(live, attacker.entity_id)
    attacker_zone = live.actor_zone.get(attacker.entity_id)
    first = targets[0]
    first_zone = live.actor_zone.get(first.entity_id)
    if not allies or attacker_zone is None or first_zone is None:
        return None
    reach = 10 if WeaponProperty.REACH in weapon.properties else 5
    # F4 — exclude EVERY primary target's id, not just ``first``'s: a
    # multi-target swing (should one ever exist) must never offer a
    # creature that is already being attacked directly as its own cleave
    # chain candidate.
    primary_target_ids = {t.entity_id for t in targets}
    best: tuple[int, str, Combatant] | None = None
    for other in live.initiative:
        if (
            other.entity_id in primary_target_ids
            or other.entity_id in allies
            or other.entity_id in live.dead_ids
            or not other.is_alive
        ):
            continue
        other_zone = live.actor_zone.get(other.entity_id)
        if other_zone is None or not live.topology.within_range(first_zone, other_zone, 5):
            continue
        distance = live.topology.distance_ft(attacker_zone, other_zone)
        if distance is None or distance > reach:
            continue
        if best is None or (distance, other.entity_id) < best[:2]:
            best = (distance, other.entity_id, other)
    return None if best is None else best[2]


def _cleave_available(
    current: Combatant, weapon: Weapon | None, main_target_distance_ft: int | None
) -> bool:
    """SRD 5.2 §Weapon Mastery — Cleave gate: the swung weapon carries
    ``cleave``, the attacker has not already cleaved this turn, and the
    swing is a MELEE attack within reach ("hit a creature with a melee attack
    roll"). A cleave weapon is never Thrown in the corpus, but the reach
    check keeps a hypothetical thrown use from chaining. ``None`` distance
    (untracked) counts as within reach, mirroring ``_versatile_grip_applies``.
    """
    if weapon is None or weapon.mastery != "cleave" or current.cleave_spent_this_turn:
        return False
    if weapon.weapon_category in {"simple_ranged", "martial_ranged"}:
        return False
    reach = 10 if WeaponProperty.REACH in weapon.properties else 5
    return main_target_distance_ft is None or main_target_distance_ft <= reach


#: ``ActiveEffect.origin`` prefixes whose THIRD ``:``-segment is the entity that
#: created the effect (``cast:<slug>:<caster_id>`` is the shipped convention;
#: ``grapple:<slug>:<grappler_id>`` is reserved for C14's grapple contest).
_ENTITY_ORIGIN_PREFIXES: frozenset[str] = frozenset({"cast", "grapple"})


def _condition_source_entity(live: _LiveCombat, combatant: Combatant, condition: str) -> str | None:
    """Who imposed ``condition`` on ``combatant`` (the grappler, the charmer)?

    Resolution order: an ``ActiveCondition.source_entity_id`` that is a real
    entity id (not an ``implied:*`` marker); else the imposing ``ActiveEffect``
    (via ``source_effect_id``) whose ``origin`` encodes its creator. ``None``
    when unknown — every consumer treats unknown as "no restriction".
    """
    for ac in combatant.conditions:
        if ac.condition != condition:
            continue
        if not ac.source_entity_id.startswith("implied:"):
            return ac.source_entity_id
        if ac.source_effect_id is None:
            continue
        for eff in live.active_effects.get(combatant.entity_id, []):
            if eff.id != ac.source_effect_id:
                continue
            parts = (eff.origin or "").split(":", 2)
            if len(parts) == 3 and parts[0] in _ENTITY_ORIGIN_PREFIXES and parts[2]:
                return parts[2]
    return None


def _sneak_ally_adjacent_map(
    live: _LiveCombat, caster: Combatant, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """SRD §Sneak Attack (Rogue), ally-adjacent alternative — per target, is at
    least one of the caster's allies within 5 ft of that target and NOT
    Incapacitated?

    A new CONSUMER of the ``spatial.py`` distance seam (``within_range`` at 5
    ft), NOT a new spatial primitive. "Ally" = a living combatant on the
    caster's own side (party vs encounter; a summon is on its owner's —
    ``_allied_ids``) other than the caster. The
    Incapacitated read uses the SRD condition-implication chain (Paralyzed /
    Stunned / Petrified / Unconscious all imply Incapacitated). Threaded into
    ``ActivityResolutionContext.sneak_attack_ally_adjacent`` so the pure
    resolver never touches the spatial seam. Absent cell data for the caster's
    side, a target, or every ally contributes no entry (⇒ no adjacent ally).
    """
    side = _allied_ids(live, caster.entity_id)
    if not side:
        return {}
    allies = [
        c
        for c in live.initiative
        if c.entity_id in side
        and c.entity_id != caster.entity_id
        and c.is_alive
        and not is_condition_active(Condition.INCAPACITATED, active_condition_names(c.conditions))
    ]
    if not allies:
        return {}
    out: dict[str, bool] = {}
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        for ally in allies:
            ally_zone = live.actor_zone.get(ally.entity_id)
            if ally_zone is not None and live.topology.within_range(ally_zone, target_zone, 5):
                out[target.entity_id] = True
                break
    return out


def _grappled_by_map(
    live: _LiveCombat, attacker: Combatant, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """SRD 5.2 Grappler — "a creature Grappled by you": per target, is it
    Grappled by ``attacker`` (``_condition_source_entity``)? Threaded into
    ``ActivityResolutionContext.target_grappled_by_attacker``; ``attack.py``
    applies it only to an attacker with the feat."""
    return {
        t.entity_id: True
        for t in targets
        if _condition_source_entity(live, t, "grappled") == attacker.entity_id
    }


def _pack_tactics_map(
    live: _LiveCombat, attacker: Combatant, targets: Sequence[Combatant]
) -> dict[str, bool]:
    """SRD 5.2 stat-block trait "Pack Tactics" — per target, is at
    least one of the ATTACKER's allies (any OTHER living combatant on its
    own side — the encounter for a monster attacker, a summon on its
    owner's) within 5 ft of that target and not Incapacitated?

    Mirrors ``_sneak_ally_adjacent_map``'s geometry (same spatial-seam
    consumer shape) with two differences: the ally gate is
    ``is_alive and hp_current > 0`` (not just ``is_alive``) plus
    ``conditions_block_actions`` (the Incapacitated helper shared with the
    rest of the engine, rather than the raw ``is_condition_active`` call),
    and the reach test is ``distance_ft(...) <= 5`` against the TARGET
    rather than ``within_range``. Threaded into
    ``ActivityResolutionContext.pack_tactics_ally_adjacent`` so the pure
    resolver never touches the spatial seam. Absent cell data for the
    attacker's side, a target, or every ally contributes no entry (⇒ no
    qualifying ally).
    """
    side = _allied_ids(live, attacker.entity_id)
    if not side:
        return {}
    allies = [
        c
        for c in live.initiative
        if c.entity_id in side
        and c.entity_id != attacker.entity_id
        and c.is_alive
        and c.hp_current > 0
        and not conditions_block_actions(active_condition_names(c.conditions))
    ]
    if not allies:
        return {}
    out: dict[str, bool] = {}
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        for ally in allies:
            ally_zone = live.actor_zone.get(ally.entity_id)
            if ally_zone is None:
                continue
            distance = live.topology.distance_ft(ally_zone, target_zone)
            if distance is not None and distance <= 5:
                out[target.entity_id] = True
                break
    return out


def _special_sense_reaches(live: _LiveCombat, viewer: Combatant, target: Combatant) -> bool:
    """SRD 5.2 Blindsight — "you can see anything that isn't behind Total Cover
    even if you have the Blinded condition or are in Darkness. Moreover, in
    that range, you can see something has the Invisible condition." Truesight
    — "You see creatures and objects that have the Invisible condition."
    Reach test only (``within_range`` on the viewer's blindsight / truesight);
    line of sight is re-checked by ``SpatialTopology.can_see``. Untracked
    positions ⇒ False. Darkvision is NOT a special sense here (it only
    re-grades light — SRD 5.2 Darkvision)."""
    target_zone = live.actor_zone.get(target.entity_id)
    return target_zone is not None and _special_sense_reaches_zone(live, viewer, target_zone)


def _special_sense_reaches_zone(live: _LiveCombat, viewer: Combatant, zone: str) -> bool:
    """``_special_sense_reaches`` for a space rather than a creature: the
    viewer's Blindsight or Truesight range reaches ``zone``. Untracked viewer
    position ⇒ False."""
    viewer_zone = live.actor_zone.get(viewer.entity_id)
    if viewer_zone is None:
        return False
    for range_ft in (viewer.senses.blindsight, viewer.senses.truesight):
        if range_ft and live.topology.within_range(viewer_zone, zone, range_ft):
            return True
    return False


def _blindsight_reaches_zone(live: _LiveCombat, viewer: Combatant, zone: str) -> bool:
    """The one sense that works through the Blinded condition. SRD 5.2
    Blindsight: "you can see anything that isn't behind Total Cover even if
    you have the Blinded condition"; Truesight is enhanced vision ("your
    vision pierces through" Darkness and Invisibility), and a Blinded creature
    "can't see". Untracked viewer position ⇒ False."""
    viewer_zone = live.actor_zone.get(viewer.entity_id)
    reach = viewer.senses.blindsight
    if viewer_zone is None or not reach:
        return False
    return live.topology.within_range(viewer_zone, zone, reach)


def _combatant_can_see(live: _LiveCombat, viewer: Combatant, target: Combatant) -> bool:
    """C16b composite "can see" predicate for every SRD 5.2
    "can see" conjunct: Dodge, Ranged Attacks in Close Combat, Opportunity
    Attacks, Hide's line-of-sight gate, Frightened's line-of-sight gate.

    1. Untracked position on either side ⇒ True (a scene with no positional
       data can never impose a penalty — same convention as
       ``_target_visibility_maps``).
    2. Blinded viewer (SRD 5.2 Blinded: "You can't see") ⇒ False unless its
       Blindsight reaches (``_blindsight_reaches_zone``; Truesight is sight).
    3. Invisible target (SRD 5.2 Invisible: "If a creature can somehow see
       you, you don't gain this benefit against that creature") ⇒ False
       unless a special sense reaches. A creature hidden
       via Hide carries the Invisible condition, so this covers it.
    4. Otherwise the scene vision model: ``SpatialTopology.can_see`` with
       the viewer's own projected senses (line of sight, light, obscurement).

    NOT used by ``_target_visibility_maps`` (the ``"unseen"`` producer) —
    Blinded / Invisible already emit their own ``condition:*`` sources.
    """
    viewer_zone = live.actor_zone.get(viewer.entity_id)
    target_zone = live.actor_zone.get(target.entity_id)
    if viewer_zone is None or target_zone is None:
        return True
    if is_condition_active(
        Condition.BLINDED, _condition_names(viewer)
    ) and not _blindsight_reaches_zone(live, viewer, target_zone):
        return False
    if is_condition_active(
        Condition.INVISIBLE, _condition_names(target)
    ) and not _special_sense_reaches(live, viewer, target):
        return False
    return live.topology.can_see(viewer_zone, target_zone, viewer.senses)


def _fear_source_in_sight(live: _LiveCombat, combatant: Combatant) -> bool:
    """SRD 5.2 Frightened: "You have Disadvantage on ability checks and attack
    rolls while the source of fear is within line of sight." The flag both
    rows read (``conditions_grant_advantage_on_attack``,
    ``conditions_grant_disadvantage_on_ability_checks``), "within line of
    sight" read as "can see". True (penalty stays) when ``combatant`` isn't
    Frightened, when its fear source is unknown (``_condition_source_entity``
    returns ``None``), or when the source is dead/untracked/no longer in the
    initiative order (SRD-conservative — can't prove it's out of sight).
    False only for a known, LIVING, tracked source that ``_combatant_can_see``
    says the Frightened creature cannot currently see.
    """
    if not is_condition_active(Condition.FRIGHTENED, _condition_names(combatant)):
        return True
    source_id = _condition_source_entity(live, combatant, "frightened")
    if source_id is None:
        return True
    source = next((c for c in live.initiative if c.entity_id == source_id), None)
    if source is None or not source.is_alive:
        return True
    return _combatant_can_see(live, combatant, source)


def _condition_check_sources(live: _LiveCombat, actor: Combatant) -> AdvantageSources:
    """The condition rows on an ability check the engine rolls outside an
    activity (Hide's Stealth, escaping a grapple): SRD 5.2 Poisoned, "You have
    Disadvantage on attack rolls and ability checks", and Frightened, while
    the source of fear is in sight. A ``CheckActivity`` reads the same rows
    through ``project_passive_check_modifiers``."""
    if conditions_grant_disadvantage_on_ability_checks(
        _condition_names(actor), fear_source_in_sight=_fear_source_in_sight(live, actor)
    ):
        return AdvantageSources(disadvantage=("condition:attacker",))
    return AdvantageSources()


def _frightened_approach_blocked(live: _LiveCombat, mover: Combatant, path: list[str]) -> bool:
    """SRD 5.2 Frightened: "You can't willingly move closer to the source of
    fear." Unlike the disadvantage sentence, this one has no line-of-sight
    clause, so a source the mover can't see still blocks it. True iff
    ``mover`` is Frightened of a known, LIVING, tracked source (an unknown,
    dead or untracked source leaves nothing to move closer to, so it imposes
    nothing here, though ``_fear_source_in_sight`` keeps its Disadvantage)
    AND some consecutive pair of cells in ``path`` strictly reduces
    ``live.topology.distance_ft`` to that source's cell. An unresolvable
    pairwise distance (untracked/cross-topology) never blocks.
    """
    if not is_condition_active(Condition.FRIGHTENED, _condition_names(mover)):
        return False
    source_id = _condition_source_entity(live, mover, "frightened")
    if source_id is None:
        return False
    source = next((c for c in live.initiative if c.entity_id == source_id), None)
    if source is None or not source.is_alive:
        return False
    source_cell = live.actor_zone.get(source_id)
    if source_cell is None:
        return False
    for prev_cell, next_cell in itertools.pairwise(path):
        prev_dist = live.topology.distance_ft(prev_cell, source_cell)
        next_dist = live.topology.distance_ft(next_cell, source_cell)
        if prev_dist is None or next_dist is None:
            continue
        if next_dist < prev_dist:
            return True
    return False


def _pierces_invisibility(live: _LiveCombat, viewer: Combatant, target: Combatant) -> bool:
    """Does ``viewer`` pierce ``target``'s Invisible condition
    (whether or not ``target`` actually carries it — the maps below are
    computed unconditionally; the SRD row in ``rules/conditions.py`` gates on
    the condition itself)? SRD 5.2 Blindsight: "in that range, you can see
    something has the Invisible condition ... even if you have the Blinded
    condition." Truesight: "You see creatures and objects that have the
    Invisible condition" — enhanced vision, not an exemption from Blinded's
    "You can't see". So a Blinded viewer pierces only through Blindsight
    (``_blindsight_reaches_zone``); an unblinded viewer through either sense
    (``_special_sense_reaches_zone``) — the same split ``_combatant_can_see``
    applies. Either way REACH AND line of sight (``SpatialTopology.can_see``
    with the viewer's own senses) are both required — Darkvision never
    pierces (it only re-grades light). Untracked positions ⇒ False (mirrors
    ``_special_sense_reaches``; an untracked pair never grants a piercing
    benefit, unlike the "everyone seen" convention used for the raw
    visibility maps).
    """
    target_zone = live.actor_zone.get(target.entity_id)
    if target_zone is None:
        return False
    if is_condition_active(Condition.BLINDED, _condition_names(viewer)):
        reaches = _blindsight_reaches_zone(live, viewer, target_zone)
    else:
        reaches = _special_sense_reaches_zone(live, viewer, target_zone)
    if not reaches:
        return False
    viewer_zone = live.actor_zone.get(viewer.entity_id)
    if viewer_zone is None:
        return False
    return live.topology.can_see(viewer_zone, target_zone, viewer.senses)


def _invisibility_pierced_maps(
    live: _LiveCombat, caster: Combatant, targets: Sequence[Combatant]
) -> tuple[dict[str, bool], dict[str, bool]]:
    """SRD 5.2 Invisible "can somehow see you" carve-out.
    Per target: (does the TARGET pierce the CASTER's Invisible condition,
    does the CASTER pierce the TARGET's Invisible condition), via
    ``_pierces_invisibility`` in each direction. Computed regardless of
    whether anyone is actually Invisible — cheap, and the SRD row in
    ``rules/conditions.py`` gates on the condition. Threaded into
    ``ActivityResolutionContext.attacker_invisibility_pierced_by`` /
    ``.target_invisibility_pierced`` so ``activities/attack.py`` can drop the
    Invisible advantage/disadvantage without importing the spatial seam.
    Untracked caster position ⇒ both maps empty (mirrors
    ``_target_visibility_maps``).
    """
    caster_zone = live.actor_zone.get(caster.entity_id)
    attacker_invisibility_pierced_by: dict[str, bool] = {}
    target_invisibility_pierced: dict[str, bool] = {}
    if caster_zone is None:
        return attacker_invisibility_pierced_by, target_invisibility_pierced
    for target in targets:
        target_zone = live.actor_zone.get(target.entity_id)
        if target_zone is None:
            continue
        attacker_invisibility_pierced_by[target.entity_id] = _pierces_invisibility(
            live, target, caster
        )
        target_invisibility_pierced[target.entity_id] = _pierces_invisibility(live, caster, target)
    return attacker_invisibility_pierced_by, target_invisibility_pierced


def _hostile_adjacent_to_attacker(live: _LiveCombat, caster: Combatant) -> bool:
    """SRD 5.2 "Ranged Attacks in Close Combat": "you have Disadvantage on
    the roll if you are within 5 feet of an enemy who can see you and
    doesn't have the Incapacitated condition."

    Scans ``live.initiative`` for a LIVING hostile (any combatant outside
    ``_allied_ids(caster)``, so an allied summon never counts) within 5 ft of
    the ATTACKER's own cell. The attack's TARGET is never special-cased — if it
    happens to be adjacent it's simply one more entry in ``live.initiative``
    and counts like any other hostile (SRD: "an enemy", not "an enemy other
    than your target"). Excludes an Incapacitated hostile
    (``conditions_block_actions`` — the SRD conjunct) and one that cannot
    see the attacker (the C16b composite ``_combatant_can_see`` — a Blinded
    hostile or an Invisible attacker no longer imposes the disadvantage,
    same call shape as ``_target_visibility_maps`` otherwise). Threaded
    into ``ActivityResolutionContext.attacker_ranged_in_melee`` so
    ``activities/attack.py`` can add the ``"ranged_in_melee"`` disadvantage
    source without importing the spatial seam. An unregistered side or an
    untracked attacker position yields ``False``.
    """
    allies = _allied_ids(live, caster.entity_id)
    if not allies:
        return False
    attacker_zone = live.actor_zone.get(caster.entity_id)
    if attacker_zone is None:
        return False
    for hostile in live.initiative:
        if (
            hostile.entity_id in allies
            or hostile.entity_id in live.dead_ids
            or not hostile.is_alive
        ):
            continue
        if conditions_block_actions(_condition_names(hostile)):
            continue
        hostile_zone = live.actor_zone.get(hostile.entity_id)
        if hostile_zone is None:
            continue
        if not live.topology.within_range(attacker_zone, hostile_zone, 5):
            continue
        if not _combatant_can_see(live, hostile, caster):
            continue
        return True
    return False


def push_combatant(live: _LiveCombat, target_id: str, origin_cell: str, distance_ft: int) -> None:
    """Forced movement primitive — move ``target_id`` up to ``distance_ft``
    straight away from ``origin_cell`` and emit ``CombatantMoved(forced=True)``
    for the distance actually covered. Consumes no movement budget and
    provokes no opportunity attack (SRD 5.2 §Opportunity Attacks). A dead or
    untracked target is never moved, and a target sharing ``origin_cell`` with
    the pusher has no direction to be pushed along: no move and no event."""
    topology = live.topology
    target_cell = live.actor_zone.get(target_id)
    if target_cell is None or target_id in live.dead_ids:
        return
    occupied = _occupied_cells(live, exclude=(target_id,))
    path = topology.push_path(origin_cell, target_cell, distance_ft, occupied_cells=occupied)
    if not path:
        return
    live.actor_zone[target_id] = path[-1]
    _emit(
        live,
        CombatantMoved(
            actor_id=target_id,
            from_zone=target_cell,
            to_zone=path[-1],
            distance_ft=len(path) * topology.cell_size_ft,
            forced=True,
        ),
    )


def _apply_forced_movement_riders(
    live: _LiveCombat, caster: Combatant, intent: PlayerIntent, pre_event_count: int
) -> None:
    """After a cast resolves, apply the spell's typed forced-movement rider to
    every target whose save against the SPELL failed (trigger ``failed_save``).

    Only the FIRST ``SaveRolled`` per target in this resolution's event slice
    is the spell's own save: the damage can trigger a later one (Undead
    Fortitude's Constitution save), and a target that SAVED against the spell
    must not be shoved because of it. The save resolver always emits before
    ``DamageApplied``, so "first per target" is well defined; keying on it also
    caps each target at one push.

    Pushes happen after all saves/damage so the rider never perturbs the
    seeded roll order."""
    rider = FORCED_MOVEMENT_RIDERS.get(intent.spell_id or "")
    if rider is None or rider.trigger != "failed_save" or rider.direction != "away_from_caster":
        return
    origin_cell = live.actor_zone.get(caster.entity_id)
    if origin_cell is None:
        return
    seen: set[str] = set()
    for ev in list(live.event_log[pre_event_count:]):
        if not isinstance(ev, SaveRolled) or ev.target_id in seen:
            continue
        seen.add(ev.target_id)
        if not ev.succeeded:
            push_combatant(live, ev.target_id, origin_cell, rider.distance_ft)


def _record_sneak_attack_spent(
    live: _LiveCombat,
    caster: Combatant,
    weapon: Weapon | None,
    targets: Sequence[Combatant],
    actx: ActivityResolutionContext,
    pre_event_count: int,
) -> None:
    """SRD §Sneak Attack, "Once per turn" — flip the caster's per-turn
    ``sneak_attack_spent_this_turn`` flag once a rider has actually fired.

    The rider folds inside the pure resolver; recording the actor-state is the
    orchestrator's job. A rider fired iff this was a weapon attack, the caster
    has Sneak Attack dice, was not already spent, and at least one damaged
    target satisfied the trigger (``sneak_attack_triggers`` — the SAME predicate
    the resolver gated the fold on, reused here so the two never diverge).

    ``weapon`` is the swung weapon — ``None`` for anything but a weapon
    attack, which spends nothing. The flag clears at every ``TurnStarted``
    (``_emit_apply_turn_started``), so an opportunity attack on another
    creature's turn can deal Sneak Attack again.
    """
    if weapon is None:
        return
    if caster.sneak_attack_spent_this_turn or sneak_attack_dice(actx) is None:
        return
    has_advantage, has_disadvantage = attacker_advantage_flags(actx)
    damaged_ids = {
        e.target_id for e in live.event_log[pre_event_count:] if isinstance(e, DamageApplied)
    }
    fired = any(
        target.entity_id in damaged_ids
        and sneak_attack_triggers(
            actx,
            weapon,
            target,
            attacker_has_advantage=has_advantage,
            attacker_has_disadvantage=has_disadvantage,
        )
        for target in targets
    )
    if not fired:
        return
    for idx, c in enumerate(live.initiative):
        if c.entity_id == caster.entity_id:
            live.initiative[idx] = c.model_copy(update={"sneak_attack_spent_this_turn": True})
            break


def _record_savage_attacker_spent(
    live: _LiveCombat, caster: Combatant, actx: ActivityResolutionContext
) -> None:
    """SRD 5.2 Savage Attacker, "Once per turn" — flip the caster's
    ``savage_attacker_spent_this_turn`` once the resolver rolled a weapon's
    damage dice twice (it marks ``actx.savage_attacker_spent``). The flag
    clears at every ``TurnStarted`` (``_emit_apply_turn_started``)."""
    if actx.savage_attacker_spent.get(caster.entity_id):
        _update_combatant(live, caster.entity_id, savage_attacker_spent_this_turn=True)


def _path_total_distance(
    topology: SpatialTopology,
    path: Sequence[str],
    *,
    ignore_terrain: Collection[str] = ()
) -> int | None:
    """Sum a shortest-path's edge distances; ``None`` if any step is missing.

    ``path`` is the cell sequence ``shortest_path`` returns (``path[0]``
    is the start). A one-element or empty path costs ``0``.

    ``ignore_terrain`` is the walker own difficult-terrain exemption (SRD 5.2
    Boots of the Winterlands, "You ignore Difficult Terrain created by ice or
    snow"): only the cells that walker ignores stop doubling.
    """
    if len(path) < 2:
        return 0
    total = 0
    for a, b in itertools.pairwise(path):
        step = topology.edge_distance(a, b, ignore_terrain=ignore_terrain)
        if step is None:
            return None
        total += step
    return total


def _monster_dash_movement_budget(
    total_path_distance: int | None,
    movement_remaining: int,
    base_speed: int,
) -> int | None:
    """SRD §Actions in Combat, Dash — decide whether a monster gambit should
    Dash to close a gap it cannot otherwise cross this turn.

    Returns the doubled movement budget (``movement_remaining + base_speed``,
    mirroring ``_handle_dash``'s additive convention) when the path to the
    target exceeds the current budget but fits within a single Dash; ``None``
    when no Dash is needed (already in reach) or a Dash still wouldn't be
    enough (the monster gives up on closing the gap, same as today).
    """
    if total_path_distance is None or base_speed <= 0:
        return None
    if total_path_distance <= movement_remaining:
        return None  # already affordable — no Dash needed
    dashed_budget = movement_remaining + base_speed
    if total_path_distance > dashed_budget:
        return None  # even a Dash can't close this gap
    return dashed_budget


def _plan_flee_route(
    grid: GridTopology,
    start: str,
    threat_cells: Collection[str],
    budget_ft: int,
    *,
    enemy_cells: Collection[str],
    occupied_cells: Collection[str],
) -> list[str] | None:
    """The route a fleeing monster standing on ``start`` walks to get as far
    from its threats as ``budget_ft`` allows (``route[0]`` is ``start``), or
    ``None`` when it holds: cornered, out of movement, or nowhere farther.
    Monster AI, DM-adjudicated rather than SRD text; it draws no dice.

    The candidates are the cells ``GridTopology.reachable_cells`` reaches
    without entering an enemy's space, except ``start`` and any cell a
    creature stands on (SRD 5.2: "You can't willingly end a move in a space
    occupied by another creature"). A candidate scores its Chebyshev distance
    to the NEAREST threat, the same measure as reach, and only a cell that
    scores above ``start`` qualifies. The best has the highest score, then
    the cheapest route, then the lowest ``(column, row)`` compared as
    integers (the string ``"10,0"`` sorts before ``"2,0"``).
    """

    def nearest_threat_ft(cell: str) -> int:
        return min(grid.distance_ft(cell, threat) or 0 for threat in threat_cells)

    reached = grid.reachable_cells(start, budget_ft, avoid=enemy_cells)
    occupied = set(occupied_cells)
    baseline = nearest_threat_ft(start)
    candidates = [
        (-score, cost, parse_cell(cell), cell)
        for cell, (cost, _previous) in reached.items()
        if cell != start and cell not in occupied and (score := nearest_threat_ft(cell)) > baseline
    ]
    if not candidates:
        return None
    route = [min(candidates)[3]]
    while (previous := reached[route[-1]][1]) is not None:
        route.append(previous)
    return route[::-1]


def _take_walk_step(live: _LiveCombat, mover_id: str, next_cell: str) -> bool:
    """One step of a monster's walk (the closing walk, the flee walk): fire the
    opportunity attacks it provokes, then pay the step out of
    ``movement_remaining``, move, and emit its own ``ActorMoved``.

    Returns ``False``, without moving, when the walk ends here instead: the
    step is illegal or no longer affordable (an opportunity attack's rider can
    cut the budget), or an opportunity attack stopped the mover
    (``_walk_must_stop``)."""
    mover = _find_combatant(live, mover_id)
    from_cell = live.actor_zone.get(mover_id)
    if mover is None or from_cell is None:
        return False
    step_distance = live.topology.edge_distance(from_cell, next_cell)
    if step_distance is None or mover.movement_remaining < step_distance:
        return False
    if _fire_opportunity_attacks_on_step(
        live, mover_id=mover_id, from_cell=from_cell, to_cell=next_cell
    ):
        return False
    mover = _find_combatant(live, mover_id)
    if mover is None or mover.movement_remaining < step_distance:
        return False
    _spend_movement(live, mover_id, step_distance)
    live.actor_zone[mover_id] = next_cell
    _emit(
        live,
        ActorMoved(
            actor_id=mover_id, from_zone=from_cell, to_zone=next_cell, distance_ft=step_distance
        ),
    )
    return True


def _walk_path(live: _LiveCombat, mover_id: str, path: Sequence[str]) -> None:
    """Walk ``mover_id`` along ``path`` (``path[0]`` is its own cell) one
    ``_take_walk_step`` at a time, stopping at the first step that can't be
    taken. The flee walk's counterpart of the closing walk, which also stops
    once its target is in range."""
    for next_cell in path[1:]:
        if not _take_walk_step(live, mover_id, next_cell):
            return


def _execute_flee_retreat(
    live: _LiveCombat, monster: Combatant, enemies: Sequence[Combatant]
) -> None:
    """A fleeing monster spends its movement getting away from ``enemies``,
    every living enemy it would otherwise attack (a charmer excluded): the
    route ``_plan_flee_route`` picks, walked by ``_walk_path``. Fleeing takes
    no Disengage or Dash, so each step out of an enemy's reach provokes its
    opportunity attack. Monster-AI plumbing (DM-adjudicated, not SRD text):
    ``_monster_is_fleeing`` decides the monster wants to get away; this gives
    that decision teeth. A monster with nowhere farther to go holds.
    """
    start = live.actor_zone.get(monster.entity_id)
    threat_cells = [
        cell for enemy in enemies if (cell := live.actor_zone.get(enemy.entity_id)) is not None
    ]
    if start is None or not threat_cells:
        return
    route = _plan_flee_route(
        live.topology,
        start,
        threat_cells,
        monster.movement_remaining,
        enemy_cells=_occupied_cells(live, exclude=_allied_ids(live, monster.entity_id)),
        occupied_cells=_occupied_cells(live, exclude=(monster.entity_id,)),
    )
    if route is not None:
        _walk_path(live, monster.entity_id, route)


def _apply_monster_flee_stance(
    live: _LiveCombat, current: Combatant, enemies: Sequence[Combatant]
) -> Combatant:
    """Persist ``Combatant.has_fled`` across turns.

    A live, conscious monster over the flee threshold (``_monster_is_fleeing``)
    that is not Incapacitated (SRD 5.2: "You can't take any action, Bonus
    Action, or Reaction"; an Incapacitated monster takes no flee stance at
    all) spends its movement retreating (``_execute_flee_retreat``) and is marked
    ``has_fled=True`` regardless of whether that retreat actually moved it
    (already cornered, or no movement budget left) — the flag records the monster's STANCE this
    turn, not whether it displaced. A live, conscious monster that is NOT
    fleeing this turn — including one healed back above the threshold after
    an earlier flee — is marked ``has_fled=False``, so ``_derive_ended_reason``'s
    "every living foe has fled" check reflects current stance rather than a
    stale flag. Dead/unconscious monsters take no turn and are left
    untouched either way.

    Returns the (possibly replaced, per ``model_copy``) ``Combatant`` so the
    caller's ``current`` stays in sync with ``live.initiative``.
    """
    if not (current.is_alive and current.hp_current > 0):
        return current
    fleeing_by_hp = _monster_is_fleeing(current) and not conditions_block_actions(
        _condition_names(current)
    )
    # SRD 5.2 Turn Undead: a creature that failed its save "must spend its
    # turns trying to move as far away from you as it can" — a forced flight
    # that outranks the Incapacitated condition the same feature imposes
    # ("You can't take any Action, Bonus Action, or Reaction" — it never says
    # you can't move). Modelled here so a turned undead physically retreats
    # instead of standing still, and WITHOUT marking it ``has_fled``: the
    # encounter is not over, the creature is only repelled for a minute.
    turned = _is_turned(live, current.entity_id)
    fleeing = fleeing_by_hp or turned
    if fleeing:
        _execute_flee_retreat(live, current, enemies)
        current = next(c for c in live.initiative if c.entity_id == current.entity_id)
    for idx, c in enumerate(live.initiative):
        if c.entity_id == current.entity_id:
            live.initiative[idx] = c.model_copy(update={"has_fled": fleeing_by_hp})
            return live.initiative[idx]
    return current


def _monster_target_distance_ft(
    live: _LiveCombat, monster_id: str, target: Combatant | None
) -> int | None:
    """Path distance (ft) from a monster to its chosen target, or ``None``.

    The same shortest-path cost the movement gate reads — handed to
    ``expand_action_to_parts`` so its range-aware multiattack fallback and
    the gate agree on the live distance. ``None`` when either actor
    has no tracked cell or no path connects them.
    """
    if target is None:
        return None
    monster_zone = live.actor_zone.get(monster_id)
    target_zone = live.actor_zone.get(target.entity_id)
    if monster_zone is None or target_zone is None:
        return None
    path = live.topology.shortest_path(monster_zone, target_zone)
    return _path_total_distance(live.topology, path)


def _pc_attack_out_of_range(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> bool:
    """True iff the PC attack would be rejected by the weapon-reach gate.

    Returns ``False`` when the gate doesn't apply (no target, no weapon
    id, unknown weapon, no extractable reach/range, or no tracked cell
    for one of the participants) — those cases fall through to the
    resolver, which then either synthesizes IR or returns empty.
    """
    if intent.target_id is None or not intent.weapon_id:
        return False
    weapon = get_lib_loader().get_weapon(intent.weapon_id)
    weapon_bands = _weapon_attack_range_ft(weapon)
    if weapon_bands is None:
        return False
    # SRD 5.2 §Range — the reject gate reads the MAX (long) band only; the
    # narrower normal/long distinction lives in ``_target_beyond_normal_range_map``
    # and is folded into a Disadvantage source (``attack.py``), never a reject.
    _normal_band, max_band = weapon_bands
    attacker_zone = live.actor_zone.get(actor_id)
    target_zone = live.actor_zone.get(intent.target_id)
    if attacker_zone is None or target_zone is None:
        return False
    return not _in_range_with_los(live.topology, attacker_zone, target_zone, max_band)


def _attack_out_of_range_failure(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """``AttackFailed(reason="out_of_range")`` when ``intent`` is a
    weapon-reach-gated attack; ``None`` otherwise. One of the
    ``pre_resolution_gates`` failure-builders consumed by
    ``submit_player_intent``."""
    if intent.intent_type != "attack" or not _pc_attack_out_of_range(live, actor_id, intent):
        return None
    return AttackFailed(actor_id=actor_id, target_id=intent.target_id, reason="out_of_range")


def _attack_target_missing_failure(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """`AttackFailed(reason="target_invalid")` when an attack names a
    creature that is not in the fight; `None` otherwise. One of the
    `pre_resolution_gates` failure-builders consumed by
    `submit_player_intent`.

    SRD 5.2 §Making an Attack: "Choose a target within your reach" — a
    target must exist. Without this gate an attack naming an unknown id
    reached `_resolve_targets`, which looks the id up in the initiative
    order, finds nobody and returns an empty list: the intent was emitted
    as `intent_submitted`, no attack roll happened, and the Action was
    spent anyway. The host saw a successful submission with no
    `attack_rolled` and a turn whose action had vanished.

    `_pc_attack_out_of_range` cannot catch this — it reads the target
    cell, and an unknown target has none, so it reports "in range".
    """
    if intent.intent_type != "attack" or intent.target_id is None:
        return None
    if any(c.entity_id == intent.target_id for c in live.initiative):
        return None
    return AttackFailed(
        actor_id=actor_id, target_id=intent.target_id, reason="target_invalid"
    )

def _loading_weapon_already_fired_failure(
    current: Combatant, actor_id: str, intent: PlayerIntent, weapon: Weapon | None
) -> CombatEvent | None:
    """SRD 5.2 Loading — "You can fire only one piece of ammunition from a
    Loading weapon when you use an action, a Bonus Action, or a Reaction to
    fire it, regardless of the number of attacks you can normally make."
    Engine reading: one fire per TURN (no PC reaction-attack path exists,
    so action/bonus/reaction collapse to the turn boundary); the cap is
    per-actor, not per-weapon (SRD ambiguity resolved toward "you", the
    actor). Applies to BOTH the main-hand and off-hand attack path — one
    of the ``pre_resolution_gates`` failure-builders consumed by
    ``submit_player_intent``, so a rejected shot spends no Action/Bonus
    Action and leaves ``attacks_remaining`` untouched."""
    if (
        intent.intent_type != "attack"
        or weapon is None
        or WeaponProperty.LOADING not in weapon.properties
        or not current.loading_weapon_fired_this_turn
    ):
        return None
    return AttackFailed(
        actor_id=actor_id, target_id=intent.target_id, reason="weapon_already_fired"
    )


_HARMFUL_ACTIVITY_KINDS: frozenset[str] = frozenset({"attack", "damage", "save"})


def _spell_is_harmful(spell: Spell) -> bool:
    """SRD 5.2 Charmed's "damaging abilities or magical effects" — a spell with
    any attack / damage / save activity. Heal- and utility-only spells are
    not restricted."""
    return any(a.kind in _HARMFUL_ACTIVITY_KINDS for a in spell.activities)


def _charmed_target_violation(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> str | None:
    """The charmer's id when ``intent`` would attack / harmfully target the
    creature that charmed ``current``; ``None`` otherwise (not charmed, unknown
    charmer, other target, beneficial spell, non-targeting intent)."""
    if intent.target_id is None or not is_condition_active(
        Condition.CHARMED, _condition_names(current)
    ):
        return None
    charmer = _condition_source_entity(live, current, "charmed")
    if charmer is None or charmer != intent.target_id:
        return None
    if intent.intent_type == "attack":
        return charmer
    if intent.intent_type == "cast_spell" and intent.spell_id:
        spell = get_lib_loader().get_spell(intent.spell_id)
        if spell is not None and _spell_is_harmful(spell):
            return charmer
    return None


def _charmed_target_failure(
    live: _LiveCombat, actor_id: str, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """The typed Charmed-target rejection (``AttackFailed`` / ``CastFailed``,
    reason ``target_is_charmer``) when ``intent`` would attack or harmfully
    target the charmer; ``None`` when the gate doesn't apply. One of the
    ``pre_resolution_gates`` failure-builders consumed by
    ``submit_player_intent``."""
    charmer = _charmed_target_violation(live, current, intent)
    if charmer is None:
        return None
    if intent.intent_type == "attack":
        return AttackFailed(actor_id=actor_id, target_id=charmer, reason="target_is_charmer")
    return CastFailed(actor_id=actor_id, spell_id=intent.spell_id or "", reason="target_is_charmer")


def _synthesize_attack_from_weapon(weapon: Weapon) -> AttackActivity:
    """Build a base-weapon ``AttackActivity`` for a weapon with no
    activities of its own.

    A handful of magic weapons (frost-brand, flame-tongue, …) ship empty
    ``activities`` because their attack rides the base mundane weapon they
    enchant. A bare ``AttackActivity`` (empty ``attack.ability`` ⇒ the
    resolver picks the weapon's SRD default ability; empty ``damage.parts``
    with ``include_base=True`` ⇒ the handler rolls ``weapon.damage_parts``)
    reproduces the OLD ``_synthesize_weapon_attack`` behavior: one melee/ranged
    swing dealing the weapon's own dice plus the governing-ability mod.
    """
    return AttackActivity(
        id=f"synth:{weapon.slug}",
        activation=ActivationBlock(type="action", value=1),
    )


_LEGACY_DICE_RE = re.compile(r"(\d+)d(\d+)([+-]\d+)?$")


def _synthesize_attack_from_legacy_fields(current: Combatant) -> AttackActivity | None:
    """Legacy-fixture fallback (SRD §Attack Rolls / §Damage Rolls): one typed
    ``AttackActivity`` built from the spec-supplied ``attack_bonus`` /
    ``damage_dice`` / ``damage_type`` for a monster with no
    ``monster_template_slug``. ``specs.py`` has always documented this
    fallback on the field; the typed-activity cutover dropped it and
    template-less foes silently no-opped every turn. The synthesized
    activity flows through the normal gambit (movement, range gate,
    condition rows, concentration checks): to-hit magnitude comes from
    ``build_activity_context``'s Monster branch (``attack_bonus``), damage
    from the parsed dice parts. ``None`` when ``damage_dice`` doesn't parse
    — the turn stays the pre-existing no-op pass.
    """
    # The whitespace strip is deliberate: host-supplied damage_dice strings
    # are tolerated in loosely-spaced forms too (e.g. "2 d 6", "2d6 +3"),
    # not just the canonical "2d6+3".
    m = _LEGACY_DICE_RE.match((current.damage_dice or "").replace(" ", ""))
    if m is None:
        return None
    return AttackActivity(
        id="synth:legacy-swing",
        activation=ActivationBlock(type="action", value=1),
        damage=AttackDamageBlock(
            include_base=False,
            parts=[
                DamagePartBlock(
                    number=int(m.group(1)),
                    denomination=int(m.group(2)),
                    bonus=m.group(3) or "",
                    types=[current.damage_type or "bludgeoning"],
                )
            ],
        ),
    )


# ── Monster areas of effect ─────────────────────────────────────────────────

#: One activity a monster's turn resolves, with the stat-block action it comes
#: from (``None`` for a legacy fixture's synthesized attack).
_MonsterPart = tuple[MonsterAction | None, Any]

# SRD 5.2 stat blocks name "which creatures make the save" ("each creature in a
# 60-foot Cone", "each enemy in a 20-foot-radius Sphere"), and a monster places
# its own area: the built-in AI aims it where it affects the most enemies minus
# allies (``areas.best_aim``), never on itself, and only when it affects an
# enemy — else that action or spell is not an option this turn, and the monster
# takes its next one. The same holds for a stat-block action, a monster's spell
# and a legendary action.


def _monster_area(activities: Sequence[Any]) -> tuple[Any, AreaTemplate] | None:
    """The area a monster's activities place, as its area activity and grid
    template; ``None`` when they resolve against the turn's one target: no
    templated save, damage or heal; an attack among them (an attack roll
    targets one creature); a template the grid can't place (a ``wall``, a
    formula size); or "one creature" (a count of one keeps that one target)."""
    if any(a.kind == "attack" for a in activities):
        return None
    activity = area_activity(activities)
    if activity is None:
        return None
    template = area_template(activity)
    if template is None or creature_count(activity) == 1:
        return None
    return activity, template


def _range_ft(block: Any) -> int | None:
    """A range block's reach in whole feet, or ``None`` for one given in other
    units ("self", "touch") or as a formula."""
    value = str(block.value or "")
    if block.units != "ft" or not value.isdigit() or int(value) == 0:
        return None
    return int(value)


def _monster_aim(
    live: _LiveCombat,
    monster: Combatant,
    activity: Any,
    template: AreaTemplate,
    range_ft: int | None,
) -> AreaAim | None:
    """Where ``monster`` places ``activity``'s area, and whom it then affects.

    ``areas.best_aim`` scores each candidate placement against ``monster``'s
    own targets (``_select_monster_targets``: living, above 0 Hit Points, a
    charmer excepted) — a Sphere or Cylinder centres on one it can see
    within ``range_ft`` — and picks the aim with the most of those targets
    minus allies, never the monster itself, requiring at least one of them
    among the affected. That scored selection is valid only for the
    population it was scored against: once an origin and direction are
    chosen this way, whom the area actually affects is recomputed at that
    placement against every living creature it catches there, 0 Hit Points
    included (SRD 5.2: a creature at 0 Hit Points in an area still makes its
    save or takes its damage). There an enemy is any creature on the other
    side; a charmer is spared only where the monster chooses whom the area
    affects (a choice or counted area)."""
    actor_cell = live.actor_zone[monster.entity_id]
    enemies = _select_monster_targets(live, monster)
    target_cells = [
        cell
        for enemy in enemies
        if range_ft is not None
        and (cell := live.actor_zone.get(enemy.entity_id)) is not None
        and live.topology.within_range(actor_cell, cell, range_ft)
        and _combatant_can_see(live, monster, enemy)
    ]
    creatures = [
        (c.entity_id, cell)
        for c in live.initiative
        if c.is_alive
        and c.entity_id not in live.dead_ids
        and (cell := live.actor_zone.get(c.entity_id)) is not None
    ]
    enemy_ids = {c.entity_id for c in enemies}
    allies = _allied_ids(live, monster.entity_id)
    aim = best_aim(
        live.topology,
        activity,
        template,
        actor_id=monster.entity_id,
        actor_cell=actor_cell,
        target_cells=target_cells,
        creatures=creatures,
        is_enemy=enemy_ids.__contains__,
        is_ally=allies.__contains__,
    )
    if aim is None:
        return None
    charmer_id = (
        _condition_source_entity(live, monster, "charmed")
        if is_choice(activity) or creature_count(activity) is not None
        else None
    )

    def affected_is_enemy(other_id: str) -> bool:
        return _is_enemy(live, monster.entity_id, other_id) and other_id != charmer_id

    cells = area_cells(live.topology, template, aim.origin, aim.direction)
    selection = select_affected(
        [entity_id for entity_id, cell in creatures if cell in cells],
        affects_type=activity.target.affects.type,
        choice=is_choice(activity),
        count=creature_count(activity),
        harmful=is_harmful([activity]),
        excluded_ids=None,
        is_enemy=affected_is_enemy,
        is_ally=allies.__contains__,
    )
    return AreaAim(aim.origin, aim.direction, selection)


def _place_monster_area(
    live: _LiveCombat, monster: Combatant, source_id: str, template: AreaTemplate, aim: AreaAim
) -> list[Combatant]:
    """Report a monster's area (``AreaTargeted``) and return the creatures it
    affects, in initiative order."""
    _emit(
        live,
        AreaTargeted(
            actor_id=monster.entity_id,
            source_id=source_id,
            shape=template.shape,
            size_ft=template.size_ft,
            origin=aim.origin,
            direction=aim.direction,
            affected_ids=list(aim.selection.affected_ids),
            excluded_ids=list(aim.selection.spared_ids),
        ),
    )
    by_id = {c.entity_id: c for c in live.initiative}
    return [by_id[i] for i in aim.selection.affected_ids]


def _monster_area_unaimable(
    live: _LiveCombat, monster: Combatant, activities: Sequence[Any], spell: Spell | None
) -> bool:
    """True when ``activities`` place an area that affects no enemy from where
    ``monster`` stands, within ``spell``'s own range when it casts one (the
    range ``_resolve_monster_cast`` aims with), else the area activity's."""
    area = _monster_area(activities)
    if area is None:
        return False
    activity, template = area
    reach = _range_ft(spell.range) if spell is not None else _range_ft(activity.range)
    return _monster_aim(live, monster, activity, template, reach) is None


def _action_area_aim(
    live: _LiveCombat, monster: Combatant, area: tuple[Any, AreaTemplate]
) -> AreaAim | None:
    """``_monster_aim`` for a stat-block or legendary action's area, within
    its area activity's own range."""
    activity, template = area
    return _monster_aim(live, monster, activity, template, _range_ft(activity.range))


def _resolve_monster_area(
    live: _LiveCombat,
    monster: Combatant,
    source_id: str,
    template: AreaTemplate,
    aim: AreaAim,
    activities: Sequence[Any],
) -> None:
    """Resolve ``activities`` against the creatures ``aim`` affects, reported
    in an ``AreaTargeted`` naming ``source_id`` (the action's slug)."""
    affected = _place_monster_area(live, monster, source_id, template, aim)
    _resolve_monster_attack_activities(live, monster, affected, activities, cover_origin=aim.origin)


def _resolve_monster_parts(
    live: _LiveCombat,
    current: Combatant,
    target: Combatant,
    parts: Sequence[_MonsterPart],
) -> None:
    """Resolve a monster turn's chosen action part by part — SRD 5.2
    Multiattack: the attacks it makes and the actions it uses, in order.
    Consecutive parts that place no area resolve together against
    ``target``, as one action; a part whose action places an area resolves
    against the creatures that area affects (``_resolve_monster_area``), and
    is skipped when, from where the monster ended its move, that is no enemy.
    A Recharge action the turn resolves is spent."""

    def area_action(part: _MonsterPart) -> MonsterAction | None:
        action = part[0]
        if action is None or _monster_area(action.activities) is None:
            return None
        return action

    for action, group in itertools.groupby(parts, key=area_action):
        members = list(group)
        activities = [activity for _, activity in members]
        if action is None or (area := _monster_area(action.activities)) is None:
            _resolve_monster_attack_activities(live, current, [target], activities)
        elif (aim := _action_area_aim(live, current, area)) is None:
            continue
        else:
            _resolve_monster_area(live, current, action.slug, area[1], aim, activities)
        for owner in {id(a): a for a, _ in members if a is not None}.values():
            _mark_monster_action_used(live, current, owner)


# ── Monster action selection, casts and legendary actions ───────────────────


def _monster_cast_candidate(
    live: _LiveCombat, current: Combatant, action: MonsterAction
) -> tuple[CastActivity, Spell] | None:
    """Which spell a cast-only monster action (SRD 5.2 §Spellcasting /
    Innate Spellcasting) resolves to, or ``None`` when nothing on it
    qualifies.

    Walks ``action``'s ``CastActivity`` entries in two passes — first every
    LIMITED-USE activity (a tracked ``uses_remaining`` entry) that still has
    a use left, then every at-will activity (no tracked entry) — each pass
    in the stat block's own listed order, and returns the FIRST one that:

      * is not an exhausted limited-use activity (tracked, zero left);
      * resolves to a real ``Spell`` via ``activity.spell.uuid`` (an
        unresolvable uuid logs ``cast_spell_unresolved`` and is skipped,
        mirroring ``activities/cast.py``'s loud-miss idiom);
      * casts on the monster's own turn (``casting_time.unit`` one of
        ``action``/``bonus``/``reaction`` — a ritual-only or 1-minute+ prep
        spell in the repertoire, e.g. the mage's Detect Magic at 10 minutes'
        notice, is never a combat-turn action);
      * carries at least one offensive activity of its own (an
        ``AttackActivity``/``SaveActivity``/``DamageActivity`` — a buff/
        utility spell like Mage Armor or Invisibility is never worth the
        monster's action over an attack);
      * when it places an area, affects an enemy from where the monster
        stands (``_monster_area_unaimable``).

    The limited-use-first order is the SRD 5.2 Multiattack DM guidance
    ("have it use Multiattack on any of its turns in which it's not using
    one of its more powerful abilities") applied inside one Spellcasting
    action: the adult red dragon's Spellcasting lists Command and Scorching
    Ray at will before its 1/Day Fireball, and the turn that promotes the
    action above Multiattack for that Fireball must cast the Fireball.

    A monster's spells are stat-block resources tracked entirely through
    ``monster_action_uses_by_entity`` — this never touches
    ``spell_slots_by_entity`` (PC-only).
    """
    entry = live.monster_action_uses_by_entity.get(current.entity_id, {}).get(action.slug)
    limited: list[CastActivity] = []
    at_will: list[CastActivity] = []
    for activity in action.activities:
        if not isinstance(activity, CastActivity):
            continue
        remaining = (
            entry.uses_remaining.get(f"{action.slug}:{activity.id}") if entry is not None else None
        )
        if remaining is None:
            at_will.append(activity)
        elif remaining > 0:
            limited.append(activity)
    for activity in (*limited, *at_will):
        uuid = activity.spell.uuid
        spell = _build_cast_spell_book([activity]).get(uuid)
        if spell is None:
            _LOGGER.warning("cast_spell_unresolved uuid=%s", uuid)
            continue
        if spell.casting_time.unit not in ("action", "bonus", "reaction"):
            continue
        if not any(
            isinstance(a, (AttackActivity, SaveActivity, DamageActivity)) for a in spell.activities
        ):
            continue
        if _monster_area_unaimable(live, current, spell.activities, spell):
            continue
        return activity, spell
    return None


def _monster_limited_cast_remaining(
    live: _LiveCombat, current: Combatant, action: MonsterAction
) -> bool:
    """Whether the cast ``_monster_cast_candidate`` would resolve for
    ``action`` right now is a LIMITED-USE (tracked N/Day) one — the live
    probe ``rank_monster_actions`` uses to keep a Spellcasting action in its
    limited-use tier only while such a cast has a use remaining. An action
    whose only resolvable offensive casts are at will answers ``False`` and
    ranks behind Multiattack.
    """
    candidate = _monster_cast_candidate(live, current, action)
    if candidate is None:
        return False
    entry = live.monster_action_uses_by_entity.get(current.entity_id, {}).get(action.slug)
    return entry is not None and f"{action.slug}:{candidate[0].id}" in entry.uses_remaining


def _monster_action_available(live: _LiveCombat, current: Combatant, action: MonsterAction) -> bool:
    """Whether ``action`` can be chosen for ``current``'s turn right now.

    ``False`` when: its tracked ``MonsterActionUses.recharge_spent`` is
    True (SRD 5.2 "Recharge X-Y" — spent and not yet rolled back in); its
    activities are ALL ``CastActivity`` and ``_monster_cast_candidate``
    can't resolve one (every candidate exhausted/unresolvable/non-offensive);
    its limited-use activities are all exhausted (``uses_remaining``
    tracked and all zero) with no unlimited activity on the same action to
    fall back to; or it places an area that affects no enemy from where the
    monster stands (``_monster_area_unaimable``). ``True`` otherwise,
    including for an action with no tracked ``MonsterActionUses`` entry at
    all (nothing to gate).
    """
    entry = live.monster_action_uses_by_entity.get(current.entity_id, {}).get(action.slug)
    if entry is not None and entry.recharge_spent:
        return False
    activities = action.activities
    if activities and all(isinstance(a, CastActivity) for a in activities):
        return _monster_cast_candidate(live, current, action) is not None
    if entry is not None and entry.uses_remaining:
        has_unlimited_activity = any(
            not activity.uses.max.strip().isdigit() for activity in activities
        )
        if not has_unlimited_activity and all(v <= 0 for v in entry.uses_remaining.values()):
            return False
    return not _monster_area_unaimable(live, current, activities, None)


def _mark_monster_action_used(live: _LiveCombat, current: Combatant, action: MonsterAction) -> None:
    """Record that ``action`` was chosen this turn — spends its tracked
    ``MonsterActionUses`` state.

    Only the recharge half is handled here: a chosen recharge action is
    marked spent (``_roll_recharges`` then rolls for it at the monster's
    NEXT turn start). The per-day cast-activity ``uses_remaining`` decrement
    happens in ``_resolve_monster_cast`` instead — this function only ever
    sees the ``MonsterAction``, never the specific ``CastActivity``
    ``_monster_cast_candidate`` chose off it.
    """
    if not action.recharge:
        return
    entry = live.monster_action_uses_by_entity.get(current.entity_id, {}).get(action.slug)
    if entry is not None:
        entry.recharge_spent = True


def _select_monster_targets(live: _LiveCombat, current: Combatant) -> list[Combatant]:
    """Target pool for ``current``'s attack/legendary action: every living
    enemy of ``current`` — the party's members and the summons they own
    (C21: "The creature is an ally to you and your allies") — minus a
    charmer (SRD 5.2 Charmed — "You can't attack the charmer"). Extracted
    from the main-turn targeting block so a legendary action can share it
    byte-for-byte.
    """
    enemies = [
        c
        for c in live.initiative
        if _is_enemy(live, current.entity_id, c.entity_id) and c.is_alive and c.hp_current > 0
    ]
    charmer_id = _condition_source_entity(live, current, "charmed")
    if charmer_id is not None:
        enemies = [c for c in enemies if c.entity_id != charmer_id]
    return enemies


def _lowest_hp_target(enemies: list[Combatant]) -> Combatant | None:
    """SRD 5.2 monster gambit targeting — lowest current HP among ``enemies``,
    or ``None`` when the list is empty."""
    return min(enemies, key=lambda c: c.hp_current) if enemies else None


def _monster_action_imposed_statuses(monster: Monster, action: MonsterAction) -> set[str]:
    """The conditions ``action`` would land, read from typed data only.

    An activity's ``effects[].id`` points at one of the ACTOR's own effect
    definitions (``Monster.passive_effects``, the array the canonical cut used to
    drop); each definition names the SRD condition ids it imposes. A ref that
    does not resolve contributes nothing — the same ``effect_ref_unresolved``
    silence the resolver logs, never a guess from the prose.
    """
    by_id = {effect.id: effect for effect in monster.passive_effects}
    statuses: set[str] = set()
    for activity in action.activities:
        for ref in getattr(activity, "effects", ()) or ():
            definition = by_id.get(ref.id)
            if definition is not None:
                statuses.update(definition.statuses)
    return statuses


def _monster_control_predicate(
    monster: Monster, target: Combatant | None
) -> Callable[[MonsterAction], bool] | None:
    """The "this action lands something the target does not have yet" seam for
    ``rank_monster_actions``' control tier, or ``None`` when it cannot apply.

    ``None`` (no tier at all ⇒ the historical ranking, byte-for-byte) when the
    monster's corpus data types no effect definition, or the turn has no target
    to measure against. Otherwise a condition counts only when the target does
    not already carry it AND is not immune to it: re-grappling a creature that
    is already Grappled changes no state, and a Grapple against a creature
    immune to the condition cannot land it — in both cases the monster's action
    is better spent on its attacks (SRD 5.2 §Immunity: "it doesn't affect you in
    any way").
    """
    if target is None or not monster.passive_effects:
        return None
    held = {ac.condition for ac in target.conditions}

    def predicate(action: MonsterAction) -> bool:
        statuses = _monster_action_imposed_statuses(monster, action)
        return any(
            status not in held and not is_condition_immune(target, status, imposed=statuses)
            for status in statuses
        )

    return predicate


def _resolve_monster_activities(
    live: _LiveCombat,
    current: Combatant,
    monster_slug: str | None,
    skip_to_record_pass: bool,
    chosen_target: Combatant | None,
) -> tuple[list[_MonsterPart], tuple[MonsterAction, CastActivity, Spell] | None]:
    """Resolve monster activities: legacy-fallback when no template, or typed
    activity selection from a ``Monster`` template.

    Returns ``(parts, cast_selection)``. ``parts`` pairs each ``Activity`` to
    resolve with the stat-block action it comes from (``None`` for the
    legacy fallback's synthesized attack) — typically one action's own
    activities, a multiattack's sub-attacks and the actions it uses, in
    order (``expand_action_to_parts``). It is empty when the monster has no
    template and ``damage_dice`` doesn't parse, when a slug is unresolvable
    from the lib, or when the chosen action is a stat-block spellcast (its
    own activities are ``CastActivity`` wrappers, never resolver-ready
    directly). ``cast_selection`` is the ``(action, activity, spell)``
    tuple ``_monster_cast_candidate`` resolved when the ranked pick is a
    cast-only action, else ``None`` — mutually exclusive with non-empty
    ``parts``.
    """
    monster_parts: list[_MonsterPart] = []
    cast_selection: tuple[MonsterAction, CastActivity, Spell] | None = None
    if not skip_to_record_pass and monster_slug is None:
        # Legacy-fixture fallback — see _synthesize_attack_from_legacy_fields.
        synthesized = _synthesize_attack_from_legacy_fields(current)
        if synthesized is not None:
            monster_parts = [(None, synthesized)]
    if not skip_to_record_pass and monster_slug is not None:
        monster = get_lib_loader().get_monster(monster_slug)
        if monster is None:
            # Slug absent from the lib — no action this turn. Loud, never
            # silent; the turn still advances through the pass shape below.
            _LOGGER.warning("monster_unresolved slug=%s", monster_slug)
        else:
            # r199: the control tier. A monster whose typed data lands a
            # condition the chosen target does not have yet (Quick Grapple,
            # Constrict) picks that action above Multiattack — the measured
            # defect was that such an action was structurally unreachable, so
            # the condition the stat block prints could never be applied. The
            # predicate is None (tier absent, ranking unchanged) whenever the
            # corpus types no effect for this monster, which is every monster
            # but the ones this round typed.
            ranked = rank_monster_actions(
                monster.actions,
                is_available=lambda a: _monster_action_available(live, current, a),
                has_limited_use_remaining=lambda a: _monster_limited_cast_remaining(
                    live, current, a
                ),
                imposes_new_condition=_monster_control_predicate(monster, chosen_target),
            )
            monster_action = ranked[0] if ranked else None
            if monster_action is not None:
                _mark_monster_action_used(live, current, monster_action)
                action_activities = monster_action.activities
                if action_activities and all(
                    isinstance(a, CastActivity) for a in action_activities
                ):
                    candidate = _monster_cast_candidate(live, current, monster_action)
                    if candidate is not None:
                        cast_activity, spell = candidate
                        cast_selection = (monster_action, cast_activity, spell)
                else:
                    # hand the labelless-multiattack fallback the live
                    # distance + profile so it can prefer a sibling whose own range
                    # already covers the target (scout → longbow at 100 ft) instead
                    # of the first-listed melee weapon. Distance is the same path
                    # cost the movement gate below reads, so the two agree. A
                    # sibling the Multiattack uses "if available" sits the turn
                    # out while it isn't (a spent Recharge).
                    monster_parts = list(
                        expand_action_to_parts(
                            monster,
                            monster_action,
                            target_distance_ft=_monster_target_distance_ft(
                                live, current.entity_id, chosen_target
                            ),
                            behavior_profile=current.behavior_profile,
                            melee_reach_ft=current.melee_reach_ft,
                            is_available=lambda a: _monster_action_available(live, current, a),
                        )
                    )
    return monster_parts, cast_selection


def _summon_level_of(live: _LiveCombat, entity_id: str) -> int | None:
    """The slot level the spell summoning ``entity_id`` was cast at.

    ``None`` for any creature no cast of this combat owns — the token stays
    unresolved rather than guessed (§2).
    """
    summon = live.summons.get(entity_id)
    return summon.slot_level if summon is not None else None

def _monster_context_kwargs(
    live: _LiveCombat,
    current: Combatant,
    target_list: list[Combatant],
    payload: dict[str, Any],
    *,
    origin_cell: str | None = None,
) -> dict[str, Any]:
    """The ``build_activity_context`` keyword block shared by every monster
    resolution path (the mundane attack site; the stat-block
    spellcast path) — every argument that depends only on the caster + its
    target list + the per-entity hydration ``payload`` the caller already
    computed, never on the specific activity/spell being resolved (those
    stay per-call: ``slot_level``, ``base_spell_level``,
    ``spellcasting_ability``, ``concentration``, ``source_passive_effects``,
    ``spell_book``). Extracted from the historical single monster attack
    site so a second monster resolution branch can share it byte-for-byte
    rather than re-deriving it (and to hold ``advance_monster_turn`` under
    the McCabe ceiling). ``origin_cell`` is an area's point of origin, which
    cover is measured from (``_target_cover_map``).
    """
    target_unseen, attacker_unseen_by = _target_visibility_maps(live, current, target_list)
    attacker_invisibility_pierced_by, target_invisibility_pierced = _invisibility_pierced_maps(
        live, current, target_list
    )
    return {
        "passive_damage_modifiers": payload["passive_damage_modifiers"],
        "save_modifiers": payload["save_modifiers"],
        "check_modifiers": payload["check_modifiers"],
        "d20_test_penalty": payload["d20_test_penalty"],
        "target_cover": _target_cover_map(
            live, current.entity_id, target_list, origin_cell=origin_cell
        ),
        "target_distance_ft": _target_distance_map(live, current.entity_id, target_list),
        # SRD 5.2 §Actions in Combat — Dodge: a dodging target imposes
        # disadvantage only while it can also see THIS attacker (C16b's
        # "can see the attacker" conjunct).
        "target_dodging": {
            t.entity_id: _dodge_benefit_active(live, t) and _combatant_can_see(live, t, current)
            for t in target_list
        },
        # SRD 5.2 §Actions in Combat — Help: a monster attacker
        # can be granted Help by one of ITS OWN allies (another monster).
        "target_help_advantage": _target_help_advantage_map(live, current.entity_id, target_list),
        "attacker_grappler_id": _condition_source_entity(live, current, "grappled"),
        "target_unseen": target_unseen,
        "attacker_unseen_by": attacker_unseen_by,
        "attacker_invisibility_pierced_by": attacker_invisibility_pierced_by,
        "target_invisibility_pierced": target_invisibility_pierced,
        # SRD 5.2 Frightened line-of-sight gate (C16b): PRE-RESOLVED
        # attacker-own-perception flag.
        "attacker_fear_source_in_sight": _fear_source_in_sight(live, current),
        # SRD 5.2 "Ranged Attacks in Close Combat": per-attacker
        # flag; a monster attack/cast carries no ``Weapon``, so this only
        # ever matters via the shared attack.py penalty gate.
        "attacker_ranged_in_melee": _hostile_adjacent_to_attacker(live, current),
        # SRD 5.2 §Weapon Mastery — Vex / Sap: a monster attack
        # never PRODUCES a proc itself (no ``Weapon``), but it can be a
        # vex-grant target or sap-mark holder from a prior PC weapon hit.
        "attacker_vex_advantage": _attacker_vex_advantage_map(live, current.entity_id, target_list),
        "attacker_sapped": current.entity_id in live.sap_marks,
        # C18 §Monster action economy — Legendary Resistance sidecars:
        # a monster's own attack/cast/legendary-action resolution
        # can roll a save against IT (e.g. a Counterspell-style effect, or
        # this same monster as the target of another monster's save-kind
        # activity) just as readily as the PC path can.
        "legendary_resistance_armed": payload["legendary_resistance_armed"],
        "legendary_resistances_remaining_by_entity": payload[
            "legendary_resistances_remaining_by_entity"
        ],
        # C18 §Monster action economy — Pack Tactics / Sunlight Sensitivity:
        # a monster attack/cast is the only path either trait's
        # bearer resolves through today, so both sidecars are projected
        # here rather than at the PC-only sneak-attack call site.
        "pack_tactics_ally_adjacent": _pack_tactics_map(live, current, target_list),
        "attacker_in_sunlight": live.scene_sunlight,
        # C18 §Monster action economy — Undead Fortitude is a
        # TARGET-side trait (a monster attacker can just as easily be
        # SWINGING AT a zombie ally as fielding one), so this handshake
        # dict is threaded at every ``build_activity_context`` call site,
        # not just the monster-attacker ones. See ``_LiveCombat.
        # undead_fortitude_holds`` / the ``ActivityResolutionContext``
        # field docstring for the full contract — this MUST be the live
        # object itself, never a copy.
        "undead_fortitude_holds": live.undead_fortitude_holds,
        # C21: a transformed monster's turn rolls at its form's real
        # scores and Proficiency Bonus; ``None`` (the legacy model) otherwise.
        # A monster's own save action (the Air Elemental's Whirlwind) has no
        # attack roll to hang its damage on, so its ``@mod`` reads the same
        # stat-block scores its attacks do; ``build_activity_context`` then
        # skips the spell save DC instead of deriving one from an ability the
        # stat block never had.
        "stat_block_magnitudes": _stat_block_magnitudes_of(live, current),
        # A summoned creature's stat block scales off the slot that conjured
        # it; ``None`` for everything else.
        "summon_level": _summon_level_of(live, current.entity_id),
        # The whole fight, so an AC mode applied to a TARGET is readable from
        # the attacker's resolution: the sidecar is keyed by entity, and the
        # defender of a monster attack is never the casting wizard, so without
        # the roster ``build_activity_context`` could not reach the defender's
        # sheet and Mage Armor's ``ac.calc`` read as nothing.
        "roster": live.initiative,
    }


def _resolve_monster_cast(
    live: _LiveCombat,
    current: Combatant,
    chosen_target: Combatant,
    action: MonsterAction,
    activity: CastActivity,
    spell: Spell,
) -> None:
    """Resolve a stat-block spellcast (SRD 5.2 §Spellcasting)
    chosen by ``_resolve_monster_activities``/``_monster_cast_candidate``.

    Mirrors the PC on-turn ``cast_spell`` path structurally — the same
    ``build_activity_context`` + ``resolve_activity`` loop over
    ``spell.activities``, the same ``_emit_spell_cast`` metadata event — but
    against the monster's OWN stat block instead of a slot pool:
    ``spell_slots_by_entity`` (PC-only) is never touched here. ``slot_level``
    is the stat block's own PRINTED cast level (``activity.spell.level``,
    e.g. the mage's Fireball at level 4) when set, else the spell's own
    base ``level`` — the same "wrapper override, else the referenced
    spell's own level" split ``resolve_cast`` (``activities/cast.py``) uses
    for an item wrapper. Spellcasting ability precedence:
    ``activity.spell.ability`` when the stat-block entry forces one
    (Foundry's "" sentinel means "use the caster's own"), else the
    monster's own ``Combatant.spellcasting_ability`` (hydrated from
    ``Monster.spellcasting_ability`` in ``_build_foe_combatants``).

    A spell that places an area resolves against the creatures its best aim
    affects (``_monster_aim``; the aim's point of origin is where cover is
    measured from), reported in an ``AreaTargeted`` whose ``source_id`` is
    the spell's slug; any other spell resolves against ``chosen_target``. No
    movement-closing gambit (the caller never reads the turn's parts for
    range on this path, so a cast always resolves from the monster's current
    position).
    """
    target_list = [chosen_target]
    cover_origin: str | None = None
    slot_level = activity.spell.level if activity.spell.level is not None else spell.level
    spellcasting_ability = activity.spell.ability or current.spellcasting_ability

    payload = _build_hydration_payload(live, caster=current)
    pre_event_count = len(live.event_log)
    _emit_spell_cast(live, current.entity_id, spell, slot_level)
    area = _monster_area(spell.activities)
    aim = None if area is None else _monster_aim(live, current, *area, _range_ft(spell.range))
    if area is not None and aim is not None:
        target_list = _place_monster_area(live, current, spell.slug, area[1], aim)
        cover_origin = aim.origin
    actx = build_activity_context(
        current,
        target_list,
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=slot_level,
        base_spell_level=spell.level,
        spellcasting_ability=spellcasting_ability,
        concentration=spell.concentration,
        source_passive_effects=list(spell.passive_effects),
        spell_book=_build_cast_spell_book(spell.activities),
        **_monster_context_kwargs(live, current, target_list, payload, origin_cell=cover_origin),
    )
    for child_activity in spell.activities:
        resolve_activity(child_activity, actx)

    # SRD 5.2 §Actions in Combat — Help; §Weapon Mastery — Vex / Sap: a
    # monster cast whose resolved spell includes an ``AttackActivity``
    # (e.g. Scorching Ray, reached via a Fiery Rays legendary
    # action) is an attack roll like any other — a Help/Vex grant folded
    # into it above must be consumed here too, or it leaks into a later,
    # unrelated roll. A save-only spell (Fireball, Cone of Cold, ...) never
    # calls this: the shared helper's pop functions only remove a grant
    # when an ``AttackRolled`` actually fired, but gating on the activity
    # SHAPE here (rather than relying on that) keeps a save-only cast from
    # even attempting the lookup. No mastery-proc fold either way — a
    # monster cast has no ``Weapon``.
    if any(isinstance(a, AttackActivity) for a in spell.activities):
        _consume_attack_roll_grants(live, current, target_list, pre_event_count)

    # SRD §Innate/Prepared Spellcasting "N/Day" — spend the chosen
    # activity's per-day use. A ``None`` lookup (no tracked entry — an
    # at-will spell) is a no-op; guarded rather than asserted since
    # ``_monster_cast_candidate`` already filtered to a use-bearing or
    # unlimited activity, but this stays defensive against a future caller.
    entry = live.monster_action_uses_by_entity.get(current.entity_id, {}).get(action.slug)
    key = f"{action.slug}:{activity.id}"
    if entry is not None and key in entry.uses_remaining:
        entry.uses_remaining[key] = max(0, entry.uses_remaining[key] - 1)

    # The PC path's fold; ``concentration_max_rounds`` stays on the default
    # here — the same recorded follow-up as the mundane monster-attack site.
    _fold_resolution_outcome(live, current, spell=spell, actx=actx, pre_event_count=pre_event_count)
    _sync_legendary_resistance(live, pre_event_count)


def _eligible_legendary_actor(live: _LiveCombat, actor_id: str | None) -> Combatant:
    """SRD 5.2 §Legendary Actions — which encounter member (if any) may
    spend a legendary action right now.

    A candidate qualifies when: it is an encounter member with a
    legendary-action pool (``legendary_actions_max > 0``); it is alive and
    above 0 HP; it is not Incapacitated (``conditions_block_actions``); it
    has at least one use left; a turn has ended ("immediately after
    another creature's turn") and that turn was NOT the candidate's own;
    and that turn-end window hasn't already spent a legendary action
    ("only one of these actions can be taken at a time").

    ``actor_id=None`` picks the first eligible member in initiative order;
    an explicit ``actor_id`` picks that member or nothing. Either way,
    raises ``IntentRejectedError("no_legendary_action", ...)`` when the
    result is empty.
    """
    if live.last_ended_turn is None:
        raise IntentRejectedError("no_legendary_action", "no turn has ended yet")
    ended_round, ended_actor_id = live.last_ended_turn

    def qualifies(monster: Combatant) -> bool:
        if monster.entity_id not in live.encounter_ids:
            return False
        if monster.legendary_actions_max <= 0:
            return False
        if not monster.is_alive or monster.hp_current <= 0:
            return False
        if conditions_block_actions(_condition_names(monster)):
            return False
        if monster.legendary_actions_remaining < 1:
            return False
        if ended_actor_id == monster.entity_id:
            return False
        return (ended_round, ended_actor_id, monster.entity_id) not in live.legendary_windows_used

    if actor_id is not None:
        candidate = next((c for c in live.initiative if c.entity_id == actor_id), None)
        if candidate is None or not qualifies(candidate):
            raise IntentRejectedError(
                "no_legendary_action",
                f"actor_id={actor_id!r} is not eligible for a legendary action right now",
            )
        return candidate

    for combatant in live.initiative:
        if qualifies(combatant):
            return combatant
    raise IntentRejectedError(
        "no_legendary_action", "no encounter member is eligible for a legendary action right now"
    )


def _spend_legendary_use(live: _LiveCombat, monster: Combatant, action_slug: str) -> None:
    """SRD 5.2 §Legendary Actions — "expends one use whenever it takes a
    Legendary Action": decrement the pool, close the turn-end window
    (``live.legendary_windows_used``), then emit ``LegendaryActionUsed``
    BEFORE the chosen action's own events (per ``_take_legendary_action``).
    """
    assert live.last_ended_turn is not None  # narrowed by the eligibility gate
    monster.legendary_actions_remaining -= 1
    live.legendary_windows_used.add((*live.last_ended_turn, monster.entity_id))
    _emit(
        live,
        LegendaryActionUsed(
            actor_id=monster.entity_id,
            action_slug=action_slug,
            uses_remaining=monster.legendary_actions_remaining,
        ),
    )


def _monster_source_passive_effects(live: _LiveCombat, actor: Combatant) -> list[Any]:
    """The effect DEFINITIONS a monster's own activities may ref by id.

    ``build_activity_context``'s ``source_passive_effects`` is what
    ``apply_activity_effects`` resolves ``activity.effects[].id`` against; the
    PC path hands it ``spell.passive_effects`` / ``item.passive_effects``, and
    the monster path passed ``[]`` unconditionally — so every monster rider in
    the corpus hit ``effect_ref_unresolved`` (r198: 58 grapple actions, 0 typed
    statuses). A stat block's effects live on the actor itself
    (``Monster.passive_effects``); a legendary action, a summon's commanded
    attack and a stat-block opportunity attack share this seam and inherit the
    same definitions. An unresolvable slug (a legacy fixture with no template)
    contributes nothing, exactly as before.
    """
    slug = live.monster_slug_by_entity.get(actor.entity_id)
    if slug is None:
        return []
    monster = get_lib_loader().get_monster(slug)
    return list(monster.passive_effects) if monster is not None else []


def _resolve_monster_attack_activities(
    live: _LiveCombat,
    actor: Combatant,
    target_list: list[Combatant],
    activities: Sequence[Any],
    *,
    is_opportunity_attack: bool = False,
    cover_origin: str | None = None,
) -> None:
    """Resolve a monster's own attack/save ``Activity`` list against
    ``target_list``: Shield drain, ``build_activity_context`` (via
    ``_monster_context_kwargs``), the ``resolve_activity`` loop,
    Help/Vex/Sap grant consumption, mastery-proc fold, the Hide break
    clause, and concentration/effect-lifecycle writeback — the ONE
    resolution sequence shared by the mundane monster-attack branch of
    ``advance_monster_turn`` (which first expands multiattack via
    ``expand_action_to_parts`` and may close distance with a move before
    calling this, through ``_resolve_monster_parts``) and a legendary
    action's own attack/save entry
    (``_take_legendary_action``, which never moves and always resolves
    from the monster's current position — same as a stat-block spellcast,
    ``_resolve_monster_cast``) and a stat-block creature's opportunity attack
    (``is_opportunity_attack``). Extracted so a
    future hook added to one caller can't silently miss the other.
    ``cover_origin`` is an area's point of origin (``_resolve_monster_area``).
    """
    # SRD §Reactions — drain the attacked PC's pending ``hit_by_attack``
    # reaction (Shield) BEFORE the sidecar projection below, so the
    # just-applied +5 AC effect folds into THIS attack's hydration
    # payload — the monster-attacker / PC-defender direction. Shield's own
    # resolution draws no dice, so the attack's d20 keeps its seed-stream
    # position. SRD 5.2 Shield answers "being hit by an attack roll": a
    # save action (a Breath Weapon) leaves it readied.
    if any(isinstance(a, AttackActivity) for a in activities):
        _drain_targeted_reactions(
            live,
            trigger="hit_by_attack",
            triggering_actor_id=actor.entity_id,
            targets=target_list,
        )
    # The orchestrator owns the per-entity passive sidecars; project them
    # once and hand the two dicts ``build_activity_context`` needs in (it
    # stays pure — no orchestrator import, no double-compute). Mirrors the
    # PC site.
    payload = _build_hydration_payload(live, caster=actor)
    pre_event_count = len(live.event_log)
    # Monster magnitudes (save DC = 8 + attack_bonus, mod = attack_bonus)
    # are reproduced by ``build_activity_context``'s ``entity_type ==
    # "Monster"`` branch — no per-call slot/spell parameters apply to a
    # mundane monster attack. SRD 5.2 §Weapon Proficiency — "A monster is
    # proficient with any weapon in its stat block": left on the default
    # (True) — a monster's ``Combatant.weapon_proficiencies`` is never
    # explicitly set (the unset sentinel), so it would resolve to True via
    # ``_is_proficient_with_weapon`` anyway; the SRD rule makes the gate
    # a no-op for every monster.
    actx = build_activity_context(
        actor,
        target_list,
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=None,
        base_spell_level=None,
        spellcasting_ability=None,
        concentration=False,
        # r199: the monster's OWN effect definitions, so a stat-block rider
        # ("the target has the Grappled condition") can resolve. Was `[]`, which
        # silently dropped every monster effect in the corpus.
        source_passive_effects=_monster_source_passive_effects(live, actor),
        # Monster/reaction paths don't delegate casts yet — PC-path
        # delegation lives in _build_cast_spell_book; extending it here
        # is a recorded follow-up.
        spell_book={},
        is_opportunity_attack=is_opportunity_attack,
        **_monster_context_kwargs(live, actor, target_list, payload, origin_cell=cover_origin),
    )
    # A monster attack's target is a DEFENDER whose sheet this resolution
    # never held: ``Combatant.ac`` is the authority on this path, so the
    # AC-mode sidecar would regain a second source here. Left as a recorded
    # follow-up (reports/ENGINE_WORKSPACE_PROGRESS.md, round 100).
    for activity in activities:
        # Monster attacks carry their damage on the AttackActivity itself,
        # not a separate Weapon (unlike the PC weapon path).
        resolve_activity(activity, actx, weapon=None)
    # SRD 5.2 §Actions in Combat — Help; §Weapon Mastery — Vex / Sap:
    # one-use pops, shared with the C18 monster-cast
    # attack-roll branch via ``_consume_attack_roll_grants``. A monster
    # attack never produces a Vex/Sap proc itself (no ``Weapon``), so
    # the fold below is a no-op here in practice — wired for symmetry /
    # future monster weapons.
    _consume_attack_roll_grants(live, actor, target_list, pre_event_count)
    _fold_mastery_procs(live, actor.entity_id, actx)
    # SRD 5.2 §Actions in Combat — Hide, break clause: mirrors the PC
    # site. A monster hidden via a prior Hide loses Invisible the
    # moment IT makes an attack roll (no monster gambit currently
    # issues a Hide intent, so this is defensive symmetry, not a
    # reachable path today).
    _break_hide(live, actor.entity_id)
    # Symmetric concentration writeback for spellcaster monsters
    # (mirrors the PC path; no-op for non-caster monsters).
    _writeback_concentration(live, actor, pre_event_count)
    # ``concentration_max_rounds`` stays on the default (None) here: the
    # monster path has no typed ``Spell`` in scope (monster stat-block
    # casts resolve straight off the monster's own activities, not a
    # fetched Spell). Monster concentration effects therefore remain
    # cascade-governed only — no timed expiry.
    _record_effect_lifecycle_links(live, actor, pre_event_count)
    _sync_legendary_resistance(live, pre_event_count)


def _take_legendary_action(live: _LiveCombat, monster: Combatant) -> None:
    """SRD 5.2 §Legendary Actions — spend one of ``monster``'s legendary-
    action uses on the first offensive entry in its ``legendary_actions``
    list (stat-block order) that is currently available.

    "Offensive" mirrors ``_monster_cast_candidate``'s bar: a cast-only
    action (all ``CastActivity``) resolves through
    ``_monster_cast_candidate``/``_resolve_monster_cast`` — the same
    at-will/N-per-day gating and Spell lookup a stat-block spellcast on the
    monster's own turn uses; a non-cast action qualifies when it carries an
    ``AttackActivity``/``SaveActivity``/``DamageActivity`` and resolves
    through ``_resolve_monster_attack_activities`` — an area through
    ``_resolve_monster_area``, and skipped like any unavailable entry when it
    affects no enemy. A ``utility``-only entry (e.g.
    Pounce) is never offensive and is skipped. Only entries whose
    ``legendary_cost`` is unset or ``1`` are considered — the bundled
    corpus carries no multi-point legendary action today (see BACKLOG for
    the point-pool follow-up).

    Raises ``IntentRejectedError("no_legendary_action", ...)`` when the
    monster's template is unresolvable, no PC target is legal, or nothing
    on the list qualifies.
    """
    slug = live.monster_slug_by_entity.get(monster.entity_id)
    template = get_lib_loader().get_monster(slug) if slug else None
    if template is None:
        raise IntentRejectedError(
            "no_legendary_action",
            f"actor_id={monster.entity_id!r} has no resolvable monster template",
        )
    target = _lowest_hp_target(_select_monster_targets(live, monster))
    if target is None:
        raise IntentRejectedError(
            "no_legendary_action", f"actor_id={monster.entity_id!r} has no legal target"
        )

    for action in template.legendary_actions:
        if action.legendary_cost not in (None, 1):
            continue
        activities = action.activities
        if activities and all(isinstance(a, CastActivity) for a in activities):
            candidate = _monster_cast_candidate(live, monster, action)
            if candidate is None:
                continue
            cast_activity, spell = candidate
            _spend_legendary_use(live, monster, action.slug)
            _resolve_monster_cast(live, monster, target, action, cast_activity, spell)
            return
        is_offensive = any(
            isinstance(a, (AttackActivity, SaveActivity, DamageActivity)) for a in activities
        )
        if not is_offensive:
            continue
        area = _monster_area(activities)
        aim = None if area is None else _action_area_aim(live, monster, area)
        if area is not None and aim is None:
            continue
        _spend_legendary_use(live, monster, action.slug)
        if area is None or aim is None:
            _resolve_monster_attack_activities(live, monster, [target], activities)
        else:
            _resolve_monster_area(live, monster, action.slug, area[1], aim, activities)
        return

    raise IntentRejectedError(
        "no_legendary_action", f"actor_id={monster.entity_id!r} has no usable legendary action"
    )


# ── Internal live-combat state ──────────────────────────────────────────────


@dataclass
class _LiveCombat:
    """Per-combat state held by the orchestrator.

    In-memory only: keeping it here lets the boundary surface be exercised
    standalone, without coupling to a host's storage.
    """

    handle_id: str
    session_id: str
    initiative: list[Combatant]
    party_ids: set[str]
    encounter_ids: set[str]
    topology: GridTopology
    rng: random.Random
    event_queue: asyncio.Queue[CombatEvent | None]
    scene_location_id: str
    # C18 §Monster action economy — SRD 5.2 stat-block trait "Sunlight
    # Sensitivity": whole-scene sunlight flag, projected from
    # ``GridScene.sunlight`` at ``start_combat``. Read by ``_monster_context_
    # kwargs`` into ``ActivityResolutionContext.attacker_in_sunlight``.
    scene_sunlight: bool = False
    current_turn_index: int = 0
    round_number: int = 1
    ended: bool = False
    final_outcome: CombatOutcome | None = None
    # each combatant's cell, per entity_id (read through ``topology``)
    actor_zone: dict[str, str] = field(default_factory=dict)
    # SRD 5.2 Opportunity Attacks: the weapon slug each character makes its
    # opportunity attacks with (``_opportunity_attack_weapon_slug``); a
    # character absent from it makes an Unarmed Strike.
    opportunity_attack_weapons: dict[str, str] = field(default_factory=dict)
    # monster-template slug, per entity_id (drives gambit lookup in
    # ``advance_monster_turn``). Absent for PCs and slug-less NPCs.
    monster_slug_by_entity: dict[str, str] = field(default_factory=dict)
    # SRD §Encounter XP — monster.xp_value per encounter member, indexed by
    # entity_id. Used by ``end_combat`` to compute total XP awarded.
    xp_value_by_entity: dict[str, int] = field(default_factory=dict)
    # Outcome-population running state (Agent 03). The event listener wired in
    # ``_emit`` mutates these as each ``CombatEvent`` flows through, so
    # ``end_combat`` can project a populated outcome from a single source of
    # truth. Per-effect handlers do NOT mutate ``Combatant.hp_current`` /
    # ``temp_hp`` / ``is_alive`` directly — the orchestrator owns end-state
    # derivation from the event stream.
    event_log: list[CombatEvent] = field(default_factory=list)
    tracked_hp: dict[str, int] = field(default_factory=dict)
    tracked_temp_hp: dict[str, int] = field(default_factory=dict)
    # C18 §Monster action economy — SRD 5.2 stat-block trait
    # "Undead Fortitude" live write-back handshake (see
    # ``ActivityResolutionContext.undead_fortitude_holds`` for the full
    # contract). THE SAME dict object is threaded into every
    # ``build_activity_context`` call (never a disposable per-resolution
    # copy) so ``activities/apply.py`` mutating it from inside a resolution
    # is visible here; ``_emit_apply_damage`` pops the entry for the exact
    # ``DamageApplied`` the save fired for. Private — never surfaced on a
    # public event or view.
    undead_fortitude_holds: dict[str, bool] = field(default_factory=dict)
    # active condition set per target_id; final outcome lifts permanent ones.
    active_conditions: dict[str, set[str]] = field(default_factory=dict)
    # active effect: target_id → list of full ActiveEffect documents.
    # Foundry-shaped: identity is (target_id, effect.id, effect.origin),
    # so two PCs both casting Bless on the same target produce two
    # entries (distinct origins) rather than collapsing into one record.
    active_effects: dict[str, list[ActiveEffect]] = field(default_factory=dict)
    # dead encounter members, in death order (drives loot + XP projection).
    deaths_recorded: list[DeathRecord] = field(default_factory=list)
    dead_ids: set[str] = field(default_factory=set)
    # pc_id → {slot_or_feature_label: count_used} (from EffectApplied with
    # concentration / known feature names — projected onto expended_resources).
    expended_resources: dict[str, dict[str, int]] = field(default_factory=dict)
    # current actor (set at TurnStarted) — credited as killer when a non-PC
    # drops to ≤0 HP and the orchestrator synthesizes a Death event.
    current_actor_id: str | None = None
    # SRD §Spellcasting + class features — caster resource pools, indexed by
    # entity_id. Populated at ``start_combat`` from ``PartyMemberSpec``;
    # consumed by ``_build_hydration_payload`` to project the per-caster
    # sidecar payload before each evaluator invocation. Non-PCs are absent
    # from these maps (treated as "no spells / no counters" by the handlers).
    spell_slots_by_entity: dict[str, dict[int, int]] = field(default_factory=dict)
    # SRD §Multiclassing — Pact Magic: a second, independent slot pool
    # (Warlock's Pact Magic feature); a cast draws from Spellcasting first,
    # then Pact — see ``_take_spell_slot``.
    pact_slots_by_entity: dict[str, dict[int, int]] = field(default_factory=dict)
    spells_known_by_entity: dict[str, list[str]] = field(default_factory=dict)
    custom_counters_by_entity: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    # SRD §Concentration — persistent IEffect parent/child lifecycle graph.
    # ``concentration_chain[caster_id] = [(target_id, effect.id, effect.origin), …]``
    # — full Foundry-shaped identity tuple per emitted effect so two PCs
    # both casting Bless never collapse into a single record. Survives
    # across turns; the transient ``ctx.parent_chain`` on RuntimeContext
    # is per-evaluation only and cannot be relied on for cross-turn
    # cascade walks. Written by ``_record_effect_lifecycle_links`` after
    # each evaluator run; read by ``_drop_concentration`` when a
    # concentration drop must cascade EffectExpired + ConditionRemoved.
    concentration_chain: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    # SRD 5.2 §Concentration — "up to 1 minute, 1 hour, or some other
    # duration": the caster-keyed countdown of the concentration spell's own
    # MAXIMUM duration, in rounds, decremented by the
    # ``engine:concentration-expiry`` turn_end hook at the CASTER's turn end
    # (Bless on three allies is 10 rounds total, not per-target). Sourced
    # from the typed Spell.duration at cast time — the per-effect Foundry
    # ``duration.rounds`` is display-only and unreliable (Bane ships 1).
    # Seeded effects (cross-combat persistence, a host concern) carry no
    # counter and remain cascade-governed only.
    concentration_rounds_remaining: dict[str, int] = field(default_factory=dict)
    # SRD §Conditions — per-effect condition lineage. Keyed by the
    # Foundry-shaped identity tuple ``(target_id, effect.id, effect.origin)``;
    # value is the list of ConditionType values that the named
    # effect-instance applied to that target. Walked on EffectExpired
    # (concentration_drop) to synthesize the matching ConditionRemoved
    # cascade. ``ActiveCondition.source_effect_id`` carries the same link on
    # each condition; this map is the orchestrator's per-effect index of it.
    conditions_by_effect: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    # SRD §Hold Person — end-of-turn repeat-save specs. Keyed by the
    # Foundry-shaped identity tuple ``(target_id, effect.id, effect.origin)``;
    # value is the list of pending saves the target rolls at the end
    # of each of their turns. Each spec carries the ability, DC, the
    # condition the spell applied, the source ``effect_name`` (for
    # ``ConcentrationDropped`` projection), and the caster_id (used to
    # clear ``concentration_chain`` on success). Populated by
    # ``_record_effect_lifecycle_links``; consumed by ``_run_end_of_turn_saves``.
    repeat_save_on_turn_end: dict[tuple[str, str, str], list[dict[str, Any]]] = field(
        default_factory=dict
    )
    # Per-call event subscribers — ``start_combat`` and ``end_combat`` push a
    # local list's ``append`` here to capture events emitted during their
    # body, then pop it on return. This is how those entry points surface
    # an ``events`` list on the result envelope without changing the
    # canonical queue-based delivery for ``narration_events``.
    event_listeners: list[Any] = field(default_factory=list)
    # SRD §Reactions — 's pre-armed reaction queue (see
    # docs/dev/reaction-queue.md). Populated by a ``"ready"`` intent; drained
    # (popped + resolved) by ``_pop_pending_reaction`` when a matching trigger
    # is observed from another combatant's intent.
    pending_reactions: list[_PendingReaction] = field(default_factory=list)
    # SRD §Reactions / one-round buffs — a reaction-applied effect (Shield)
    # fires DURING another actor's turn, so the generic caster-turn-end
    # duration tick (``_tick_durations_at_turn_end``) won't recur until the
    # reactor's own NEXT turn ends — one full turn too late for a "until the
    # start of your next turn" effect. Keyed by the effect owner/caster's
    # entity_id -> list of (target_id, effect.id, effect.origin) identities to
    # expire the moment that owner's OWN next TurnStarted fires (see
    # ``_emit_apply_turn_started``). Populated by ``_resolve_readied_spell_cast``.
    reaction_effects_pending_expiry: dict[str, list[tuple[str, str, str]]] = field(
        default_factory=dict
    )
    # SRD 5.2 §Actions in Combat — Help, "Assist an Attack Roll":
    # *"You momentarily distract an enemy within 5 feet of you, giving
    # Advantage to the next attack roll by one of your allies against that
    # enemy. This benefit expires at the start of your next turn."* Keyed by
    # the HELPED-AGAINST target's entity_id -> the list of helper entity_ids
    # who have an outstanding, unconsumed grant against that target (one
    # entry per ``help`` intent — a second helper against the same target
    # queues a second grant, each independently consumable by one ally
    # attack). Populated by the ``"help"`` intent branch in
    # ``submit_player_intent``; consumed (popped) after an allied attack
    # against the target rolls in this resolution (see the one-use pop
    # comment near the PC/monster attack-context build sites); expired per
    # helper at that helper's OWN next ``TurnStarted``
    # (``_emit_apply_turn_started``), never at the target's turn.
    help_grants: dict[str, list[str]] = field(default_factory=dict)
    # SRD 5.2 §Actions in Combat — Hide: the set of entity_ids
    # currently benefiting from a successful Hide check's Invisible
    # condition. Populated by ``_handle_hide`` on a successful DC 15
    # Dexterity (Stealth) check (mirrors the ``ConditionApplied`` fold onto
    # ``Combatant.conditions``); consumed (discarded, with a matching
    # ``ConditionRemoved``) the moment its member "makes an attack roll" or
    # "casts a spell with a Verbal component" (SRD 5.2 Hide's break clause).
    # The found-DC / check total is intentionally NOT stored — the SRD's
    # "an enemy finds you" and "a sound louder than a whisper" break clauses
    # are host/Search concerns out of this engine's scope (no Search action
    # or perception-vs-stealth contest is modeled here).
    hidden_entities: set[str] = field(default_factory=set)
    # SRD 5.2 Alert — Initiative Swap: party members that already swapped
    # their Initiative this roll. One swap per Initiative roll.
    alert_swaps_used: set[str] = field(default_factory=set)
    # SRD 5.2 §Weapon Mastery — Vex: *"If you hit a creature
    # with this weapon and deal damage to the creature, you have Advantage
    # on your next attack roll against that creature before the end of your
    # next turn."* Keyed ATTACKER entity_id -> {TARGET entity_id ->
    # rounds-remaining}. A fresh proc sets rounds-remaining to 2, or 1 for a
    # proc on another creature's turn ("before the end of your NEXT turn"
    # spans this turn's remainder plus the attacker's whole next turn, but an
    # off-turn proc has no remainder of the attacker's own turn left to
    # span); re-procing the SAME attacker/target pair REFRESHES to that same
    # value rather than stacking (riders are
    # non-stacking). Decremented for the ENDING actor's own grants at
    # ``_end_turn_and_advance`` (dropping entries that reach 0); the
    # one-use consumption pop (after that attacker's next attack roll vs
    # the SAME target lands) happens at the PC/monster attack-context build
    # sites, mirroring ``help_grants``' ``_pop_help_grant``. Populated by
    # folding ``ActivityResolutionContext.mastery_procs`` post-resolution.
    vex_grants: dict[str, dict[str, int]] = field(default_factory=dict)
    # SRD 5.2 Rage — "Take a Bonus Action to extend your Rage." The barbarians
    # who took that Bonus Action this turn. The ``engine:rage-extension`` hook
    # reads and clears the mark at that barbarian's turn end; the other two
    # extensions (an attack roll against an enemy, an enemy's saving throw)
    # are read from ``event_log``.
    rage_bonus_extensions: set[str] = field(default_factory=set)
    # SRD 5.2 §Weapon Mastery — Sap: *"If you hit a creature
    # with this weapon, that creature has Disadvantage on its next attack
    # roll before the start of your next turn."* Keyed SAPPED-entity
    # entity_id -> the SOURCE attacker's entity_id (single mark; a re-proc
    # from a DIFFERENT attacker overwrites rather than stacking).
    # Cleared for the source attacker's outstanding marks at that
    # attacker's OWN next ``TurnStarted`` (``_emit_apply_turn_started``,
    # beside the Help-grant expiry sweep); the one-use consumption pop
    # (after the sapped entity's own next attack roll resolves) happens at
    # the PC/monster attack-context build sites. Populated by folding
    # ``ActivityResolutionContext.mastery_procs`` post-resolution.
    sap_marks: dict[str, str] = field(default_factory=dict)
    # SRD 5.2 §Weapon Mastery — Slow: *"If you hit a creature
    # with this weapon and deal damage to it, you can reduce its Speed by 10
    # feet until the start of your next turn. If the creature is hit more
    # than once by weapons that have this property, the Speed reduction
    # doesn't exceed 10 feet."* Keyed SLOWED-entity entity_id -> the set of
    # SOURCE attacker entity_ids whose marks are outstanding. The reduction
    # is a FLAT -10 ft in ``_effective_speed`` whenever the set is non-empty
    # (the SRD's non-stacking cap — a re-proc
    # refreshes, never stacks); ``_clamp_movement_budget`` re-projects the
    # target's unspent budget on application. Each source's marks are
    # cleared at that SOURCE attacker's own next ``TurnStarted``
    # (``_emit_apply_turn_started``, beside the Sap sweep); an entry whose
    # set empties is dropped. Populated by folding
    # ``ActivityResolutionContext.mastery_procs`` post-resolution.
    slow_marks: dict[str, set[str]] = field(default_factory=dict)
    # C18 §Monster action economy — per-entity, per-action-slug limited-use
    # state (recharge actions, N/Day trait uses). Hydrated at ``start_combat``
    # from the monster template (``_hydrate_monster_action_uses``); mutated by
    # ``_roll_recharges`` at the owner's own turn start. Absent for PCs and
    # template-less foes.
    monster_action_uses_by_entity: dict[str, dict[str, MonsterActionUses]] = field(
        default_factory=dict
    )
    # C18 — idempotency guard for ``_run_monster_turn_start``: the
    # ``(round_number, entity_id)`` pair the turn-start mechanics (legendary
    # reset, recharge rolls, regeneration) last ran for. ``None`` before the
    # first driven monster turn. Keyed by the creature, not its slot: a
    # creature that leaves the initiative order mid-round (C21) shifts every
    # later slot back by one, and a slot key would then skip the next
    # monster's turn start or run one twice.
    monster_turn_start_done: tuple[int, str] | None = None
    # C18 §Monster action economy — legendary actions. The
    # ``(round_number, actor_id)`` of the LAST turn to end (recorded in
    # ``_end_turn_and_advance`` before the round/turn-index bump), read by
    # ``_eligible_legendary_actor``'s "immediately after ANOTHER creature's
    # turn" gate. ``None`` before any turn has ended.
    last_ended_turn: tuple[int, str] | None = None
    # The current actor that left the initiative order while an intent or a
    # legendary action was resolving (C21). The turn passes on only once that
    # resolution completes (``_hand_off_departed_turn``), so every event it
    # emits precedes the next ``TurnStarted``. While it is set no turn is
    # running: the pointer names the next creature to open.
    departed_actor_id: str | None = None
    # C18 §Monster action economy — one-shot guard for "only one of these
    # actions can be taken at a time": every ``(round, ended_actor_id,
    # monster_id)`` window that has already spent a legendary action.
    # Never cleared — a window is identified by the (round, ended actor)
    # pair, which never repeats, so old entries are simply inert going
    # forward rather than needing eviction.
    legendary_windows_used: set[tuple[int, str, str]] = field(default_factory=set)
    # C18 §Monster action economy — Legendary Resistance. SRD 5.2:
    # "If the monster fails a saving throw, it can choose to succeed
    # instead." The engine has no mid-resolution round-trip to a host, so the
    # choice is a PRE-ARMED declaration: ``resolve_legendary_resistance``
    # increments the bearer's count here BEFORE the save it will convert is
    # even rolled. Keyed entity_id -> armed-but-not-yet-consumed use count.
    # ``activities/save_primitive.roll_save`` consults + decrements a
    # per-resolution COPY of this dict (projected by
    # ``_build_hydration_payload``); ``_sync_legendary_resistance`` reconciles
    # this authoritative dict (and ``Combatant.legendary_resistances_remaining``)
    # from the ``LegendaryResistanceUsed`` events a resolution actually
    # emitted (the "read the events" pattern the reaction drains use).
    legendary_resistance_armed: dict[str, int] = field(default_factory=dict)
    # C18 §Monster action economy — Legendary Resistance. Idempotency guard
    # for the single authoritative writer: the ``live.event_log`` index of
    # every ``LegendaryResistanceUsed`` whose decrement has already been
    # applied to ``legendary_resistance_armed`` /
    # ``Combatant.legendary_resistances_remaining``. The orchestrator-level
    # save paths (repeat save, concentration check, Grapple/Shove) decrement
    # synchronously (``_consume_armed_legendary_resistance``) and record their
    # index at emit (``_emit_legendary_resistance_used``);
    # ``_sync_legendary_resistance`` records every index it processes. Any
    # index found here is skipped, so overlapping scan windows (the
    # concentration check fires INSIDE ``resolve_activity``) never
    # double-decrement. Bounded by the combat's total uses.
    legendary_resistance_applied_event_indices: set[int] = field(default_factory=set)
    # Turn-boundary hook registry (``dnd5e_engine.turn_lifecycle``). Populated
    # by ``_register_default_turn_hooks`` in ``start_combat``; run by
    # ``_end_turn_and_advance`` / ``_begin_turn``. Every rule that fires "at the
    # start/end of a turn" registers here rather than being open-coded into the
    # advance path.
    lifecycle: TurnLifecycle = field(default_factory=TurnLifecycle)
    # C21 — caster-owned spell constructs (SRD 5.2 Spiritual Weapon's force),
    # keyed ``_construct_id(owner, spell)``. Never combatants: absent from
    # ``initiative`` and ``actor_zone``, never occupants or targets.
    constructs: dict[str, _Construct] = field(default_factory=dict)
    # C21 — live transformations (SRD 5.2 Wild Shape, Polymorph) keyed by the
    # transformed creature's entity id: at most one per creature.
    transforms: dict[str, _Transform] = field(default_factory=dict)
    # C21 — roster summons (SRD 5.2 Summon Dragon's Draconic Spirit) keyed by
    # entity id. Never in ``party_ids`` / ``encounter_ids``: a summon's
    # allegiance is its owner's (``_side_of``), so every reader of the side
    # sets as "the PCs" / "the foes" stays correct.
    summons: dict[str, _Summon] = field(default_factory=dict)
    #: A command the summon's owner gave it this combat (C21):
    #: ``summon_entity_id -> stat_block_action_id``. A summon has no AI of its
    #: own — SRD 5.2 Summon Dragon: "It obeys your verbal commands" — so
    #: ``advance_monster_turn`` on its turn resolves this action when one was
    #: given and falls back to the uncommanded Dodge otherwise.
    summon_commands: dict[str, str] = field(default_factory=dict)
    # C21 — roster summons each owner has made: never reset and never purged,
    # so a summon id is never reused.
    summon_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
    # TESTDIVERT
class _PendingReaction:
    """One armed-but-not-yet-fired reaction — 's queue entry.

    Registered by a ``"ready"`` intent (``owner_id`` spends their Action to
    arm it); popped + resolved by ``_pop_pending_reaction`` the moment a
    matching ``trigger`` is next observed from any OTHER combatant's
    intent. See ``docs/dev/reaction-queue.md``.
    """

    owner_id: str
    trigger: ReactionTrigger
    spell_id: str | None
    slot_level: int | None


@dataclass
class _Construct:
    """One live spell construct. ``anchor`` is its concentration anchor's
    ``(target_id, effect_id, origin)`` identity: the construct ends when that
    effect expires. ``cast_round`` is the round of the cast that made the
    construct's immediate attack ("As a Bonus Action on your later turns")."""

    construct_id: str
    owner_id: str
    spell_id: str
    cell: str
    slot_level: int
    anchor: tuple[str, str, str]
    cast_round: int


@dataclass
class _Transform:
    """One live transformation. ``stash`` holds the original value of every
    ``Combatant`` field the form replaced; ``original_monster_slug`` /
    ``original_action_uses`` the monster target's own stat block (``None`` for
    a Character, or a Monster with no template). ``(effect_id, origin)`` names
    the effect whose expiry reverts it."""

    entity_id: str
    form_slug: str
    source: TransformSource
    effect_id: str
    origin: str
    stash: dict[str, Any]
    original_monster_slug: str | None
    original_action_uses: dict[str, MonsterActionUses] | None
    form_proficiency_bonus: int
    attacks_per_action: int
    clears_temp_hp_on_end: bool


@dataclass
class _Summon:
    """One summoned creature in the initiative order (C21): SRD 5.2 Summon
    Dragon's Draconic Spirit. ``anchor`` is its caster's concentration-anchor
    ``(target_id, effect_id, origin)`` identity: the creature disappears when
    that effect expires ("when the spell ends"). ``magnitudes`` and
    ``attacks_per_action`` are fixed when it is seated, from the slot level
    and its caster ("Use the spell slot's level for the spell's level in the
    stat block")."""

    entity_id: str
    owner_id: str
    spell_id: str
    stat_block_slug: str
    slot_level: int
    anchor: tuple[str, str, str]
    magnitudes: StatBlockMagnitudes
    attacks_per_action: int


_REGISTRY: dict[str, _LiveCombat] = {}

#: How many ended combats stay readable after ``end_combat`` (a repeat close,
#: a late ``drain_pending_events`` / ``narration_events`` drain, a last
#: ``get_live``) before the oldest is released, so a long-running host holds a
#: bounded number of them rather than every combat it ever ran.
_ENDED_COMBATS_KEPT: Final = 64
#: The ended combats still in ``_REGISTRY``, oldest first, each with the handle
#: id it was registered under.
_ENDED: deque[tuple[str, _LiveCombat]] = deque()


def _get_live(handle: CombatHandle) -> _LiveCombat:
    live = _REGISTRY.get(handle.handle_id)
    if live is None:
        raise UnknownHandleError(f"No live combat for handle {handle.handle_id!r}")
    return live


# Public read-only live-combat accessor. Host-side resolvers that run alongside
# the engine's dispatch consume this snapshot view of the live state the engine
# owns. Engine-internal callers use _get_live (the private _LiveCombat).
def get_live(handle: CombatHandle) -> LiveCombatView:
    return LiveCombatView.from_live(_get_live(handle))


def resolve_legendary_resistance(handle: CombatHandle, entity_id: str) -> int:
    """SRD 5.2 Legendary Resistance — *"If the monster fails a saving throw,
    it can choose to succeed instead."*

    A PRE-ARMED declaration: the engine has no mid-resolution round-trip to a
    host, so a monster's Legendary Resistance choice must be committed
    BEFORE the save it will convert is even rolled — call this any time
    before submitting the intent that will force ``entity_id``'s save.
    Synchronous (no roll, no I/O): it only arms a declaration that
    ``activities/save_primitive.roll_save`` consults the next time this
    entity's save fails.

    Validates: the combat is live (``IntentRejectedError("combat_ended", …)``,
    mirroring every other seam entry point); ``entity_id`` is an encounter
    member carrying the Legendary Resistance trait
    (``MonsterTraitMechanic.LEGENDARY_RESISTANCE in combatant.trait_mechanics``);
    and its REMAINING per-day pool exceeds the count already armed (arming a
    4th use against a 3/day pool is rejected, not silently capped). On
    success, increments ``live.legendary_resistance_armed[entity_id]`` and
    returns the new armed count; otherwise raises
    ``IntentRejectedError("no_legendary_resistance", …)``.
    """
    live = _get_live(handle)
    if live.ended:
        raise IntentRejectedError("combat_ended", f"handle={handle.handle_id}")
    bearer = _find_combatant(live, entity_id)
    if (
        bearer is None
        or entity_id not in live.encounter_ids
        or MonsterTraitMechanic.LEGENDARY_RESISTANCE not in bearer.trait_mechanics
    ):
        raise IntentRejectedError(
            "no_legendary_resistance",
            f"entity_id={entity_id!r} has no Legendary Resistance trait",
        )
    already_armed = live.legendary_resistance_armed.get(entity_id, 0)
    if bearer.legendary_resistances_remaining <= already_armed:
        raise IntentRejectedError(
            "no_legendary_resistance",
            f"entity_id={entity_id!r} has no unarmed Legendary Resistance use "
            f"left (remaining={bearer.legendary_resistances_remaining}, "
            f"already_armed={already_armed})",
        )
    new_armed = already_armed + 1
    live.legendary_resistance_armed[entity_id] = new_armed
    return new_armed


def get_actor_active_effects(handle: CombatHandle, entity_id: str) -> tuple[ActiveEffect, ...]:
    """Read-only snapshot of one combatant's active effects.

    Public API for host-side resolvers (e.g. a host's own flee or item
    dispatch) that run alongside the engine's own resolution and need to
    see the same active_effects the engine resolvers consume internally. The
    engine is the single source of truth for in-combat effect state; this
    accessor lets the host read it without re-implementing the registry.

    Returns an empty tuple if the handle has no live combat (caller
    should treat as out-of-combat — per spec, no effects apply).
    """
    live = _REGISTRY.get(handle.handle_id)
    if live is None:
        return ()
    return tuple(live.active_effects.get(entity_id, []))


def _current_actor(live: _LiveCombat) -> Combatant:
    return live.initiative[live.current_turn_index]


def _condition_names(c: Combatant) -> list[str]:
    """The combatant's active condition slugs (``is_condition_active`` resolves
    ``CONDITION_IMPLIES`` from the names, so implied conditions need not be
    materialised on ``Combatant.conditions``)."""
    return active_condition_names(c.conditions)


#: "@attributes.movement.<mode>" -- the corpus' way of saying "a speed equal
#: to your <mode> Speed" (Spider Climb, Roving, Potion of Flying).
_MOVEMENT_REFERENCE_RE = re.compile(r"^@attributes\.movement\.([a-z]+)$")
#: Foundry's CONST.ACTIVE_EFFECT_MODES, the ints the corpus writes.
_FOUNDRY_EFFECT_MODE: dict[int, str] = {
    0: "custom",
    1: "multiply",
    2: "add",
    3: "downgrade",
    4: "upgrade",
    5: "override",
}

_WALK_SPEED_KEY = "system.attributes.movement.walk"# Foundry writes every non-walking movement mode as
# ``system.attributes.movement.<mode>``; ``_effect_movement_modes`` projects
# the live effects onto ``Combatant.movement_modes`` by stripping this prefix.
_MOVEMENT_MODE_PREFIX = "system.attributes.movement."


def _enabled_effects(active: Sequence[ActiveEffect]) -> list[ActiveEffect]:
    """The effects currently in force: ones flagged ``disabled`` are dropped.

    Foundry keeps a disabled effect on the actor (that is what the sheet's
    toggle does) without applying its changes. The sheet-side projection
    already honours it (``passive.transfer and not passive.disabled``); the
    fight-side readers must agree, or a switched-off effect would still
    move the creature.
    """
    return [effect for effect in active if not getattr(effect, "disabled", False)]


def _speed_effect_multiplier(active: Sequence[ActiveEffect]) -> float:
    """The factor the live ``ActiveEffects`` apply to a creature's walking Speed.

    SRD 5.2 Boots of Speed: "If you do, the boots double your Speed" -- a
    multiplicative change on ``system.attributes.movement.walk` (Foundry
    mode 1). Two effects that double both apply (1.0 when there are none).
    A multiplicative change on a movement mode the creature has no rating
    for is irrelevant here: this only scales walking Speed, and the boots'
    fly/climb/swim/burrow siblings scale modes the wearer may not have.
    """
    multiplier = 1.0
    for effect in _enabled_effects(active):
        for change in getattr(effect, "changes", ()) or ():
            if change.key != _WALK_SPEED_KEY or change.mode != "multiply":
                continue
            try:
                multiplier *= float(change.value)
            except (TypeError, ValueError):
                continue
    return multiplier


def _effect_speed_delta(active: Sequence[ActiveEffect]) -> int:
    """Feet the live ``ActiveEffects`` add to a creature's walking Speed.

    SRD 5.2 magic items switch speed effects on and off at play time --
    Boots of Speed ("If you do, the boots double your Speed") is the only
    such item in the corpus, and it states the doubling as Foundry
    ``system.attributes.movement.walk`` with ``mode=1`` (multiply). The
    sheet cannot know the boots were clicked, so the projection has to
    happen on the fight's own speed rather than on ``base_speed``.

    ``mode=2`` (add) is a flat bonus -- Roving and Fast Movement add
    ``walk_speed_bonus`` on the sheet the same way. ``mode=1`` (multiply)
    scales the creature's OWN walking Speed, which is what "the boots
    double your Speed" means; a multiplicative change on a movement mode
    the creature has no rating for is ignored (Boots of Speed also carry
    fly/climb/swim/burrow x2 for creatures that can already do them).
    """
    bonus = 0
    for effect in _enabled_effects(active):
        for change in getattr(effect, "changes", ()) or ():
            if change.key != _WALK_SPEED_KEY or change.mode != "add":
                continue
            try:
                bonus += int(float(change.value))
            except (TypeError, ValueError):
                continue
    return bonus


def _effective_speed(c: Combatant, live: _LiveCombat | None = None) -> int:
    """SRD 5.2 walking Speed under the combatant's conditions
    (``rules.conditions.project_speed``): 0 under a Speed-0 condition, else
    ``base_speed - 5 x exhaustion level``.

    SRD 5.2 §Weapon Mastery, Slow: when ``live`` is supplied
    and ``c`` carries any outstanding ``live.slow_marks`` entry, a FLAT
    10 ft comes off on top ("the Speed reduction doesn't exceed 10 feet" —
    one deduction regardless of how many slow-weapon hits landed), floored
    at 0. ``live=None`` (unit-test / pre-combat callers) projects
    conditions only.
    """
    speed = project_speed(c.base_speed, _condition_names(c), exhaustion_level_of(c.conditions))
    if live is not None:
        # Speed effects an item switches on at play time (Boots of Speed).
        # "the boots DOUBLE your Speed" scales the creature's Speed, so the
        # multiplier acts on the projected value, not on the flat bonus.
        #
        # Speed 0 is absolute, whatever produced it: Paralyzed / Grappled
        # ("your Speed is 0") and deep Exhaustion ("reduced by 5 x level",
        # floored at 0) alike. A creature that cannot move is not revived by
        # a +10 rider or a doubling: zero times anything is still zero.
        # Only a real creature with a positive Speed can be scaled.
        if speed > 0:
            active = live.active_effects.get(c.entity_id, [])
            speed = max(
                0,
                int((speed + _effect_speed_delta(active)) * _speed_effect_multiplier(active)),
            )
    if live is not None and live.slow_marks.get(c.entity_id):
        speed = max(0, speed - 10)
    # SRD 5.2 §Movement: a creature with several speeds uses the one it
    # chooses for each move, and the fastest one is what its turn affords.
    # Dragon Wings turns a fly speed on DURING the fight, after the budget
    # was already dealt from the walking Speed; without this the effect
    # existed and did nothing (§7). Only a live fight can raise it — the
    # sheet-side ``movement_modes`` is the resting value.
    if live is not None and speed > 0:
        modes = _effect_movement_modes(c, live)
        for mode_speed in modes.model_dump().values():
            if mode_speed is not None and mode_speed > speed:
                speed = mode_speed
    return speed


def _effect_movement_modes(
    c: Combatant, live: _LiveCombat | None = None
) -> CombatantMovementModes:
    """Movement modes under the combatant's live ``ActiveEffects``.

    SRD 5.2 Dragon Wings (sorcerer, Draconic Sorcery 14): "you gain a Fly
    Speed of 60 feet"? The corpus states it as an effect the feature's own
    activity turns on -- Foundry ``system.attributes.movement.fly`` with
    ``mode=4`` (upgrade/max) and value 60. ``Combatant.movement_modes`` is
    filled once, when the party member is built, so a feature used DURING a
    fight never reached it and a winged sorcerer could not fly at all.

    This projects the live effects onto the combatant's own ratings with
    the same Foundry semantics ``_resolve_movement_modes`` uses for
    permanent passive changes: mode 4 = max (an upgrade never lowers a
    better existing rating), mode 2 = additive, mode 1 = multiply (Boots of
    Speed double a mode the bearer already has; a multiply with no base
    rating grants nothing). ``walk`` is not a mode here -- walking Speed is
    ``_effective_speed``'s job, and folding it twice would double-count.

    ``live=None`` returns the combatant's own modes unchanged.
    """
    if live is None:
        return c.movement_modes
    active = live.active_effects.get(c.entity_id, [])
    if not active:
        return c.movement_modes
    values = c.movement_modes.model_dump()
    for effect in _enabled_effects(active):
        for change in getattr(effect, "changes", ()) or ():
            key = str(change.key)
            if not key.startswith(_MOVEMENT_MODE_PREFIX):
                continue
            mode = key[len(_MOVEMENT_MODE_PREFIX) :]
            if mode not in values:
                continue
            try:
                literal = int(float(change.value))
            except (TypeError, ValueError):
                continue
            current = values.get(mode)
            if change.mode == "upgrade":
                values[mode] = literal if current is None else max(current, literal)
            elif change.mode == "add":
                values[mode] = literal if current is None else current + literal
            elif change.mode == "multiply":
                if current is not None:
                    values[mode] = current * literal
    return CombatantMovementModes(**values)

def _projected_movement_modes(
    live: _LiveCombat,
    c: Combatant,
    extra: Sequence[ActiveEffect] = (),
) -> CombatantMovementModes:
    """``c``'s movement modes under every effect that is in force DURING
    this resolution.

    ``_effect_movement_modes`` reads ``live.active_effects``, which only
    holds effects that ALREADY landed. The activity context is built before
    the activities run, so a spell's own rider is missing there: Spider
    Climb's "Climb Speed equal to your Speed" and Alter Self's Aquatic
    Adaptation (a Swim Speed) would resolve against no rating at all and
    be dropped, leaving the spell cast and the movement inert.

    ``extra`` are the effect changes this resolution carries, already
    translated to ``ActiveEffect``s by the caller. They are folded onto the
    live ratings with the same Foundry semantics, never replacing them: an
    upgrade keeps the better rating the creature already has.
    """
    modes = _effect_movement_modes(c, live)
    if not extra:
        return modes
    # Merge over the live ratings: an upgrade never lowers what the fight
    # already gives, an add stacks on it.
    return _effect_movement_modes_for(list(extra), modes)


def _effect_movement_modes_for(
    changes: Sequence[ActiveEffectChange],
    base: CombatantMovementModes,
) -> CombatantMovementModes:
    """Fold non-walk movement changes onto ``base``."""
    values = base.model_dump()
    for change in changes:
        key = str(change.key)
        if not key.startswith(_MOVEMENT_MODE_PREFIX):
            continue
        mode = key[len(_MOVEMENT_MODE_PREFIX) :]
        if mode not in values:
            continue
        try:
            literal = int(float(change.value))
        except (TypeError, ValueError):
            continue
        current = values.get(mode)
        if change.mode == "upgrade":
            values[mode] = literal if current is None else max(current, literal)
        elif change.mode == "add":
            values[mode] = literal if current is None else current + literal
        elif change.mode == "multiply":
            if current is not None:
                values[mode] = current * literal
    return CombatantMovementModes(**values)


def _speed_ratio_effects(effects: Sequence[Any]) -> frozenset[str]:
    """Ids of the passive effects whose movement change is written "1".

    The corpus states "a Swim Speed equal to your Speed" (Alter Self,
    Aquatic Adaptation) as ``system.attributes.movement.swim = "1"`` -- the
    source's own mark for "your Speed", which read as a distance would give
    a Swim Speed of one foot. Only a movement-only effect qualifies: an
    effect that also carries a real numeric change keeps its literal.
    """
    ids: set[str] = set()
    for effect in effects:
        changes = list(getattr(effect, "changes", ()) or ())
        if not changes or not all(
            str(getattr(c, "key", "")).startswith(_MOVEMENT_MODE_PREFIX)
            and str(c.key) != _WALK_SPEED_KEY
            and str(getattr(c, "value", "")).strip() == "1"
            for c in changes
        ):
            continue
        ids.add(str(getattr(effect, "id", "")))
    return frozenset(ids)

def _passive_change_to_effect_change(
    change: PassiveEffectChange,
    walk_speed: int,
    *,
    walk_ratio: bool = False,
) -> ActiveEffectChange:
    """One passive change as the runtime ``ActiveEffectChange`` it becomes.

    The ``@attributes.movement.<mode>`` corpus references ("a Climb Speed
    equal to your Speed") are resolved here against ``walk_speed``, the
    caster's effective walking Speed -- the same reading the fight's own
    budget was dealt from. An unresolved rating stays as the operator wrote
    it, so it is simply ignored by the movement projection instead of being
    read as a speed of its own.
    """
    # "a Climb Speed equal to your Speed" -- the source's walking Speed, Roving
    # bonus included; otherwise the literal the source wrote.
    value = str(walk_speed) if walk_ratio else str(change.value)
    match = _MOVEMENT_REFERENCE_RE.match(value)
    if match is not None and walk_speed > 0:
        # Any speed reference reads the creature's walking Speed here: a
        # Climb/Swim Speed "equal to your Speed" and Roving's own
        # walk reference alike. The projected modes are not known at this
        # point, so a reference to one of them is simply answered with the
        # walk reading rather than left as an unparseable token.
        value = str(walk_speed)
    return ActiveEffectChange(
        key=str(change.key),
        # The context carries effects this resolution will APPLY, so the
        # change is already typed: a translated ``ActiveEffectChange``
        # names its mode, a raw corpus one carries Foundry's int.
        mode=(
            change.mode
            if isinstance(getattr(change, "mode", None), str)
            else _FOUNDRY_EFFECT_MODE.get(change.mode, "custom")
        ),
        value=value,
        priority=(
            change.priority if getattr(change, "priority", None) is not None else 20
        ),
    )


def _upcoming_movement_changes(
    effects: Sequence[ActiveEffect],
    walk_speed: int,
) -> list[ActiveEffectChange]:
    """The movement-mode changes among the effects THIS resolution carries,
    resolved against the creature it lands on.

    Only the raw change, never the effect: the activity builder applies the
    same change to the live state afterwards, and the movement projection
    never mistakes a pending effect for a landed one.
    """
    changes = [
        change
        for effect in _enabled_effects(list(effects))
        for change in (getattr(effect, "changes", ()) or ())
        if str(getattr(change, "key", "")).startswith(_MOVEMENT_MODE_PREFIX)
    ]
    # An add on the walk mode is a Speed this resolution gives the creature,
    # so it is part of "your Speed" that the mode references point at:
    # Roving adds 10 ft to the walk AND sets climb/swim to the walk, and
    # reading the old 30 here would make the spell grant 30 while the live
    # effect it lands carries 40.
    walk_speed += sum(
        int(float(change.value))
        for change in changes
        if str(change.key) == _WALK_SPEED_KEY and change.mode == "add"
    )
    # Alter Self's Aquatic Adaptation writes the same sentence as the value
    # "1" (the source's own mark for "equal to your Speed"), which is not a
    # distance: read literally it would give a Swim Speed of one foot.
    walk_ratio_modes = {
        str(change.key)
        for change in changes
        if str(change.key) != _WALK_SPEED_KEY
        # Foundry's ints: 4 = upgrade, 5 = override.
        and change.mode in (4, 5)
        and str(change.value).strip() == "1"
    }
    return [
        _passive_change_to_effect_change(
            change,
            walk_speed,
            walk_ratio=str(change.key).strip().lower() in walk_ratio_modes,
        )
        for change in changes
    ]

def _set_dodging(live: _LiveCombat, actor_id: str) -> None:
    """SRD 5.2 §Actions in Combat — Dodge: flip ``actor_id``'s ``dodging``
    flag live via the standard initiative-list model_copy write. Split out
    of ``submit_player_intent`` purely to keep that dispatcher's cyclomatic
    complexity under the lint ceiling."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={"dodging": True})
            break


def _set_hide_attempted(live: _LiveCombat, actor_id: str) -> None:
    """SRD 5.2 §Actions in Combat — Hide (F3): flip ``actor_id``'s
    ``hide_attempted_this_turn`` flag live, mirroring ``_set_dodging``. Called
    once a Hide intent has cleared the cover/obscurement gate — a second
    attempt this turn is rejected before any dice are drawn."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={"hide_attempted_this_turn": True})
            break


def _help_target_invalid(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> bool:
    """SRD 5.2 §Actions in Combat — Help, Assist an Attack Roll: *"an enemy
    within 5 feet of you"*. True (reject) unless ``intent.target_id`` names a
    living combatant within 5 ft of ``actor_id``, using the same
    ``SpatialTopology.distance_ft`` measure ``_target_distance_map`` threads
    into attack resolution (Chebyshev on the grid backend)."""
    target_id = intent.target_id
    if not target_id:
        return True
    target = _find_combatant(live, target_id)
    if target is None or not target.is_alive or target.hp_current <= 0:
        return True
    actor_zone = live.actor_zone.get(actor_id)
    target_zone = live.actor_zone.get(target_id)
    if actor_zone is None or target_zone is None:
        return True
    distance = live.topology.distance_ft(actor_zone, target_zone)
    return distance is None or distance > 5


def _reject_invalid_help_target(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """Raise ``IntentRejectedError("target_invalid")`` for an out-of-range /
    dead / missing Help target; a no-op for every other intent type. Unlike
    a cast or attack, ``help`` has no dedicated ``...Failed`` event to emit,
    so an invalid target raises directly — mirrors the generic
    ``no_action_economy`` raise ``_action_economy_gate_failure`` uses for
    intents without a typed failure event of their own. Split out of
    ``submit_player_intent`` purely to keep that dispatcher's cyclomatic
    complexity under the lint ceiling (mirrors ``_set_dodging``)."""
    if intent.intent_type == "help" and _help_target_invalid(live, actor_id, intent):
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} help target {intent.target_id!r} is not a living "
            "combatant within 5 ft",
        )


def _reject_invalid_grapple_target(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """Refuse a Grapple whose target cannot be grappled; a no-op for every other
    intent type.

    Two refusals, deliberately distinct:

    * ``target_invalid`` — the actor named ITSELF. SRD 5.2 Unarmed Strike
      (Grapple) targets "a creature", i.e. another one: before r199
      ``grapple`` with ``target_id`` == the actor was accepted, rolled a save and
      could put Grappled on the actor. The measure came from r198's live run,
      which used exactly that hole to seed the state it was checking.
    * ``out_of_range`` — dead / missing / beyond the 5-ft reach
      (``_help_target_invalid``, the shared liveness + distance predicate).

    SRD 5.2 Unarmed Strike — Grapple has the same 5-ft reach shape as Help's
    "an enemy within 5 feet of you", so this reuses ``_help_target_invalid``'s
    liveness/distance predicate — only the rejection reason differs (Grapple
    has no dedicated ``...Failed`` event either, so — like Help — an invalid
    target raises directly rather than emitting one). Split out purely to
    keep ``submit_player_intent``'s cyclomatic complexity under the lint
    ceiling (mirrors ``_reject_invalid_help_target`` / ``_set_dodging``).
    """
    if intent.intent_type != "grapple":
        return
    if intent.target_id == actor_id:
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} cannot grapple itself: SRD 5.2 Unarmed Strike "
            "(Grapple) targets another creature",
        )
    if _help_target_invalid(live, actor_id, intent):
        raise IntentRejectedError(
            "out_of_range",
            f"actor_id={actor_id!r} grapple target {intent.target_id!r} is not a living "
            "combatant within reach (5 ft)",
        )


def _reject_invalid_shove_target(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """Raise ``IntentRejectedError("target_invalid")`` for an out-of-reach /
    dead / missing Shove target; a no-op for every other intent type.

    SRD 5.2 Unarmed Strike — Shove has the same 5-ft reach shape as Grapple,
    so this reuses ``_help_target_invalid``'s liveness/distance predicate,
    as Grapple does. Split out purely to keep
    ``submit_player_intent``'s cyclomatic complexity under the lint ceiling
    (mirrors ``_reject_invalid_grapple_target``)."""
    if intent.intent_type == "shove" and _help_target_invalid(live, actor_id, intent):
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} shove target {intent.target_id!r} is not a living "
            "combatant within reach (5 ft)",
        )


def _dodge_benefit_active(live: _LiveCombat, c: Combatant) -> bool:
    """SRD 5.2 §Actions in Combat — Dodge. True while ``c``'s
    Dodge benefit is live: the combatant took the Dodge action this turn (or
    a prior turn, "until the start of your next turn") AND has not lost it
    under the SRD loss clause — *"You lose these benefits if you have the
    Incapacitated condition or if your Speed is 0."* ``live`` feeds the
    Slow-mastery Speed projection. This predicate does NOT
    itself apply the SRD "if you can see the attacker" conjunct on the
    attack-disadvantage half — callers building a ``target_dodging`` map
    (C16b) AND this result with ``_combatant_can_see(live, dodger,
    attacker)`` at the call site, since only the caller has the attacker in
    scope.
    """
    return (
        c.dodging
        and not conditions_block_actions(_condition_names(c))
        and _effective_speed(c, live) > 0
    )


def _clamp_movement_budget(live: _LiveCombat, entity_id: str) -> None:
    """Re-project one combatant's ``movement_remaining`` after its conditions
    changed mid-turn: never above the effective Speed (a creature grappled
    mid-move loses the rest of its budget; the budget is never RAISED here —
    only the turn-start reset and Dash add movement)."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id != entity_id:
            continue
        cap = _effective_speed(c, live)
        if c.movement_remaining > cap:
            live.initiative[idx] = c.model_copy(update={"movement_remaining": cap})
        break


def _grant_movement_headroom(live: _LiveCombat, entity_id: str) -> None:
    """Raise one combatant's unspent movement to the effective Speed after
    an effect that increases it landed mid-turn.

    SRD 5.2 §Movement: a creature uses whichever speed it has for a move, so
    the moment Dragon Wings turns a Fly Speed of 60 ft on, 60 ft is what the
    creature can walk away with this turn. The turn-start deal in
    ``_reset_turn_resources`` happens once, before the bonus action that
    activated the wings, so without this the effect was inert for the rest
    of the turn while still emitting ``effect_applied`` (§7).

    Only the SPEED carries over, not the spent portion: a creature that had
    already walked 20 of 30 ft climbs to 60, not to 90. Effects that do not
    raise the fastest available speed leave the budget untouched, so this is
    a no-op for Rage, Bless and the like.
    """
    for idx, c in enumerate(live.initiative):
        if c.entity_id != entity_id:
            continue
        cap = _effective_speed(c, live)
        if cap > c.movement_remaining:
            live.initiative[idx] = c.model_copy(update={"movement_remaining": cap})
        return


def _spend_movement(live: _LiveCombat, entity_id: str, cost: int) -> None:
    """Take ``cost`` feet out of a combatant's unspent movement.

    A temporary speed bonus (Boots of Speed's doubling, Spider Climb's
    Climb Speed, Haste) hands movement budget out ahead of the spending:
    ``_grant_movement_headroom`` raises ``movement_remaining`` to the
    boosted Speed the moment the effect lands. The regular budget for this
    turn is the unboosted one, so walking a foot past it spends the
    borrowed part first -- otherwise clicking the boots on, walking the
    borrowed 30 ft and clicking them off would have walked 30 ft more than
    the creature's Speed allows.

    Falls back to the plain subtraction when nothing was borrowed.
    """
    for idx, c in enumerate(live.initiative):
        if c.entity_id != entity_id:
            continue
        live.initiative[idx] = c.model_copy(update={"movement_remaining": c.movement_remaining - cost})
        return

#: SRD 5.2 Incapacitated — intents that spend NO action, Bonus Action or
#: Reaction and therefore stay legal: ending the turn, and plain movement
#: (Speed is governed separately — see ``_handle_move``'s speed gate).
_INCAPACITATED_ALLOWED_INTENTS: frozenset[str] = frozenset({"pass", "move", "drop_concentration"})


def _find_combatant(live: _LiveCombat, entity_id: str) -> Combatant | None:
    """Locate a combatant in the live initiative by entity id."""
    for c in live.initiative:
        if c.entity_id == entity_id:
            return c
    return None


def _fold_condition_onto_combatant(
    live: _LiveCombat,
    entity_id: str,
    condition: str,
    *,
    save_dc: int | None = None,
    source_effect_id: str | None = None,
) -> None:
    """Materialise a condition on **both** condition stores: the coarse
    ``live.active_conditions`` name set (what ``views.py`` shows a host) and
    ``Combatant.conditions``,
    the typed list every projection reads (speed, incapacitated gate, save
    auto-fail, attack rows).

    Writing both here — rather than leaving ``active_conditions`` to each
    caller — is what makes this helper safe to call from OUTSIDE the
    ``ConditionApplied`` fold (``start_combat``'s 0-HP hydration is one such
    caller). A caller that set only one store would leave the two views
    disagreeing, which is exactly the class of bug the condition-immunity gates
    exist to prevent. The ``active_conditions`` write is deliberately BEFORE the
    idempotence guard, so it still lands for a combatant that is unknown or
    already carries the typed entry — preserving the pre-existing
    unconditional-write semantics of the ``ConditionApplied`` branch of
    ``_emit``.

    Idempotent per condition name; implied conditions are NOT materialised —
    ``is_condition_active`` resolves ``CONDITION_IMPLIES`` from the names.

    ``save_dc`` / ``source_effect_id`` are optional keyword-only overrides for
    the newly-materialised ``ActiveCondition`` — C14's Grapple option threads
    the escape DC and the imposing effect's id here (via
    ``_emit_grapple_condition_applied``) so later escape attempts read the
    STORED DC rather than recomputing it. Every other call site (the generic
    ``ConditionApplied`` fold in ``_emit``, ``start_combat``'s 0-HP hydration)
    leaves both at their ``None`` default — unchanged behavior.
    """
    live.active_conditions.setdefault(entity_id, set()).add(condition)
    c = _find_combatant(live, entity_id)
    if c is None or any(ac.condition == condition for ac in c.conditions):
        return
    new = [
        *c.conditions,
        ActiveCondition(
            condition=condition,
            source_entity_id="implied:event",
            scope="combat",
            applied_round=live.round_number,
            save_dc=save_dc,
            source_effect_id=source_effect_id,
        ),
    ]
    for idx, slot in enumerate(live.initiative):
        if slot.entity_id == entity_id:
            live.initiative[idx] = slot.model_copy(update={"conditions": new})
            break
    _clamp_movement_budget(live, entity_id)
    _end_what_incapacitation_ends(live, entity_id, condition)


def _emit_condition_applied(live: _LiveCombat, target: Combatant, condition: ConditionType) -> bool:
    """Emit ``ConditionApplied`` for a condition an action applies (Shove's
    Prone, Hide's Invisible) unless the target is immune — SRD 5.2: "If you
    have Immunity to a damage type or a condition, it doesn't affect you in any
    way." Takes the ``Combatant`` directly: both callers already hold it, so
    there is no need for a second lookup. Returns whether the condition
    landed."""
    if is_condition_immune(target, condition):
        _LOGGER.info(
            "condition_immune_suppressed status=%s target_id=%s", condition, target.entity_id
        )
        return False
    _emit(live, ConditionApplied(target_id=target.entity_id, condition=condition))
    return True


def _end_what_incapacitation_ends(live: _LiveCombat, entity_id: str, condition: str) -> None:
    """What ``condition`` ends as it first lands on ``entity_id``, when it is
    Incapacitated or implies it (Paralyzed / Petrified / Stunned / Unconscious
    via ``CONDITION_IMPLIES``).

    Keyed to the condition's first materialisation on ``Combatant.conditions``,
    not a raw HP threshold. Two folds write that store, and whichever writes
    the condition first calls this, so it runs once however the condition
    arrives: ``_emit_apply_effect_applied`` for an effect's status (Hold
    Person's Paralyzed, whose ``ConditionApplied`` then finds the condition
    already there) and ``_fold_condition_onto_combatant`` for a bare
    ``ConditionApplied`` (0 HP's Unconscious).
    """
    if not is_condition_active(Condition.INCAPACITATED, [condition]):
        return
    # SRD 5.2 Incapacitated — "No Concentration. Your Concentration is broken."
    _drop_concentration(live, entity_id)
    # SRD 5.2 "Ending a Grapple" — "The condition also ends if the grappler has
    # the Incapacitated condition." Release every victim this newly
    # incapacitated combatant is grappling.
    _release_grapple_victims_of(live, entity_id)
    _end_rage_on_incapacitation(live, entity_id, condition)
    # SRD 5.2 Wild Shape: the form lasts "until you ... have the Incapacitated
    # condition".
    _end_wild_shape(live, entity_id, "incapacitated")


def _strip_condition_from_combatant(live: _LiveCombat, entity_id: str, condition: str) -> None:
    """Drop the ``Combatant.conditions`` entries named ``condition`` after a
    ``ConditionRemoved``.

    Multi-source stacking guard (same rule ``_emit_apply_effect_expired``
    honours): an entry bridged from an effect that is STILL active is kept —
    ``_drop_concentration`` emits one ``ConditionRemoved`` per condition its
    dropped effect installed, and that must not clear a condition another live
    effect keeps imposing. The invariant: ``live.active_conditions``'s coarse
    name for ``condition`` agrees with whether a typed entry for it survives.
    ``_emit`` discards that name before calling us, so the name is re-added
    whenever an entry survives, whether or not this filter dropped any.
    """
    c = _find_combatant(live, entity_id)
    if c is None or not any(ac.condition == condition for ac in c.conditions):
        return
    live_effect_ids = {eff.id for eff in live.active_effects.get(entity_id, [])}
    new = [
        ac
        for ac in c.conditions
        if ac.condition != condition
        or (ac.source_effect_id is not None and ac.source_effect_id in live_effect_ids)
    ]
    if len(new) != len(c.conditions):
        for idx, slot in enumerate(live.initiative):
            if slot.entity_id == entity_id:
                live.initiative[idx] = slot.model_copy(update={"conditions": new})
                break
    if any(ac.condition == condition for ac in new):
        live.active_conditions.setdefault(entity_id, set()).add(condition)


def _drop_concentration(
    live: _LiveCombat, caster_id: str, *, reason: EffectExpiryReason = "concentration_drop"
) -> None:
    """Cascade a concentration drop: ``ConcentrationDropped`` + per-target
    ``EffectExpired(reason=...)`` + ``ConditionRemoved`` for every
    condition the dropped effect installed.

    ``reason`` distinguishes a broken concentration ("concentration_drop")
    from the spell's own maximum duration running out ("duration"); the
    ``ConcentrationDropped`` marker fires either way.

    Reads the persistent ``live.concentration_chain[caster_id]`` (the
    caster's owned-effects-by-name map) and
    ``live.conditions_by_effect[(target_id, effect_name)]`` (the
    persistent effect→condition bijection the orchestrator maintains in
    lieu of the transient ``ctx.parent_chain``). Clears both on
    completion + removes any matching ``repeat_save_on_turn_end`` specs
    so a paralyzed target whose source effect is gone stops rolling
    end-of-turn saves on the next turn.

    Idempotent against an empty chain — calling on a non-concentrating
    caster is a no-op.
    """
    entries = list(live.concentration_chain.get(caster_id) or ())
    if not entries:
        return
    for target_id, effect_id, origin in entries:
        # ``ConcentrationDropped.effect_name`` carries the effect *id*
        # (``effect:<slug>``) — the single representation the rest of the
        # lifecycle uses: ``concentration_chain`` / ``conditions_by_effect`` key
        # on ``effect.id`` and ``_build_hydration_payload`` projects
        # ``existing_concentration[caster]["effect_name"] = concentration_effect_id``
        # (the id). Resolving the human-readable ``ActiveEffect.name`` here only
        # when the effect was still in ``active_effects`` produced two divergent
        # representations from one emit site; the id is canonical.
        _emit(
            live,
            ConcentrationDropped(target_id=caster_id, effect_name=effect_id),
        )
        _emit(
            live,
            EffectExpired(
                target_id=target_id,
                effect_id=effect_id,
                origin=origin,
                reason=reason,
            ),
        )
        identity = (target_id, effect_id, origin)
        conditions = live.conditions_by_effect.pop(identity, [])
        for cond in conditions:
            # Cast back to the literal type expected by ConditionRemoved.
            _emit(
                live,
                ConditionRemoved(
                    target_id=target_id,
                    condition=cond,
                ),
            )
        # Drop any pending repeat-save spec keyed off this expired effect.
        live.repeat_save_on_turn_end.pop(identity, None)
    live.concentration_chain.pop(caster_id, None)
    live.concentration_rounds_remaining.pop(caster_id, None)
    # Clear ``Combatant.concentration_effect_id`` so subsequent hydration
    # payloads project an empty ``existing_concentration`` for this
    # caster. The ``_writeback_concentration`` path only fires when the
    # caster is the active turn-actor, but damage-driven drops happen on
    # arbitrary turns — clear inline so the canonical session-state field
    # stays consistent with the lifecycle event stream.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == caster_id and c.concentration_effect_id is not None:
            live.initiative[idx] = c.model_copy(update={"concentration_effect_id": None})
            break


# SRD 5.2 armor categories a creature can wear; a Shield is tracked
# separately (``Combatant.shield_equipped``). Keyed off the dataset's
# ``ArmorCategory`` so an unhandled category surfaces as a KeyError rather
# than a silently-dropped armor slug.
_BODY_ARMOR: Final[dict[ArmorCategory, WornArmor]] = {
    ArmorCategory.LIGHT: "light",
    ArmorCategory.MEDIUM: "medium",
    ArmorCategory.HEAVY: "heavy",
}
_MARTIAL_ARTS: Final = "martial-arts"


def _worn_armor(equipment: Sequence[str]) -> tuple[WornArmor | None, bool]:
    """Body armor category and Shield among ``equipment``: armor and Shields a
    host lists there are worn (the ``derive_sheet`` convention)."""
    loader = get_lib_loader()
    body: WornArmor | None = None
    shield = False
    for slug in equipment:
        armor = loader.get_armor(slug)
        if armor is None:
            continue
        if armor.armor_category == ArmorCategory.SHIELD:
            shield = True
        else:
            body = _BODY_ARMOR[armor.armor_category]
    return body, shield


def _martial_arts_active(c: Combatant) -> bool:
    """SRD 5.2 Martial Arts: "You gain the following benefits while you are
    unarmed or wielding only Monk weapons and you aren't wearing armor or
    wielding a Shield." The weapon half is checked per attack (``attack.py``);
    what the other hand holds is not modelled."""
    return (
        c.worn_armor is None
        and not c.shield_equipped
        and _MARTIAL_ARTS in _granted_feature_slugs(c)
    )


# ── Unarmed Strike: the Grapple option + escape ─────────────────────────────
#
# SRD 5.2 (Unarmed Strike, "Grapple"): "The target must succeed on a Strength
# or Dexterity saving throw (it chooses which), or it has the Grappled
# condition. The DC for the saving throw and any escape attempts equals 8
# plus your Strength modifier and Proficiency Bonus."
#
# A deterministic-choice policy, since the engine has no player-facing
# choice prompt: the target saves with whichever of STR/DEX
# has the higher save modifier (tie -> STR); the escaper picks Athletics vs
# Acrobatics by higher check modifier (tie -> Athletics/STR).
#
# Out of scope (BACKLOG.md): the size gate ("a creature can grapple
# no more than one size larger than itself"), the free-hand gate (SRD
# requires "a hand free"), and the distance-exceeded auto-release (no
# forced-move currently separates a grappled pair mid-grapple).


def _unarmed_option_dc(attacker: Combatant) -> int:
    """SRD 5.2 Unarmed Strike — Grapple / Shove: "The DC ... equals 8 plus
    your Strength modifier and Proficiency Bonus." Shared by both options.
    Martial Arts (Dexterous Attacks):
    "you can use your Dexterity modifier instead of your Strength modifier
    to determine the save DC"."""
    ability_mod = ability_modifier(attacker.strength)
    if _martial_arts_active(attacker):
        ability_mod = max(ability_mod, ability_modifier(attacker.dexterity))
    return 8 + ability_mod + proficiency_bonus_of(attacker)


def _emit_grapple_condition_applied(
    live: _LiveCombat, target_id: str, *, save_dc: int, source_effect_id: str
) -> None:
    """Fold + emit ``ConditionApplied(grappled)`` with the escape DC and the
    imposing effect's id threaded onto the freshly-materialised
    ``ActiveCondition`` — a dedicated emit-side entry point (rather than
    mutating the slot after the fact) so ``escape_grapple`` can later read
    the STORED DC instead of recomputing it. Pre-folds directly (idempotent
    per condition) THEN routes through the generic ``_emit`` so the event
    still lands on the log/queue/listeners exactly like any other
    ``ConditionApplied`` — the generic fold branch inside ``_emit`` then
    no-ops (the condition is already materialised)."""
    _fold_condition_onto_combatant(
        live, target_id, "grappled", save_dc=save_dc, source_effect_id=source_effect_id
    )
    _emit(live, ConditionApplied(target_id=target_id, condition="grappled"))


def _release_one_grapple_effect(live: _LiveCombat, victim_id: str, effect_id: str | None) -> None:
    """Drop the grapple ``ActiveEffect`` named ``effect_id`` from
    ``live.active_effects[victim_id]`` — a no-op if absent. MUST run BEFORE
    the matching ``ConditionRemoved`` is emitted: ``_strip_condition_from_
    combatant``'s multi-source stacking guard keeps a condition whose
    ``source_effect_id`` is still present in ``active_effects``, so the
    effect has to be gone first for the condition to actually clear."""
    if effect_id is None:
        return
    effects = live.active_effects.get(victim_id)
    if effects is None:
        return
    remaining = [e for e in effects if e.id != effect_id]
    if len(remaining) != len(effects):
        live.active_effects[victim_id] = remaining


def _release_grapple_conditions(
    live: _LiveCombat, victim_id: str, grappled_ac: ActiveCondition
) -> None:
    """End a grapple: drop its effect AND every other condition it installed.

    SRD 5.2 prints riders on one grapple that are separate conditions -- the
    Behir's Constrict is "Grappled (escape DC 16) ... and Restrained until the
    grapple ends", the Shambling Mound's Engulf adds Blinded, and "until the
    grapple ends" is the ONLY expiry those riders have. A typed effect carries
    them in one ``statuses`` list, so releasing the effect must remove all of
    them: emitting ``ConditionRemoved`` for "grappled" alone left the victim
    Restrained (and Blinded) for the rest of the fight after a successful
    escape -- measured r203 before this fix.

    ``live.conditions_by_effect`` holds exactly what the effect installed, so
    the riders are read from it rather than guessed from ``statuses``. Order
    matters: the effect leaves ``active_effects`` first, because
    ``_strip_condition_from_combatant``'s stacking guard keeps a condition
    whose source effect is still live.
    """
    effect_id = grappled_ac.source_effect_id
    riders: list[str] = []
    if effect_id is not None:
        for identity in [
            key
            for key in live.conditions_by_effect
            if key[0] == victim_id and key[1] == effect_id
        ]:
            riders += [
                condition
                for condition in live.conditions_by_effect.pop(identity)
                if condition != "grappled"
            ]
    _release_one_grapple_effect(live, victim_id, effect_id)
    _emit(live, ConditionRemoved(target_id=victim_id, condition="grappled"))
    for condition in dict.fromkeys(riders):
        _emit(live, ConditionRemoved(target_id=victim_id, condition=condition))


def _release_grapple_victims_of(live: _LiveCombat, grappler_id: str) -> None:
    """SRD 5.2 "Ending a Grapple" — "The condition also ends if the grappler
    has the Incapacitated condition." Called from
    ``_end_what_incapacitation_ends`` (beside the concentration drop) whenever
    ``grappler_id`` newly becomes Incapacitated, however the condition
    arrives: release every combatant currently Grappled BY it (per
    ``_condition_source_entity``'s resolution of the ``grapple:`` origin
    prefix)."""
    for victim in list(live.initiative):
        grappled_ac = next((ac for ac in victim.conditions if ac.condition == "grappled"), None)
        if grappled_ac is None:
            continue
        if _condition_source_entity(live, victim, "grappled") != grappler_id:
            continue
        _release_grapple_conditions(live, victim.entity_id, grappled_ac)


def _resolve_grapple_save_ability(target: Combatant) -> Ability:
    """The target saves with whichever of STR/DEX has
    the higher save modifier (tie -> STR)."""
    str_total = save_modifier(target, "str").total
    dex_total = save_modifier(target, "dex").total
    return "str" if str_total >= dex_total else "dex"


def _roll_unarmed_option_save(
    live: _LiveCombat, attacker: Combatant, target: Combatant
) -> SaveRolled:
    """SRD 5.2 Unarmed Strike — Grapple/Shove: the shared save roll both
    options make against ``_unarmed_option_dc(attacker)``. Extracted
    out of ``_handle_grapple``/``_handle_shove`` (byte-identical
    behaviour, same draw order, same emitted ``SaveRolled`` fields) so a
    future save-logic change lands once, not twice.

    Rolls through the same primitives ``_run_end_of_turn_saves`` uses
    outside the activity walk (auto-fail conditions + exhaustion penalty
    come free; no advantage source of its own — a plain D20 Test), so
    Paralyzed/Stunned/Petrified/Unconscious targets auto-fail with zero
    draws. A deterministic-choice policy, since the engine has no
    player-facing choice prompt: the target saves with
    whichever of STR/DEX has the higher save modifier (tie -> STR), via
    ``_resolve_grapple_save_ability``.

    Emits the ``SaveRolled`` event and returns it so callers can branch on
    ``.succeeded`` (and read back the resolved ``.dc``) without recomputing
    either.

    This save bypasses ``activities/save_primitive.roll_save``
    just like the repeat save and concentration check, so it never saw the
    C18 Legendary Resistance conversion — an armed target would be
    grappled/shoved on a failed save with its armed use silently leaking
    into a LATER save. Wired via the shared
    ``_consume_armed_legendary_resistance`` helper, BEFORE the caller
    (``_handle_grapple``/``_handle_shove``) branches on the outcome, so
    ``SaveRolled`` carries the final (possibly converted) result;
    ``LegendaryResistanceUsed`` follows it."""
    target_id = target.entity_id
    dc = _unarmed_option_dc(attacker)
    ability = _resolve_grapple_save_ability(target)
    save_proj = project_passive_save_modifiers(_condition_names(target))
    ability_upper = ability.upper()
    if ability_upper in save_proj.get("passive_save_auto_fail", []):
        roll_total, succeeded = 0, False
        mode: AdvantageMode = "normal"
        natural: int | None = None
        roll_modifier = 0
        roll_sources: list[AdvantageSource] = []
    else:
        modifier = save_modifier(target, ability).total + d20_test_penalty(target.conditions)
        dis: tuple[AdvantageSource, ...] = (
            ("condition:target",) if ability_upper in save_proj.get("passive_save_dis", []) else ()
        )
        roll = roll_d20_test(live.rng, modifier, AdvantageSources(disadvantage=dis))
        roll_total, succeeded = roll.total, roll.total >= dc
        mode, natural, roll_modifier = roll.mode, roll.kept, roll.modifier
        roll_sources = list(roll.sources)
    lr_remaining = None if succeeded else _consume_armed_legendary_resistance(live, target)
    if lr_remaining is not None:
        succeeded = True
    event = SaveRolled(
        target_id=target_id,
        ability=ability,
        dc=dc,
        roll_total=roll_total,
        succeeded=succeeded,
        advantage=mode,
        natural=natural,
        modifier=roll_modifier,
        sources=roll_sources,
    )
    _emit(live, event)
    if lr_remaining is not None:
        _emit_legendary_resistance_used(live, target_id, lr_remaining)
    return event


def _handle_grapple(live: _LiveCombat, attacker: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 Unarmed Strike — Grapple. Rolls the target's save via
    ``_roll_unarmed_option_save`` (shared with ``_handle_shove`` — auto-fail
    conditions + exhaustion penalty come free; no advantage source of its
    own — a plain D20 Test), so Paralyzed/Stunned/Petrified/Unconscious
    targets auto-fail with zero draws. On failure, applies the engine-owned
    Grappled condition with the escape DC + source effect stored on the
    ``ActiveCondition`` (see ``_emit_grapple_condition_applied``) — unless
    the target is immune to it (``is_condition_immune``: the Ghost). The
    Action is already spent (budget consumed by the caller); Grapple
    resolves no other activities and ends the turn (``_end_action``)."""
    assert intent.target_id is not None  # narrowed by the range gate above
    target_id = intent.target_id
    target = _find_combatant(live, target_id)
    assert target is not None  # narrowed by the range gate above
    save = _roll_unarmed_option_save(live, attacker, target)
    if not save.succeeded:
        if is_condition_immune(target, "grappled"):
            _LOGGER.info(
                "condition_immune_suppressed status=%s target_id=%s", "grappled", target_id
            )
        else:
            effect_id = f"effect:grapple:{live.round_number}:{attacker.entity_id}:{target_id}"
            effect = ActiveEffect(
                id=effect_id,
                name="Grappled",
                origin=f"grapple:unarmed-strike:{attacker.entity_id}",
                target_id=target_id,
                statuses={"grappled"},
            )
            live.active_effects.setdefault(target_id, []).append(effect)
            _emit_grapple_condition_applied(
                live, target_id, save_dc=save.dc, source_effect_id=effect_id
            )
    _end_action(live, attacker.entity_id, intent)


# SRD 5.2 Unarmed Strike, "Shove": "The target must succeed on a Strength or
# Dexterity saving throw (it chooses which), or you either push it 5 feet
# away or cause it to have the Prone condition. The DC for the saving throw
# equals 8 plus your Strength modifier and Proficiency Bonus. This shove is
# possible only if the target is no more than one size larger than you."
#
# Reuses Grapple's save shape byte-for-byte (``_unarmed_option_dc``, the
# higher-of-STR/DEX target save via ``_resolve_grapple_save_ability``, the
# ``_run_end_of_turn_saves``-style auto-fail/exhaustion machinery) — only the
# on-failure OUTCOME differs, per the shover's pre-declared
# ``intent.shove_push`` choice (no player-facing choice prompt exists at
# this seam, so the choice rides the intent).
#
# Out of scope (BACKLOG.md): the size gate ("no more than one size
# larger than you").


def _handle_shove(live: _LiveCombat, attacker: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 Unarmed Strike — Shove. Rolls the target's save via the same
    ``_roll_unarmed_option_save`` helper ``_handle_grapple`` uses (auto-fail
    conditions + exhaustion penalty come free; no advantage source of its
    own — a plain D20 Test). On failure: Prone (default; withheld from a
    creature immune to it, ``_emit_condition_applied``) or a 5-ft forced push
    away from the shover, per ``intent.shove_push``. No damage either way.
    The Action is already spent (budget consumed by the caller); Shove
    resolves no other activities and ends the turn (``_end_action``)."""
    assert intent.target_id is not None  # narrowed by the range gate above
    target_id = intent.target_id
    target = _find_combatant(live, target_id)
    assert target is not None  # narrowed by the range gate above
    save = _roll_unarmed_option_save(live, attacker, target)
    if not save.succeeded:
        if intent.shove_push:
            origin_cell = live.actor_zone.get(attacker.entity_id)
            assert origin_cell is not None  # the attacker is on the live grid
            push_combatant(live, target_id, origin_cell=origin_cell, distance_ft=5)
        else:
            _emit_condition_applied(live, target, "prone")
    _end_action(live, attacker.entity_id, intent)


def _escapable_grapple(current: Combatant) -> ActiveCondition | None:
    """The Grappled entry an escape rolls against: the first one storing an
    escape DC. Only a Grapple stores one; a Grappled an effect imposes (a
    seeded one, or one still kept by another effect after the grapple ends)
    carries none, so it gives an escape nothing to roll against."""
    return next(
        (ac for ac in current.conditions if ac.condition == "grappled" and ac.save_dc is not None),
        None,
    )


def _escape_grapple_actor_invalid(current: Combatant) -> bool:
    """True (reject) unless ``current`` is Grappled by a grapple with an
    escape DC (``_escapable_grapple``) — ``escape_grapple`` has nothing to
    roll against otherwise."""
    return _escapable_grapple(current) is None


def _reject_invalid_escape_grapple_actor(
    actor_id: str, intent: PlayerIntent, current: Combatant
) -> None:
    """Raise ``IntentRejectedError("target_invalid")`` for an
    ``escape_grapple`` from an actor who is not (or no longer) Grappled; a
    no-op for every other intent type. Split out (like ``_reject_invalid_
    help_target`` / ``_reject_invalid_grapple_target``) purely to keep
    ``submit_player_intent``'s cyclomatic complexity under the lint
    ceiling."""
    if intent.intent_type == "escape_grapple" and _escape_grapple_actor_invalid(current):
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} is not Grappled by a grapple with an escape DC; "
            "nothing to escape",
        )


def _handle_escape_grapple(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 "Ending a Grapple" — "A Grappled creature can use its action
    to make a Strength (Athletics) or Dexterity (Acrobatics) check against
    the grapple's escape DC, ending the condition on itself on a success."

    Reads the STORED escape DC off the actor's ``ActiveCondition`` (never
    recomputed — the grappler's Strength may have changed since). The escaper
    picks Athletics vs Acrobatics by whichever check modifier is
    higher (tie -> Athletics/STR), via the same ``check_modifier`` primitive
    every other skill check on this seam uses; Poisoned, and Frightened while
    its source is in sight, roll it at Disadvantage
    (``_condition_check_sources``). The Action is already spent (budget
    consumed by the caller); escape ends the turn (``_end_action``)."""
    grappled_ac = _escapable_grapple(current)
    assert grappled_ac is not None  # narrowed by the gate above
    assert grappled_ac.save_dc is not None  # ``_escapable_grapple`` picks one storing a DC
    dc = grappled_ac.save_dc
    athletics_mod = check_modifier(current, "str", "athletics").total
    acrobatics_mod = check_modifier(current, "dex", "acrobatics").total
    if athletics_mod >= acrobatics_mod:
        skill, ability, modifier = "athletics", "str", athletics_mod
    else:
        skill, ability, modifier = "acrobatics", "dex", acrobatics_mod
    # SRD 5.2 Exhaustion — "the roll is reduced by 2 times your Exhaustion
    # level" on EVERY D20 Test, ability checks included. The grapple SAVE
    # already threads this (mirroring ``_run_end_of_turn_saves``); the
    # escape check must too.
    modifier += d20_test_penalty(current.conditions)
    roll = roll_d20_test(live.rng, modifier, _condition_check_sources(live, current))
    succeeded = roll.total >= dc
    _emit(
        live,
        CheckRolled(
            actor_id=current.entity_id,
            ability=ability,
            skill=skill,
            dc=dc,
            roll_total=roll.total,
            succeeded=succeeded,
            advantage=roll.mode,
            natural=roll.kept,
            modifier=roll.modifier,
            sources=list(roll.sources),
        ),
    )
    if succeeded:
        _release_grapple_conditions(live, current.entity_id, grappled_ac)
    _end_action(live, current.entity_id, intent)


def _dispatch_simple_turn_ending_intent(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """Route the turn-ending intents that resolve no activities of their own
    — Dodge, Help, Grapple, Shove, ``escape_grapple`` — to their handlers and
    report whether one fired. Every gate (5-ft reach / Grappled-state) has
    already run in ``submit_player_intent`` before this is reached.
    Collapsing the dispatch into one call site (mirrors ``_dispatch_turn_
    nonending_intent``'s shape) keeps that function's cyclomatic complexity
    under the lint ceiling."""
    if intent.intent_type == "dodge":
        _set_dodging(live, actor_id)
        _end_action(live, actor_id, intent)
        return True
    if intent.intent_type == "help":
        assert intent.target_id is not None  # mypy: narrowed by the gate above
        live.help_grants.setdefault(intent.target_id, []).append(actor_id)
        _end_action(live, actor_id, intent)
        return True
    if intent.intent_type == "grapple":
        _handle_grapple(live, current, intent)
        return True
    if intent.intent_type == "shove":
        _handle_shove(live, current, intent)
        return True
    if intent.intent_type == "escape_grapple":
        _handle_escape_grapple(live, current, intent)
        return True
    return False


_CUNNING_ACTION: Final = "cunning-action"


def _require_cunning_action(current: Combatant, action: Literal["Dash", "Disengage"]) -> None:
    """SRD 5.2 Cunning Action (Rogue 2): "On your turn, you can take one of the
    following actions as a Bonus Action: Dash, Disengage, or Hide." Taking
    ``action`` as a Bonus Action needs the feature among the granted ones —
    each class at its own level, so neither a Rogue 1 nor a non-Rogue has it —
    and an unspent Bonus Action; else ``IntentRejectedError("no_action_economy")``."""
    actor_id = current.entity_id
    if _CUNNING_ACTION not in _granted_feature_slugs(current):
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={actor_id!r} cannot {action} as a Bonus Action without Cunning Action",
        )
    if not current.bonus_action_available:
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={actor_id!r} has no Bonus Action remaining for Cunning Action {action}",
        )


def _disengage_as_bonus_action(live: _LiveCombat, current: Combatant) -> None:
    """Cunning Action's Disengage: the Bonus Action instead of the Action, with
    the same effect — movement provokes no Opportunity Attacks for the rest of
    the turn."""
    _require_cunning_action(current, "Disengage")
    _update_combatant(
        live, current.entity_id, bonus_action_available=False, disengaging_this_turn=True
    )
    _emit(live, IntentSubmitted(actor_id=current.entity_id, intent_type="disengage"))


def _handle_dash(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD §Combat — Dash: double the actor's movement budget for this turn.

    Adds ``base_speed`` to ``movement_remaining`` and consumes either the
    Action (default; the Action path pays with the base Action, else an
    Action Surge extra action) or the Bonus Action (Cunning Action, when
    ``intent.use_bonus_action`` is True). Dash does NOT advance the turn.

    Rejections raise ``IntentRejectedError("no_action_economy")``:
      * ``use_bonus_action=True`` without Cunning Action among the granted
        features (``_require_cunning_action``)
      * the chosen budget slot is already spent
    """
    actor_id = current.entity_id
    budget_consumed: Literal["action", "bonus_action"]
    if intent.use_bonus_action:
        _require_cunning_action(current, "Dash")
        budget_consumed = "bonus_action"
    elif not (current.action_available or _extra_action_funds(current, "dash")):
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={actor_id!r} has no Action remaining for Dash",
        )
    else:
        budget_consumed = "action"

    # SRD 5.2 Dash adds the creature's (current) Speed; a Speed of 0 "can't increase".
    new_movement = current.movement_remaining + _effective_speed(current, live)
    payment: dict[str, Any] = (
        {"bonus_action_available": False}
        if budget_consumed == "bonus_action"
        else _action_payment(current, "dash")
    )
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(
                update={**payment, "movement_remaining": new_movement}
            )
            break
    _emit(
        live,
        DashTaken(
            actor_id=actor_id,
            doubled_movement_remaining=new_movement,
            budget_consumed=budget_consumed,
        ),
    )


def _handle_drop_concentration(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 §Concentration — "The creator can end Concentration at any
    time (no action required)." (Foundry actor.mjs:1065-1108
    ``endConcentration``: callable at any time, no action-economy gate.)

    Emits ``IntentSubmitted`` then cascades the drop via
    ``_drop_concentration``. Touches NO Action / Bonus Action / Reaction /
    movement budget and keeps the actor on turn. Idempotent: a caster with
    no active concentration gets only the ``IntentSubmitted`` marker.
    """
    _emit(
        live,
        IntentSubmitted(actor_id=current.entity_id, intent_type="drop_concentration"),
    )
    _drop_concentration(live, current.entity_id)


def _handle_hide(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 §Actions in Combat — Hide.

    *"You must succeed on a DC 15 Dexterity (Stealth) check while you're
    Heavily Obscured or behind Three-Quarters Cover or Total Cover, and
    you must be out of any enemy's line of sight... On a successful
    check, you have the Invisible condition while hidden."*

    Hide costs NO Action-economy budget at all — the same zero-cost,
    turn-keeping shape as ``_handle_drop_concentration``. The SRD actually
    costs an Action; this divergence is deliberately UNENFORCED pending
    strict Attack-action accounting (an Action-consuming Hide would make a
    hide-then-attack turn impossible against the hard Action gate the first
    attack swing enforces) — see BACKLOG.md.

    Gate: the hider's own cell must be behind Three-Quarters/Total cover,
    Heavily Obscured, or in Darkness (SRD 5.2 §Vision and Light glossary:
    "An area of darkness is Heavily Obscured."). The
    "out of any enemy's line of sight" conjunct then scans every living,
    non-Incapacitated hostile via ``_combatant_can_see`` — but ONLY when
    the hider's cell cover is not already Three-Quarters/Total, since
    per-cell cover is omnidirectional and already breaks every enemy's
    line of sight (SRD 5.2 §Cover). A failed gate raises
    ``IntentRejectedError("target_invalid")`` with NO d20 draw (zero
    stream perturbation on rejection).

    The Stealth check carries the hider's Poisoned / Frightened
    Disadvantage (``_condition_check_sources``).

    On success (unless the hider is immune to Invisible): emits
    ``ConditionApplied(condition="invisible")`` (the C12
    Invisible wiring already grants the hider's next attack Advantage and
    imposes Disadvantage on attacks against it) and records the hider in
    ``live.hidden_entities`` so the break-on-attack /
    break-on-verbal-cast hooks (attack path / cast_spell path) can end it.
    The found-DC / check total is intentionally NOT stored — Search
    (the SRD's "an enemy finds you" break clause) is a host concern out
    of this engine's scope.

    One Hide attempt per turn. Zero-cost + turn-
    keeping with no repeat gate would otherwise let a host loop ``hide``
    against the DC 15 check until it lands. A SECOND attempt this turn
    raises ``IntentRejectedError("no_action_economy")`` before the cover
    gate even runs — zero draws either way.
    """
    actor_id = current.entity_id
    if current.hide_attempted_this_turn:
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={actor_id!r} has already attempted Hide this turn",
        )
    cell = live.actor_zone.get(actor_id)
    cover = live.topology.cover_on_cell(cell) if cell is not None else "none"
    obscurement = live.topology.obscurement_on_cell(cell) if cell is not None else "none"
    light = live.topology.light_on_cell(cell) if cell is not None else "bright"
    if cover not in ("three_quarters", "total") and obscurement != "heavy" and light != "dark":
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} is not behind Three-Quarters/Total cover, "
            "Heavily Obscured, or in Darkness — Hide requires one of those",
        )

    # C16b — "you must be out of any enemy's line of sight".
    # Per-cell cover is omnidirectional, so Three-Quarters/Total cover on the
    # hider's cell already breaks every enemy's line; the conjunct bites only
    # for a hider relying on obscurement/darkness alone.
    if cover not in ("three_quarters", "total"):
        hider_side = _allied_ids(live, actor_id)
        for hostile in live.initiative:
            if (
                hostile.entity_id in hider_side
                or hostile.entity_id in live.dead_ids
                or not hostile.is_alive
            ):
                continue
            if conditions_block_actions(_condition_names(hostile)):
                continue
            if _combatant_can_see(live, hostile, current):
                raise IntentRejectedError(
                    "target_invalid",
                    f"actor_id={actor_id!r} is within line of sight of {hostile.entity_id!r} — "
                    "Hide requires being out of every enemy's line of sight",
                )

    # F3 — this attempt has cleared the cover gate and is now committed to
    # rolling; record it BEFORE the roll so a same-turn retry (success or
    # failure) is rejected regardless of this attempt's outcome.
    _set_hide_attempted(live, actor_id)

    _emit(
        live,
        IntentSubmitted(actor_id=actor_id, intent_type="hide"),
    )

    modifier = check_modifier(current, "dex", "stealth").total + d20_test_penalty(
        current.conditions
    )
    roll = roll_d20_test(live.rng, modifier, _condition_check_sources(live, current))
    dc = 15
    succeeded = roll.total >= dc
    _emit(
        live,
        CheckRolled(
            actor_id=actor_id,
            ability="dex",
            skill="stealth",
            dc=dc,
            roll_total=roll.total,
            succeeded=succeeded,
            advantage=roll.mode,
            natural=roll.kept,
            modifier=roll.modifier,
            sources=list(roll.sources),
        ),
    )
    if succeeded and _emit_condition_applied(live, current, "invisible"):
        live.hidden_entities.add(actor_id)


def _handle_stand_up(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 Prone, "Restricted Movement": *"you can spend an amount of
    movement equal to half your Speed (round down) to right yourself and
    thereby end the condition. If your Speed is 0, you can't right
    yourself."*

    Touches NO Action/Bonus Action budget (turn-KEEPING, like Hide/Dash) —
    only movement. Gates, in order: the actor must be Prone
    (``IntentRejectedError("target_invalid")``, mirrors ``_reject_invalid_
    escape_grapple_actor``'s "nothing to do this from" shape); effective
    Speed must be nonzero (``"speed_zero"``); the remaining movement budget
    must cover half Speed, rounded down (``"insufficient_movement"``). Every
    rejection draws no dice and leaves the Prone condition (and movement
    budget) untouched. On success, decrements ``movement_remaining`` by the
    cost and emits ``ConditionRemoved(prone)`` through ``_emit`` — a plain
    fold, since Prone (unlike Grappled) carries no stored ``save_dc``."""
    del intent  # stand_up carries no target/spell/item fields.
    actor_id = current.entity_id
    if not any(ac.condition == "prone" for ac in current.conditions):
        raise IntentRejectedError(
            "target_invalid",
            f"actor_id={actor_id!r} is not Prone; nothing to stand up from",
        )
    speed = _effective_speed(current, live)
    if speed == 0:
        raise IntentRejectedError(
            "speed_zero",
            f"actor_id={actor_id!r} has Speed 0; can't right themself (SRD 5.2 Prone)",
        )
    cost = speed // 2
    if current.movement_remaining < cost:
        raise IntentRejectedError(
            "insufficient_movement",
            f"actor_id={actor_id!r} has {current.movement_remaining} ft remaining, "
            f"needs {cost} ft (half Speed) to stand up",
        )
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(
                update={"movement_remaining": c.movement_remaining - cost}
            )
            break
    _emit(live, ConditionRemoved(target_id=actor_id, condition="prone"))


def _handle_drop_prone(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 Prone — "you can give yourself the Prone condition without
    using an action" (PHB 2024, «Падение на землю», printed p. 25; corpus rule
    `phb2024.phb01.drop_prone`: action_cost 0, movement_cost 0).

    Touches NO Action / Bonus Action / Reaction / movement budget and keeps
    the actor on turn (dispatched from `_dispatch_turn_nonending_intent`, the
    same zero-cost, turn-keeping shape as `_handle_drop_concentration`).
    An actor who is already Prone is refused with `IntentRejectedError(
    "already_prone")` before any event is emitted — symmetric with
    `_handle_stand_up`'s "nothing to stand up from" (and the host turns the
    reason into a player-readable phrase instead of its "no mechanical
    effect" guard, which used to be all a repeated Lie Down produced).
    A creature immune to Prone gets neither the condition nor a lie about
    it — `_emit_condition_applied` suppresses it. Standing back up is
    `_handle_stand_up` (half Speed), never this intent."""
    del intent  # drop_prone carries no target / spell / item / weapon fields.
    actor_id = current.entity_id
    if any(ac.condition == "prone" for ac in current.conditions):
        raise IntentRejectedError(
            "already_prone",
            f"actor_id={actor_id!r} is already Prone; nothing to drop",
        )
    _emit(live, IntentSubmitted(actor_id=actor_id, intent_type="drop_prone"))
    _emit_condition_applied(live, current, "prone")


def _break_hide_on_attack_or_verbal_cast(
    live: _LiveCombat,
    current: Combatant,
    intent: PlayerIntent,
    cast_spell: Spell | None,
) -> None:
    """SRD 5.2 Hide's break clause, the "attack roll" and "Verbal component"
    conjuncts: ends the hider's Invisible condition the instant they make
    an attack roll (any swing, main-hand or off-hand) or cast a spell whose
    typed ``components`` carry ``SpellComponent.VOCAL``. No-op for a
    non-hidden actor or any other intent kind. The loud-sound / found-by-
    Search break clauses are out of this engine's scope (host/Search
    concern); see ``_handle_hide``'s docstring.
    """
    if current.entity_id not in live.hidden_entities:
        return
    breaks = intent.intent_type == "attack" or (
        intent.intent_type == "cast_spell"
        and cast_spell is not None
        and SpellComponent.VOCAL in cast_spell.components
    )
    if breaks:
        _break_hide(live, current.entity_id)


def _break_hide(live: _LiveCombat, entity_id: str) -> None:
    """SRD 5.2 Hide: end the Invisible condition a successful Hide gave
    ``entity_id`` ("the condition ends on you immediately after ... you make
    an attack roll"); a no-op for a creature that isn't hidden."""
    if entity_id not in live.hidden_entities:
        return
    _emit(live, ConditionRemoved(target_id=entity_id, condition="invisible"))
    live.hidden_entities.discard(entity_id)


def _handle_disengage(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD §Actions in Combat, Disengage — *"Your movement doesn't provoke
    Opportunity Attacks for the rest of the turn."*

    Consumes the Action — or, with ``intent.use_bonus_action``, the Bonus
    Action through Cunning Action (``_disengage_as_bonus_action``) — and sets
    ``disengaging_this_turn`` so ``_opportunity_attackers`` lets no one, on
    either side, make an opportunity attack against it for the rest of the
    turn. Does NOT advance the turn (mirrors Dash) so a
    same-turn Disengage→Move sequence works. Rejects with
    ``IntentRejectedError("no_action_economy")`` when neither the Action nor
    an Action Surge extra action is left, or when the Bonus Action is asked
    for without Cunning Action.
    """
    if intent.use_bonus_action:
        _disengage_as_bonus_action(live, current)
        return
    actor_id = current.entity_id
    payment = _action_payment(current, "disengage")
    if not payment:
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={actor_id!r} has no Action remaining for Disengage",
        )
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={**payment, "disengaging_this_turn": True})
            break
    _emit(live, IntentSubmitted(actor_id=actor_id, intent_type="disengage"))


# The move_mark seam must align with the effect the typed cast emits. The typed
# Hunter's Mark PassiveEffect is named "Hunter's Mark", so the resolver
# synthesizes ``ActiveEffect.id = effect:hunter's_mark`` (via
# ``_effect_id_from_name``). The concentration-chain lookup matches on this id, so
# move_mark's identity tuples must use the SAME id/name the original cast records.
_MOVE_MARK_EFFECT_NAME = "Hunter's Mark"
_MOVE_MARK_EFFECT_ID = "effect:hunter's_mark"
# The slug used only for the (now-empty) legacy loader range lookup below.
_MOVE_MARK_SPELL_SLUG = "hunters-mark"


async def _handle_move_mark(live: _LiveCombat, caster: Combatant, intent: PlayerIntent) -> None:
    """Retarget the caster's live Hunter's Mark to a new combatant.

    SRD §Hunter's Mark — bonus action affordance triggered when the
    currently-marked target drops to 0 HP. Does not consume a fresh
    spell slot; concentration is unbroken.

    Emits ``CastFailed(reason=target_invalid)`` on any of:
      - caster not concentrating on hunters-mark
      - no live mark on any previously-marked target
      - the previously-marked target is still alive
      - the new target is missing / dead / out of range
      - no bonus action available
    """
    if not caster.bonus_action_available:
        _emit(
            live,
            CastFailed(
                actor_id=caster.entity_id,
                spell_id=_MOVE_MARK_EFFECT_NAME,
                reason="no_action_economy",
            ),
        )
        return

    chain = live.concentration_chain.get(caster.entity_id) or []
    # Pull every prior-marked target carrying a hunters-mark identity
    # tuple from this caster's concentration chain.
    old_mark_entries = [
        (target_id, effect_id, origin)
        for target_id, effect_id, origin in chain
        if effect_id == _MOVE_MARK_EFFECT_ID
    ]
    old_targets = [target_id for target_id, _eid, _o in old_mark_entries]
    if not old_targets:
        _emit(
            live,
            CastFailed(
                actor_id=caster.entity_id,
                spell_id=_MOVE_MARK_EFFECT_NAME,
                reason="target_invalid",
            ),
        )
        return

    new_target_id = intent.target_id
    new_target = next(
        (c for c in live.initiative if c.entity_id == new_target_id),
        None,
    )
    if new_target_id is None or new_target is None or not new_target.is_alive:
        _emit(
            live,
            CastFailed(
                actor_id=caster.entity_id,
                spell_id=_MOVE_MARK_EFFECT_NAME,
                reason="target_invalid",
            ),
        )
        return

    # The SRD affordance is gated on the *previously*-marked target
    # being at 0 HP. Reject if all the prior targets are still alive
    # (rare-but-possible edge: multiple historical re-targets where the
    # current mark is still up; we require at least one prior to be
    # dead, since the SRD trigger is "drops to 0").
    prior_alive = [
        tid
        for tid in old_targets
        if any(c.entity_id == tid and c.is_alive for c in live.initiative)
    ]
    if prior_alive:
        _emit(
            live,
            CastFailed(
                actor_id=caster.entity_id,
                spell_id=_MOVE_MARK_EFFECT_NAME,
                reason="target_invalid",
            ),
        )
        return

    # SRD §Hunter's Mark range 90ft — same gate as the original cast. The
    # typed ``Spell.range`` carries the band; only feet-valued ranges gate
    # (self/touch/special are not a metric distance). A missing
    # spell or non-feet range disables the gate exactly as the old
    # ``.get("range_ft")`` None did. Mirrors the casting-time/range gating
    # pattern in submit_player_intent.
    move_mark_spell = get_lib_loader().get_spell(_MOVE_MARK_SPELL_SLUG)
    range_ft = (
        move_mark_spell.range.value
        if move_mark_spell is not None and move_mark_spell.range.units == SpellRangeUnits.FEET
        else None
    )
    if isinstance(range_ft, int) and range_ft > 0:
        caster_zone = live.actor_zone.get(caster.entity_id)
        target_zone = live.actor_zone.get(new_target_id)
        if (
            caster_zone is not None
            and target_zone is not None
            and not live.topology.within_range(caster_zone, target_zone, range_ft)
        ):
            _emit(
                live,
                CastFailed(
                    actor_id=caster.entity_id,
                    spell_id=_MOVE_MARK_EFFECT_NAME,
                    reason="out_of_range",
                ),
            )
            return

    # Consume the bonus action.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == caster.entity_id:
            live.initiative[idx] = c.model_copy(update={"bonus_action_available": False})
            break

    _emit(
        live,
        IntentSubmitted(
            actor_id=caster.entity_id,
            intent_type="move_mark",
            spell_id=_MOVE_MARK_EFFECT_NAME,
            target_id=new_target_id,
        ),
    )

    # Expire the old mark(s) on every prior target. Effect-lifecycle
    # discipline: state mutations flow through EffectExpired /
    # EffectApplied via _emit, so a host that mirrors effects sees both.
    for old_target_id, old_effect_id, old_origin in old_mark_entries:
        _emit(
            live,
            EffectExpired(
                target_id=old_target_id,
                effect_id=old_effect_id,
                origin=old_origin,
                reason="moved",
            ),
        )

    # Re-target the persistent concentration chain so the rider-damage
    # projection finds the new marked target. Hunters-mark is the only
    # concentration effect this caster carries after move_mark; replace
    # any prior hunters-mark identity tuples wholesale.
    #
    # This bypasses ``_record_effect_lifecycle_links``'s one-at-a-time
    # enforcement (the "cascade the prior chain and drop it" branch)
    # DELIBERATELY: the initial Hunter's Mark cast already went through
    # ``cast_spell`` and set up the chain/counter there; a move_mark
    # re-target is not a new cast, it's the SAME concentration effect
    # continuing on a new target, so it must preserve the original
    # ``concentration_rounds_remaining`` countdown rather than restart or
    # clear it. A future change touching either path should keep this
    # divergence intentional, not reintroduce the general-cast gap here.
    new_origin = f"cast:{_MOVE_MARK_EFFECT_NAME}:{caster.entity_id}"
    new_identity = (new_target_id, _MOVE_MARK_EFFECT_ID, new_origin)
    surviving_chain = [
        entry
        for entry in (live.concentration_chain.get(caster.entity_id) or [])
        if entry[1] != _MOVE_MARK_EFFECT_ID
    ]
    surviving_chain.append(new_identity)
    live.concentration_chain[caster.entity_id] = surviving_chain

    new_effect = ActiveEffect(
        id=_MOVE_MARK_EFFECT_ID,
        name=_MOVE_MARK_EFFECT_NAME,
        origin=new_origin,
        target_id=new_target_id,
        duration=ActiveEffectDuration(seconds=600),
        flags={"concentration": True},
    )
    _emit(
        live,
        EffectApplied(effect=new_effect),
    )

    # Bonus action — caster keeps the turn (SRD §Action Economy).


def _emit(live: _LiveCombat, event: CombatEvent) -> None:
    """Append to the event log, update outcome-tracking running state, then enqueue.

    Per Agent 03 (outcome-population): the orchestrator is the single source
    of truth for end-state derivation. Per-effect handlers emit canonical
    ``CombatEvent`` instances; this listener folds them into running totals
    so ``end_combat`` can project a populated ``CombatOutcome`` without
    re-reading per-effect state.

    Side-effect: when a non-PC combatant's running HP drops to ≤0 from a
    ``DamageApplied`` (and we have not already recorded its death), the
    orchestrator synthesizes a ``Death(reason="damage")`` event. This mirrors
    legacy combat semantics — monsters die immediately at 0 HP (SRD §Damage
    at 0 Hit Points); only PCs route through death saves.
    """
    live.event_log.append(event)
    live.event_queue.put_nowait(event)
    for listener in live.event_listeners:
        listener(event)

    if isinstance(event, TurnStarted):
        _emit_apply_turn_started(live, event)
        return

    if isinstance(event, DamageApplied):
        _emit_apply_damage(live, event)
        return

    if isinstance(event, HealingApplied):
        _emit_apply_healing(live, event)
        return

    if isinstance(event, TempHpApplied):
        _emit_apply_temp_hp(live, event)
        return

    if isinstance(event, ConditionApplied):
        # Both stores are written by the helper — see its docstring.
        _fold_condition_onto_combatant(live, event.target_id, event.condition)
        return

    if isinstance(event, ConditionRemoved):
        live.active_conditions.get(event.target_id, set()).discard(event.condition)
        _strip_condition_from_combatant(live, event.target_id, event.condition)
        return

    if isinstance(event, EffectApplied):
        _emit_apply_effect_applied(live, event)
        return

    if isinstance(event, EffectExpired):
        _emit_apply_effect_expired(live, event)
        return

    if isinstance(event, Death):
        if event.target_id in live.dead_ids:
            return
        _record_death(live, event, killer_id=live.current_actor_id)


def _emit_apply_turn_started(live: _LiveCombat, event: TurnStarted) -> None:
    """Fold a ``TurnStarted`` into running state: set the current actor and
    refresh that actor's per-turn Action / Bonus Action / Reaction / movement
    budgets on the initiative slot."""
    live.current_actor_id = event.actor_id
    # SRD §Action Economy — refresh the actor's per-turn budgets on the
    # start of their own turn. The reaction line ("You regain your
    # reaction at the start of your turn") and the Action/Bonus Action
    # budgets all reset here; consumption sites in submit_player_intent
    # (and, for reactions, future off-turn intent paths) are the only
    # writers that flip these False.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == event.actor_id:
            live.initiative[idx] = c.model_copy(
                update={
                    "action_available": True,
                    "bonus_action_available": True,
                    "reaction_available": True,
                    # SRD §Movement — the budget refreshes to the actor's
                    # EFFECTIVE Speed (Speed-0 conditions, Exhaustion) at the
                    # start of their turn. Per-MOVE-intent decrement is the
                    # only other writer; this is the only reset.
                    "movement_remaining": _effective_speed(c, live),
                    # SRD §Disengage — "for the rest of the turn"; this is
                    # the start of a NEW turn, so the suppression lapses.
                    "disengaging_this_turn": False,
                    # SRD §Extra Attack / §Two-Weapon Fighting — refresh the
                    # per-Action attack budget and clear the TWF window at
                    # the start of the actor's own turn.
                    "attacks_remaining": _attacks_per_action(live, c),
                    "attack_action_engaged": False,
                    "light_weapon_swing_slug": None,
                    "offhand_attack_spent": False,
                    # SRD §Actions in Combat — Dodge: "until the start of
                    # your next turn". The reset here, at the dodger's OWN
                    # turn start, is the exact SRD expiry point.
                    "dodging": False,
                    # SRD 5.2 §Actions in Combat — Hide (F3): one attempt per
                    # turn; the gate resets alongside Dodge at the actor's own
                    # TurnStarted.
                    "hide_attempted_this_turn": False,
                    # SRD 5.2 Loading — the one-fire-per-turn cap resets at
                    # the actor's own TurnStarted, alongside the other
                    # per-turn attack-economy fields.
                    "loading_weapon_fired_this_turn": False,
                    # SRD 5.2 §Weapon Mastery — Cleave: "only once per turn";
                    # the cap resets at the actor's own TurnStarted.
                    "cleave_spent_this_turn": False,
                    # SRD 5.2 Flurry of Blows — strikes still owed lapse at
                    # the actor's own turn start.
                    "flurry_strikes_remaining": 0,
                    # SRD 5.2 Action Surge — an unspent additional action and
                    # the once-per-turn mark lapse at the actor's turn start.
                    "extra_actions_remaining": 0,
                    "action_surge_used_this_turn": False,
                }
            )
            break
    # SRD 5.2 Sneak Attack and Savage Attacker: "Once per turn" — any creature's
    # turn, not only the attacker's own: an opportunity attack on another
    # creature's turn can use them again.
    for idx, c in enumerate(live.initiative):
        if c.sneak_attack_spent_this_turn or c.savage_attacker_spent_this_turn:
            live.initiative[idx] = c.model_copy(
                update={
                    "sneak_attack_spent_this_turn": False,
                    "savage_attacker_spent_this_turn": False,
                }
            )
    # SRD 5.2 §Actions in Combat — Help: "This benefit expires at the start
    # of your next turn" — the HELPER's own next turn, not the helped-
    # against target's. Strip this actor's entity_id out of every grant
    # list (an unconsumed grant simply lapses); drop any list left empty so
    # ``help_grants`` never accumulates dead keys.
    for target_id in list(live.help_grants):
        helpers = [h for h in live.help_grants[target_id] if h != event.actor_id]
        if helpers:
            live.help_grants[target_id] = helpers
        else:
            del live.help_grants[target_id]
    # SRD 5.2 §Weapon Mastery — Sap: "before the start of your
    # next turn" — the SOURCE ATTACKER's own next turn, not the sapped
    # creature's. Clear every outstanding mark this actor sourced (an
    # unconsumed mark simply lapses), mirroring the Help sweep above.
    for sapped_id in [sid for sid, src in live.sap_marks.items() if src == event.actor_id]:
        del live.sap_marks[sapped_id]
    # SRD 5.2 §Weapon Mastery — Slow: "until the start of your
    # next turn" — again the SOURCE attacker's own next turn. Drop this
    # actor out of every slowed creature's source set; a creature whose set
    # empties is no longer slowed (its Speed projects normally from the
    # next read; an in-flight budget is never RAISED — only its own next
    # TurnStarted refreshes it).
    for slowed_id in list(live.slow_marks):
        sources = live.slow_marks[slowed_id]
        sources.discard(event.actor_id)
        if not sources:
            del live.slow_marks[slowed_id]


def _hook_expire_reaction_effects(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_start`` hook — SRD Shield / one-round reaction buffs.

    An effect this actor cast off-turn (via the reaction queue) with a
    round-scoped duration expires at the OWNER's own next turn start, rather
    than waiting for a ``TurnEnded`` that may be a full round later (see
    ``docs/dev/reaction-queue.md``, "Duration-fix semantics"). Before F3a this
    ran inline inside ``_emit_apply_turn_started``; it is now a registered
    lifecycle hook and therefore fires just after the ``TurnPhase(turn_start)``
    marker instead of during the ``TurnStarted`` fold — the relative order of
    the ``EffectExpired`` events against every pre-existing event is unchanged.
    """
    if actor_id is None:
        return
    pending_expiry = live.reaction_effects_pending_expiry.pop(actor_id, None)
    if not pending_expiry:
        return
    for target_id, effect_id, origin in pending_expiry:
        still_present = any(
            eff.id == effect_id and eff.origin == origin
            for eff in live.active_effects.get(target_id, [])
        )
        if still_present:
            _emit(
                live,
                EffectExpired(
                    effect_id=effect_id,
                    target_id=target_id,
                    origin=origin,
                    reason="duration",
                ),
            )


def _emit_apply_damage(live: _LiveCombat, event: DamageApplied) -> None:
    """Fold a ``DamageApplied`` into running state: temp-HP absorption, HP
    tracking + initiative sync, ``last_damaged_by`` attribution, the
    concentration CON save cascade, and non-PC death synthesis."""
    tracked = live.tracked_hp.get(event.target_id)
    if tracked is None:
        return
    # Temp HP absorbs first (SRD §Temporary Hit Points).
    temp = live.tracked_temp_hp.get(event.target_id, 0)
    remaining = event.amount
    if temp > 0:
        absorbed = min(temp, remaining)
        live.tracked_temp_hp[event.target_id] = temp - absorbed
        remaining -= absorbed
    # SRD 5.2 Polymorph ends the moment its Temporary Hit Points are gone; the
    # rest of this hit then lands on the creature's own Hit Points.
    _end_polymorph_on_depletion(live, event.target_id)
    new_hp = max(0, tracked - remaining)
    # C18 §Monster action economy — SRD 5.2 stat-block trait
    # "Undead Fortitude": "On a successful save, the [monster] drops to 1
    # Hit Point instead." ``activities/apply.py`` already rolled the save
    # and reported the FULL, unmodified ``event.amount`` (so the
    # narration keeps saying "took 12 damage"); THIS is the one place that
    # full amount would otherwise drop ``tracked_hp`` to ≤0 and (below)
    # synthesize a ``Death``. Popped UNCONDITIONALLY (single-use, whether or
    # not it actually changes the outcome here) so a stale flag from an
    # unrelated damage instance against the same entity never leaks into a
    # later, uncorrelated ``DamageApplied`` for it.
    undead_fortitude_saved = live.undead_fortitude_holds.pop(event.target_id, False)
    if new_hp <= 0 and undead_fortitude_saved:
        new_hp = 1
    # SRD 5.2 Relentless Rage (Barbarian 11): "If you drop to 0 Hit Points
    # while your Rage is active and don't die outright, you can make a DC 10
    # Constitution saving throw. If you succeed, your Hit Points instead
    # change to a number equal to twice your Barbarian level." This fold is
    # the one place damage becomes Hit Points, so the trigger lives here.
    if new_hp <= 0:
        new_hp = _relentless_rage_hold(
            live, event.target_id, hp_before=tracked, damage=remaining
        )
    live.tracked_hp[event.target_id] = new_hp
    # Sync hp_current / temp_hp on the initiative slot so downstream
    # readers (monster gambit targeting, OA HP checks, hydration
    # passive projection) observe the post-damage state instead of the
    # opening snapshot. Combined into the same model_copy as the
    # ``last_damaged_by`` update below.
    new_temp_hp = live.tracked_temp_hp.get(event.target_id, 0)
    damager = live.current_actor_id
    update_payload: dict[str, Any] = {
        "hp_current": new_hp,
        "temp_hp": new_temp_hp,
    }
    # SRD §Hellish Rebuke — track the *creature that damaged you* on the
    # target combatant. ``current_actor_id`` is the canonical "who is
    # acting" (same source used for kill attribution below). Self-damage
    # (e.g. reaction damage back at the actor) is excluded so HR's
    # validation can't ping-pong.
    if damager and damager != event.target_id:
        update_payload["last_damaged_by"] = damager
    for idx, c in enumerate(live.initiative):
        if c.entity_id == event.target_id:
            live.initiative[idx] = c.model_copy(update=update_payload)
            break
    # SRD §Concentration on Damage — *"You must make a Constitution
    # saving throw … DC = 10 or half the damage taken, whichever is
    # higher. On a failed save, the spell ends."* If the damaged
    # combatant is concentrating on an effect (tracked in
    # ``concentration_chain``), roll the CON save and cascade on
    # failure. The save applies the concentrating creature's real CON
    # modifier + proficiency bonus (F1c, via ``actor_stats``) and goes
    # through the shared ``roll_d20_test`` primitive (F2c) with no
    # advantage source, so it is still a single draw.
    # Capped at 30 — SRD 5.2 "up to a maximum DC of 30" (2024-specific; the 2014
    # formula has no cap).
    # Done BEFORE death synthesis so a dropped-conc + slain caster
    # still surface the cascade before the Death event.
    caster_chain = live.concentration_chain.get(event.target_id)
    if caster_chain:
        dc = min(30, max(10, event.amount // 2))
        concentrator = _find_combatant(live, event.target_id)
        # The ``else 0`` cannot fire in practice: ``concentration_chain`` is
        # only ever keyed by an entity that is in ``live.initiative``, and the
        # damage that got us here was applied to that same combatant. It stays
        # because ``_emit_apply_damage`` is contractually non-throwing — a
        # missing combatant degrades to an unmodified save rather than
        # aborting damage application mid-flight.
        # SRD 5.2 Exhaustion — the concentration CON save is a D20 Test.
        modifier = (
            save_modifier(concentrator, "con").total + d20_test_penalty(concentrator.conditions)
            if concentrator
            else 0
        )
        roll = roll_d20_test(live.rng, modifier, AdvantageSources())
        roll_total = roll.total
        succeeded = roll_total >= dc
        # C18 §Monster action economy — Legendary Resistance: the
        # concentration check bypasses ``activities/save_primitive.roll_save``
        # (it rolls its own d20 above), so the conversion is decided before
        # its ``ConcentrationCheck`` carries ``succeeded`` and
        # ``LegendaryResistanceUsed`` is emitted after it — via the same
        # shared helpers the repeat save uses.
        lr_remaining = (
            _consume_armed_legendary_resistance(live, concentrator)
            if not succeeded and concentrator is not None
            else None
        )
        if lr_remaining is not None:
            succeeded = True
        _emit(
            live,
            ConcentrationCheck(
                target_id=event.target_id,
                dc=dc,
                roll_total=roll_total,
                succeeded=succeeded,
                advantage=roll.mode,
                natural=roll.kept,
                modifier=roll.modifier,
                sources=list(roll.sources),
            ),
        )
        if lr_remaining is not None:
            _emit_legendary_resistance_used(live, event.target_id, lr_remaining)
        if not succeeded:
            _drop_concentration(live, event.target_id)
    if new_hp <= 0 and event.target_id not in live.dead_ids:
        if event.target_id in live.summons:
            # SRD 5.2 Summon Dragon: "The creature disappears when it drops to
            # 0 Hit Points" — no death, death save or XP. Its caster keeps
            # concentrating (the SRD is silent; a host can drop it).
            _leave_roster(live, event.target_id, "zero_hp")
        elif event.target_id in live.party_ids:
            _apply_zero_hp_to_character(live, event, hp_before=tracked, damage_after_temp=remaining)
        else:
            # SRD 5.2 "Monster Death" — a monster dies the instant it drops to
            # 0 HP. Recursion guard: _emit re-enters here for the Death, but
            # the dead_ids set blocks double-recording, and Death's only
            # running-state effect is to record the death.
            killer = live.current_actor_id
            death_event = Death(target_id=event.target_id, reason="damage")
            _record_death(live, death_event, killer_id=killer)
            live.event_log.append(death_event)
            live.event_queue.put_nowait(death_event)


def _apply_zero_hp_to_character(
    live: _LiveCombat, event: DamageApplied, *, hp_before: int, damage_after_temp: int
) -> None:
    """SRD 5.2 "Dropping to 0 Hit Points" for a Character.

    * Massive Damage: "When damage reduces a character to 0 Hit Points and
      damage remains, the character dies if the remainder equals or exceeds
      their Hit Point maximum." -> ``Death(reason="instant_kill")``.
    * Falling Unconscious: otherwise the character gains the Unconscious
      condition (``ConditionApplied``; the legacy ``Unconscious`` marker is
      emitted first for hosts that narrate it) and death saves begin on their
      next turn (``_maybe_roll_death_save``).
    * Damage at 0 Hit Points: "If you take any damage while you have 0 Hit
      Points, you suffer a Death Saving Throw failure. ... If the damage
      equals or exceeds your Hit Point maximum, you die." A Critical Hit
      counts as TWO failures instead of one (C15 — ``DamageApplied.is_crit``
      threaded into ``DeathSaveState.apply_damage_while_unconscious``).
    """
    target = _find_combatant(live, event.target_id)
    if target is None:
        return
    hp_max = _hp_max_for(live, event.target_id)
    remainder = damage_after_temp - hp_before if hp_before > 0 else damage_after_temp
    if remainder >= hp_max:
        death_event = Death(target_id=event.target_id, reason="instant_kill")
        _record_death(live, death_event, killer_id=live.current_actor_id)
        live.event_log.append(death_event)
        live.event_queue.put_nowait(death_event)
        for idx, c in enumerate(live.initiative):
            if c.entity_id == event.target_id:
                live.initiative[idx] = c.model_copy(update={"is_alive": False})
                break
        return
    if hp_before > 0:
        if "unconscious" not in _condition_names(target):
            _emit(live, Unconscious(target_id=event.target_id))
            _emit(live, ConditionApplied(target_id=event.target_id, condition="unconscious"))
        return
    # SRD 5.2 charges a failure for damage TAKEN: an immune damage type (or a
    # hit fully absorbed by temp HP) reaches this fold with nothing left, and
    # ``activities/apply.py`` emits ``DamageApplied`` unconditionally.
    if damage_after_temp <= 0:
        return
    state = DeathSaveState.from_dict(target.death_saves) if target.death_saves else DeathSaveState()
    # SRD 5.2 "Damage at 0 Hit Points" — the Critical-Hit two-failure clause:
    # a Critical Hit against a creature already at 0 HP counts
    # as TWO death-save failures instead of one. ``DamageApplied.is_crit``
    # (this event) now carries that flag straight from the attack resolver.
    outcome = state.apply_damage_while_unconscious(event.is_crit)
    update: dict[str, Any] = {"death_saves": state.to_dict()}
    if outcome == "dead":
        update["is_alive"] = False
    for idx, c in enumerate(live.initiative):
        if c.entity_id == event.target_id:
            live.initiative[idx] = c.model_copy(update=update)
            break
    if outcome == "dead":
        death_event = Death(target_id=event.target_id, reason="death_saves")
        _record_death(live, death_event, killer_id=live.current_actor_id)
        live.event_log.append(death_event)
        live.event_queue.put_nowait(death_event)


def _emit_apply_healing(live: _LiveCombat, event: HealingApplied) -> None:
    """Fold a ``HealingApplied`` into running state: HP tracking (capped at
    max), initiative sync, and the 0→positive revive (clear death saves +
    unconscious condition)."""
    tracked = live.tracked_hp.get(event.target_id)
    if tracked is None:
        return
    cap = _hp_max_for(live, event.target_id)
    new_hp = min(cap, tracked + event.amount)
    live.tracked_hp[event.target_id] = new_hp
    # SRD §Death Saves — "If a creature with 0 hit points regains any
    # hit points, it becomes conscious again." When tracked HP
    # transitions 0 → positive, flip ``is_alive`` back True, clear the
    # death-save counters, and drop the ``unconscious`` ActiveCondition
    # bridged by the dying state. HP sync runs unconditionally so
    # downstream readers (monster gambit targeting, hydration) observe
    # the post-heal state even when the heal didn't cross the 0->1
    # revive boundary.
    revived = tracked == 0 and new_hp > 0
    revived_ids: list[str] = []
    for idx, c in enumerate(live.initiative):
        if c.entity_id == event.target_id:
            heal_update: dict[str, Any] = {"hp_current": new_hp}
            if revived:
                heal_update["is_alive"] = True
                heal_update["death_saves"] = {}
                # SRD 5.2 Unconscious: "When this condition ends, you remain Prone."
                kept = [cond for cond in c.conditions if cond.condition != "unconscious"]
                if not any(cond.condition == "prone" for cond in kept):
                    kept.append(
                        ActiveCondition(
                            condition="prone",
                            source_entity_id="implied:revive",
                            scope="combat",
                            applied_round=live.round_number,
                        )
                    )
                heal_update["conditions"] = kept
                revived_ids.append(c.entity_id)
            live.initiative[idx] = c.model_copy(update=heal_update)
            break
    for entity_id in revived_ids:
        _emit(live, ConditionRemoved(target_id=entity_id, condition="unconscious"))
        _emit(live, ConditionApplied(target_id=entity_id, condition="prone"))


def _emit_apply_temp_hp(live: _LiveCombat, event: TempHpApplied) -> None:
    """Fold a ``TempHpApplied`` into running state: max-not-additive temp-HP
    tracking + initiative slot sync."""
    # SRD §Temporary Hit Points — new amount replaces existing if higher,
    # not additive (the legacy evaluator/Open5e canonical behavior).
    current = live.tracked_temp_hp.get(event.target_id, 0)
    new_temp = max(current, event.amount)
    live.tracked_temp_hp[event.target_id] = new_temp
    # Sync the initiative slot's temp_hp so downstream readers
    # (hydration, passive projection) observe the post-grant state.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == event.target_id:
            live.initiative[idx] = c.model_copy(update={"temp_hp": new_temp})
            break


def _effect_toggle_key(effect: ActiveEffect) -> tuple[Any, Any] | None:
    """The identity a repeat activation of the SAME item toggles off.

    SRD 5.2 Boots of Speed: "If you click your heels together again, you end
    the effect." Clicking twice must therefore turn the doubling OFF rather
    than stack a second x2. Only item-sourced effects with a duration toggle:
    a spell landing the same effect twice is two independent casts, and a
    concentration effect is ended by the caster, not by clicking.
    """
    flags = effect.flags or {}
    if flags.get("concentration"):
        return None
    item = flags.get("item") or flags.get("item_id")
    if not item:
        return None
    return (item, effect.name)


def _strip_conditions_from_effect(live: _LiveCombat, target_id: str, effect_id: str) -> None:
    """Drop the conditions ``effect_id`` landed on ``target_id``.

    ``ActiveCondition.source_effect_id`` is the link (the same key
    ``_emit_apply_effect_applied`` stamps), so ending one effect takes only
    its own conditions and leaves another effect's copy of the same name.
    """
    target = _find_combatant(live, target_id)
    if target is None:
        return
    kept = [ac for ac in target.conditions if ac.source_effect_id != effect_id]
    if len(kept) == len(target.conditions):
        return
    live.initiative = [
        c.model_copy(update={"conditions": kept}) if c.entity_id == target_id else c
        for c in live.initiative
    ]


def _end_toggled_off_effects(live: _LiveCombat, applied: ActiveEffect) -> bool:
    """Drop effects a repeat activation switches OFF; True when it did.

    Returns True (emitting ``EffectExpired`` for the effect being ended) when
    the same item already has an identical effect on this target, in which
    case the incoming application is not added at all.
    """
    key = _effect_toggle_key(applied)
    if key is None:
        return False
    held = live.active_effects.get(applied.target_id, [])
    doomed = [effect for effect in held if _effect_toggle_key(effect) == key]
    if not doomed:
        return False
    survivors = [effect for effect in held if _effect_toggle_key(effect) != key]
    live.active_effects[applied.target_id] = survivors
    for effect in doomed:
        _strip_conditions_from_effect(live, applied.target_id, effect.id)
        _emit(
            live,
            EffectExpired(
                target_id=applied.target_id,
                effect_id=effect.id,
                origin=effect.origin,
                reason="toggled_off",
            ),
        )
        _LOGGER.info(
            "effect_toggled_off target=%s effect=%s",
            applied.target_id,
            effect.name,
        )
    # The effect's speed is gone: a budget that a switched-on effect RAISED
    # must not outlive it (Boots of Speed x2, Spider Climb, Dragon Wings).
    # ``_grant_movement_headroom`` gave that headroom when it landed;
    # without this the doubled stride stayed for the rest of the turn --
    # the character spent Speed it no longer had (§7).
    _clamp_movement_budget(live, applied.target_id)
    return True


def _effect_escape_dc(effect: ActiveEffect, status: str) -> int | None:
    """The escape DC a status this effect just imposed stores, or ``None``.

    SRD 5.2 names exactly one condition a creature ends with a check of its own
    ("Ending a Grapple": *"make a Strength (Athletics) or Dexterity
    (Acrobatics) check against the grapple's escape DC"*), so only that status
    carries one; every other condition stores ``None`` and behaves exactly as
    before. The number rides ``ActiveEffect.flags["escape_dc"]`` — stamped by
    ``activities/effects.py::_effect_flags`` from the defining effect's printed
    ``(escape DC N)`` — because the emit-side ``ConditionApplied`` event has no DC
    field and this fold no longer holds the effect definition. An unreadable value
    is logged and treated as "no DC": the condition still lands, it just gives an
    escape attempt nothing to roll (the pre-r199 behaviour), never a guessed DC.
    """
    if status != "grappled":
        return None
    value = (effect.flags or {}).get("escape_dc")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        _LOGGER.warning("escape_dc_unreadable effect=%s value=%r", effect.id, value)
        return None


def _emit_apply_effect_applied(live: _LiveCombat, event: EffectApplied) -> None:
    """Fold an ``EffectApplied`` into running state: track the active effect,
    stamp one ``ActiveCondition`` entry per (status, effect) — the same key
    the ``start_combat`` seed stamps (``_seed_active_effects``) — onto the
    target's conditions, and record concentration spell-slot expenditure
    for PCs."""
    applied = event.effect
    # SRD 5.2 Rage: "Your Concentration ends" — the moment the Rage effect
    # lands on a creature that was concentrating, the concentration cascade
    # runs first (ConcentrationDropped + the dropped spell's EffectExpired /
    # ConditionRemoved), so the barbarian never holds two at once.
    if applied.id == _RAGE_EFFECT_ID and _find_combatant(live, applied.target_id) is not None:
        _drop_concentration(live, applied.target_id)
    # A repeat activation of the same item switches its effect OFF (Boots of
    # Speed: "If you click your heels together again, you end the effect"),
    # so the incoming application must not land a second one.
    if _end_toggled_off_effects(live, applied):
        return
    live.active_effects.setdefault(applied.target_id, []).append(applied)
    # SRD 5.2 §Movement — a speed the effect just turned on is available
    # from that moment: Dragon Wings' Fly Speed 60 must be spendable this
    # turn, not only from the next turn-start reset. The turn-start deal and
    # Dash are the only other writers; ``_clamp_movement_budget`` only ever
    # lowers, and the new effect is already in ``active_effects`` here, so
    # this grants exactly the headroom the effect added (nothing for an
    # effect that leaves the fastest speed unchanged).
    _grant_movement_headroom(live, applied.target_id)
    # One entry per (status, effect), the seed's key: an effect that lands a
    # condition the target already holds from another effect gets its own
    # entry, so losing either effect later (``_emit_apply_effect_expired`` /
    # ``_strip_condition_from_combatant``, both keyed on ``source_effect_id``)
    # removes only its entry and the condition survives on the other's.
    # Passive projections (advantage/disadvantage on attack, save, etc.)
    # observe the new state immediately.
    target_combatant = _find_combatant(live, applied.target_id)
    added: list[str] = []
    if target_combatant is not None and applied.statuses:
        # Captured BEFORE any entry below is added: the first-landing hooks
        # near the end of this function fire once per condition NAME newly
        # held, not once per owning effect — a second effect landing a
        # condition the creature already has isn't a new incapacitation
        # (``_end_what_incapacitation_ends``'s own invariant: "runs once
        # however the condition arrives").
        already_named = {ac.condition for ac in target_combatant.conditions}
        existing_keys = {(ac.condition, ac.source_effect_id) for ac in target_combatant.conditions}
        new_conditions = list(target_combatant.conditions)
        dirty = False
        for status in applied.statuses:
            # SRD §Condition Immunity — an immune target never acquires the
            # condition. ``activities/effects.py::apply_activity_effects``
            # already SUPPRESSES the matching ``ConditionApplied``; without the
            # same gate here the status would still land on
            # ``Combatant.conditions`` and drive every C12 projection (the
            # Incapacitated action block, ``project_speed``, the STR/DEX save
            # auto-fail, the within-5-ft auto-crit) against a creature that
            # cannot have the condition — and would diverge from
            # ``live.active_conditions``, the store ``views.py`` shows the host.
            # The shared predicate, so a condition-granted immunity counts
            # too: Petrified — held, or arriving in this same effect — keeps
            # Poisoned off. Runs for every status regardless of whether the
            # NAME is already held — being already Paralyzed grants no
            # immunity to Paralyzed, so a second genuine source must still
            # clear this gate on its own.
            if is_condition_immune(target_combatant, status, imposed=applied.statuses):
                _LOGGER.info(
                    "condition_immune_not_folded status=%s target_id=%s",
                    status,
                    applied.target_id,
                )
                continue
            # The seed's (status, effect id) key. Effect ids derive from the
            # effect's name, so one spell from two casters lands two effects
            # with one id: they share this entry, which the expiry and
            # ConditionRemoved folds keep while either effect lasts.
            if (status, applied.id) in existing_keys:
                continue
            # Derive source_entity_id from the origin tag when it
            # encodes one (e.g. "cast:bless:char:abc12"); otherwise
            # default to the canonical implied-source marker.
            source_entity_id = "implied:effect"
            new_conditions.append(
                ActiveCondition(
                    condition=status,
                    source_entity_id=source_entity_id,
                    scope="combat",
                    source_effect_id=applied.id,
                    # r199: SRD 5.2 "Ending a Grapple" rolls the victim's escape
                    # against the STORED DC. The imprinting effect carries it in
                    # flags (from the corpus definition's printed "(escape DC N)"),
                    # so the condition is materialised with a real escape DC and
                    # `escape_grapple` has something to roll against.
                    save_dc=_effect_escape_dc(applied, status),
                )
            )
            dirty = True
            if status not in already_named:
                added.append(status)
        if dirty:
            for idx, c in enumerate(live.initiative):
                if c.entity_id == applied.target_id:
                    live.initiative[idx] = c.model_copy(update={"conditions": new_conditions})
                    break
        # C12 — a newly applied Speed-0 / Exhaustion condition immediately
        # caps whatever movement the target had left this turn.
        _clamp_movement_budget(live, applied.target_id)
    # SRD spell-slot consumption: spell effects with concentration imply
    # a slot was spent. The slot level is not on the event today (follow-up
    # in the cutover); we record under a coarse "slots" label keyed by name.
    is_concentration = bool(applied.flags.get("concentration"))
    if is_concentration and applied.target_id in live.party_ids:
        bucket = live.expended_resources.setdefault(applied.target_id, {})
        bucket[applied.name] = bucket.get(applied.name, 0) + 1
    # This fold, not the ``ConditionApplied`` that follows, first writes an
    # effect's status onto the combatant, so this is where an Incapacitated
    # status ends concentration, grapples and Rage. The effect is fully folded
    # first.
    for status in added:
        _end_what_incapacitation_ends(live, applied.target_id, status)


def _emit_apply_effect_expired(live: _LiveCombat, event: EffectExpired) -> None:
    """Fold an ``EffectExpired`` into running state: pop the matching effect,
    then clear each status it imposed from both ``live.active_conditions`` and
    the target's conditions — unless that status's own ``ActiveCondition``
    entry is owned by a DIFFERENT effect still in ``live.active_effects``
    (``source_effect_id``: the same per-entry signal
    ``_strip_condition_from_combatant`` reads for ``ConditionRemoved``, so the
    two folds agree without a second bookkeeping structure). Merely listing
    the status in another effect's raw ``statuses`` does not count: SRD
    Immunity means a withheld status (Petrified's Poisoned, see
    ``is_condition_immune``) never gets an entry, so an effect that lists a
    status it never landed can't keep a different, now-expiring effect's
    status alive."""
    target_effects = live.active_effects.get(event.target_id, [])
    expired_effect: ActiveEffect | None = None
    for i, eff in enumerate(target_effects):
        if eff.id == event.effect_id and eff.origin == event.origin:
            expired_effect = target_effects.pop(i)
            break
    if expired_effect is not None and expired_effect.statuses:
        combatant = _find_combatant(live, event.target_id)
        remaining_effect_ids = {eff.id for eff in live.active_effects.get(event.target_id, [])}
        surviving_statuses: set[str] = set()
        if combatant is not None:
            new_conditions = [
                ac
                for ac in combatant.conditions
                if ac.condition not in expired_effect.statuses
                or (ac.source_effect_id is not None and ac.source_effect_id in remaining_effect_ids)
            ]
            if len(new_conditions) != len(combatant.conditions):
                for idx, c in enumerate(live.initiative):
                    if c.entity_id == event.target_id:
                        live.initiative[idx] = c.model_copy(update={"conditions": new_conditions})
                        break
            surviving_statuses = {ac.condition for ac in new_conditions}
        # Also clear the status from live.active_conditions — the coarse
        # per-entity name set a host mirrors into its own storage. Without
        # this, a host's mirror re-attaches the expired status to its own
        # state on the next sync. Derived from the same ``surviving_statuses``
        # the typed list above just computed, so the two stores cannot
        # disagree.
        active_cond_set = live.active_conditions.get(event.target_id)
        if active_cond_set is not None:
            for status in expired_effect.statuses:
                if status not in surviving_statuses:
                    active_cond_set.discard(status)
    _end_anchor_dependents(live, event)
    _revert_transform_on_expiry(live, event)


def _maybe_roll_death_save(live: _LiveCombat) -> None:
    """SRD §Dying — when a PC starts their turn at 0 HP, roll a death save.

    Called immediately after a ``TurnStarted`` is emitted for a PC. If the
    active combatant is a Character whose tracked HP is ≤ 0 and who is not
    yet recorded dead, roll one death save via
    ``roll_death_save``, emit the returned events
    through ``_emit`` (so a host receives them), and apply the
    returned ``Combatant`` mutation back into the live initiative slot.

    The death-save state machine in ``death_saves`` owns
    the success/failure counters; this orchestrator helper is the wiring
    that turns its outcome into emitted ``CombatEvent`` instances.
    """
    actor = _current_actor(live)
    if actor.entity_type != "Character":
        return
    if actor.entity_id in live.dead_ids:
        return
    tracked = live.tracked_hp.get(actor.entity_id, actor.hp_current)
    if tracked > 0:
        return
    # SRD §Death Saving Throws — stabilized PCs do not roll further death
    # saves until they take damage or are healed. The death-save helper's
    # docstring declares "not yet stable" a caller-owned precondition.
    if actor.death_saves and actor.death_saves.get("is_stable"):
        return
    # PC is at 0 HP — roll one death save.
    result = roll_death_save(actor, live.rng)
    for ev in result.events:
        _emit(live, ev)
    # Replace the combatant in initiative with the updated copy.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor.entity_id:
            live.initiative[idx] = result.combatant
            break
    # On crit_success (nat-20), HP resets to 1 — sync the tracker so the
    # PC can act on their next turn, and mirror the healing revive's condition
    # events so ``live.active_conditions`` (read by ``views.py`` and by effect
    # expiry) does not keep a stale ``unconscious``. SRD 5.2 Unconscious:
    # "When this condition ends, you remain Prone."
    if result.outcome == "critical_success":
        live.tracked_hp[actor.entity_id] = result.combatant.hp_current
        _emit(live, ConditionRemoved(target_id=actor.entity_id, condition="unconscious"))
        _emit(live, ConditionApplied(target_id=actor.entity_id, condition="prone"))


def _hp_max_for(live: _LiveCombat, entity_id: str) -> int:
    for c in live.initiative:
        if c.entity_id == entity_id:
            return c.hp_max or c.hp_current
    return 0


def _target_kind_for(live: _LiveCombat, entity_id: str) -> Literal["character", "npc", "monster"]:
    for c in live.initiative:
        if c.entity_id == entity_id:
            if c.entity_type == "Character":
                return "character"
            if c.entity_type == "NPC":
                return "npc"
            return "monster"
    return "monster"


def _record_death(live: _LiveCombat, event: Death, *, killer_id: str | None) -> None:
    """Append a DeathRecord and mark the entity dead.

    Killer attribution: the current turn's actor (set by ``TurnStarted``).
    For PC-on-monster damage that's the PC; for monster-on-monster (rare,
    e.g. AOE friendly-fire) it's still the current actor. Synthesized deaths
    from ``DamageApplied`` reuse this path.
    """
    if event.target_id in live.dead_ids:
        return
    live.dead_ids.add(event.target_id)
    live.deaths_recorded.append(
        DeathRecord(
            target_id=event.target_id,
            target_kind=_target_kind_for(live, event.target_id),
            location_id=live.scene_location_id,
            reason=event.reason,
            killer_id=killer_id if killer_id != event.target_id else None,
        )
    )
    # SRD 5.2 §Concentration — "Your Concentration ends if ... you die."
    # Idempotent for non-concentrating dead (empty-chain no-op), and safe
    # inside the _emit fold: the cascade emits no DamageApplied/Death, so
    # it cannot recurse into death synthesis.
    _drop_concentration(live, event.target_id)
    # SRD 5.2 Wild Shape: "...or die"; shape-shifting: "You revert to your true
    # form if you die."
    _end_wild_shape(live, event.target_id, "source_dead")


# ── Sidecar hydration (per-evaluation projection of session state) ──────────
#
# The per-effect handlers read sidecar surfaces off the hydration
# payload — passive damage modifiers, save /
# check modifiers, existing temp-HP, counter pools, narrative text sink,
# spell book, available slots, active concentration, IEffect graph. The
# orchestrator projects from ``_LiveCombat`` (the in-memory combat state)
# and hands the payload to the active-effect projection immediately
# before invoking the evaluator. ``set_sidecar_state`` resets ``_text_sink``
# each call, so the per-evaluation narrative bag is fresh.
#
# Follow-ups (not in scope here):
#   * passive damage / save / check modifiers projection requires reading
#     active effect modifiers, which is async (a host's effect store). Today we
#     project empty dicts; handlers tolerate the absent state by returning
#     defaults (0 modifier, no resistances, no advantage/disadvantage).
#   * spell_book / available_slots / existing_concentration are not yet
#     carried on _LiveCombat. Project empty; spell-cast handler treats
#     empty as ``CastFailed(no_slot)`` — the safe behavior.
#   * counter pools (custom counters + spell-slot pool) likewise not yet
#     on session state. Empty pools mean ``UseCounter`` warns rather than
#     decrements.
#   * ieffect_graph is hydrated empty; the per-evaluation ``triggering_ieffect``
#     payload still flows through ``ctx.variables``.


# Foundry-native attack-bonus change keys (one per attack category). Bless /
# Bane carry all four with an identical signed-dice value; a creature attacks
# in exactly one category at a time, so the projection folds them once into the
# action-agnostic ``passive_to_hit_bonus`` (see the fold below).
# SRD 5.2 Adamantine Armor. Foundry has no native change key for "a Critical
# Hit against you becomes a normal hit", so this is the extension own key,
# carried on the item always-on effect alongside the standard ones.
_CRITICAL_HIT_IMMUNE_KEY: Final = "critical.hit.immune"


_FOUNDRY_ATTACK_BONUS_KEYS = frozenset(
    {
        "system.bonuses.mwak.attack",
        "system.bonuses.msak.attack",
        "system.bonuses.rsak.attack",
        "system.bonuses.rwak.attack",
    }
)

# Foundry-native attack-CATEGORY damage-bonus change keys (Rage's Rage Damage
# rides ``mwak``; 's ranged analog rides ``rwak``; melee/ranged SPELL
# attack damage bonuses ride ``msak``/``rsak``). A creature attacks in exactly
# one category at a time (mirrors the to-hit fold above), so each key folds
# into its OWN category-scoped sidecar (never a shared bucket) — the consumer
# (``attack.py``) gates each on the swing's own melee/ranged + weapon/spell
# shape.
_FOUNDRY_DAMAGE_BONUS_KEY_TO_SIDECAR = {
    "system.bonuses.mwak.damage": "passive_melee_damage_bonus",
    "system.bonuses.rwak.damage": "passive_ranged_damage_bonus",
    "system.bonuses.msak.damage": "passive_melee_spell_damage_bonus",
    "system.bonuses.rsak.damage": "passive_ranged_spell_damage_bonus",
}

# Foundry-native flat/dice bonus to the CASTER's own spell save DC (e.g. a Rod
# of the Pact Keeper). Folds into the save-DC path (item 1's real
# spellcasting-ability formula) via ``build_context.py::_spell_dc_bonus``.
_FOUNDRY_SPELL_DC_BONUS_KEY = "system.bonuses.spell.dc"

# Foundry-native damage-RESISTANCE trait key on an ACTIVE effect (Rage's
# activation-gated ``dr``: bludgeoning/piercing/slashing while raging). Foundry
# ``mode=2`` here means "add this damage-type to the resistance SET", NOT "add to
# a numeric bucket" — so the value is a damage-type STRING, not a signed number.
# Handled at the very top of the change loop, BEFORE the numeric mode guard and
# the signed-string coercion, appending into the ``resistances`` sidecar list
# ``apply.py`` already reads (see docs/dev/passive-projection.md).
_FOUNDRY_RESISTANCE_KEY = "system.traits.dr.value"

# VULNERABILITY on an active effect. SRD 5.2 "Armor of Vulnerability" gives
# resistance to two damage types and VULNERABILITY to the third; Foundry writes
# both on the same effect, ``dv`` for the weakness. Same ``mode=2`` set-
# membership semantics as ``dr`` above, so it takes the same early branch and
# lands in the ``vulnerabilities`` sidecar ``apply.py`` doubles from. Without
# this the armour granted the two resistances and silently dropped the
# weakness -- a character better off than the rules allow.
_FOUNDRY_VULNERABILITY_KEY = "system.traits.dv.value"
# The projection seam rewrites keys to engine form (``build_party._combat_key``),
# so an effect that came through ``project_item_effects`` carries THIS name,
# not the Foundry one. Accept both: the corpus path and the projected path must
# fold the same rule.
_PROJECTED_VULNERABILITY_KEY = "vulnerabilities"

# F1d — the three D20-test bonus buckets that land on the per-actor CHECK sidecar
# (``check_modifiers[id]``) and the per-ability SAVE sidecar
# (``save_modifiers[id]["saves"]``). Both the engine's short internal key form and
# the Foundry-native ``system.bonuses.*`` / ``system.abilities.*`` form are
# accepted, mirroring the ``system.bonuses.abilities.save`` → ``save.bonus``
# aliasing above.
#
# ``abilities.check`` — a bonus to EVERY ability check (Guidance-shaped); it
# reaches skill checks too, because a skill check IS an ability check (SRD 5.2
# §Ability Checks).
_FOUNDRY_CHECK_BONUS_KEYS = frozenset({"abilities.check", "system.bonuses.abilities.check"})
# ``abilities.skill`` — a bonus to skill checks only.
_FOUNDRY_SKILL_BONUS_KEYS = frozenset({"abilities.skill", "system.bonuses.abilities.skill"})
# ``abilities.<ab>.save`` — a bonus to ONE ability's saving throw (the per-ability
# counterpart of the generic ``system.bonuses.abilities.save`` bucket, which stays
# on the action-agnostic ``passive_save_bonus`` dice sidecar).
_FOUNDRY_PER_ABILITY_SAVE_KEYS: dict[str, str] = {
    key: ability
    for ability in ("str", "dex", "con", "int", "wis", "cha")
    for key in (f"abilities.{ability}.save", f"system.abilities.{ability}.bonuses.save")
}


def _flat_change_value(value: bool | int | str) -> int | None:
    """The int a D20-test bonus change contributes, or ``None`` to skip it.

    ``check_modifiers[id]["ability_mods"/"skills"]`` and
    ``save_modifiers[id]["saves"]`` are RESOLVED INTEGER sidecars — the consumers
    (``activities/check.py::resolve_check``, ``activities/save.py``) add them to a
    natural d20 with no dice parser. So unlike the neighbouring
    ``passive_save_bonus`` / ``passive_to_hit_bonus`` sidecars (signed dice
    STRINGS their consumers roll), a dice-valued change on these buckets is
    dropped rather than folded: rolling it here would both mis-type the sidecar
    and consume RNG draws inside a projection that must stay draw-free
    (determinism contract). Plain integer strings ("2", "-1") still fold.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value.strip())
    except ValueError:
        return None


def _fold_d20_test_bonus(
    change: ActiveEffectChange,
    per_target_check: dict[str, Any],
    per_target_entry: dict[str, Any],
) -> None:
    """Fold one ``abilities.check`` / ``abilities.skill`` / ``abilities.<ab>.save``
    ``add`` change into the per-actor check + per-ability save sidecars.

    SRD 5.2 §Ability Checks — a skill check IS an ability check, so an
    ``abilities.check`` bonus reaches both the six ability-check modifiers and
    every proficient skill; ``abilities.skill`` reaches skills only.
    """
    amount = _flat_change_value(change.value)
    if amount is None:
        return
    key = change.key
    if key in _FOUNDRY_CHECK_BONUS_KEYS or key in _FOUNDRY_SKILL_BONUS_KEYS:
        skills = per_target_check.setdefault("skills", {})
        for skill in skills:
            skills[skill] += amount
        if key in _FOUNDRY_CHECK_BONUS_KEYS:
            ability_mods = per_target_check.setdefault("ability_mods", {})
            for ability in ability_mods:
                ability_mods[ability] += amount
        return
    ability = _FOUNDRY_PER_ABILITY_SAVE_KEYS[key]
    saves = per_target_entry.setdefault("saves", {})
    saves[ability] = saves.get(ability, 0) + amount


def _fold_active_effect_changes(
    active: Sequence[ActiveEffect],
    per_target_dmg: dict[str, Any],
    per_target_entry: dict[str, Any],
    per_target_check: dict[str, Any],
) -> bool:
    """Fold each live effect's Foundry-shaped ``changes`` into the per-target
    ``per_target_dmg`` (attack/damage sidecar), ``per_target_entry`` (save/ac
    sidecar) and ``per_target_check`` (ability/skill-check sidecar) dicts in
    place. Returns whether ``per_target_dmg`` was mutated
    (``dmg_dirty``) so the caller knows to re-store it.

    Pure projection over the passed dicts — no ``live`` mutation.

    Effects flagged ``disabled`` are skipped: Foundry keeps a switched-off
    effect on the actor without applying its changes, and the sheet-side
    projection already honours the flag (``passive.transfer and not
    passive.disabled``). Folding them here made a disabled Shield still grant
    its +5 AC to attacks and saves.
    """
    dmg_dirty = False
    for active_effect in _enabled_effects(active):
        # Equipped enchantments and other
        # ActiveEffects carry mechanically-relevant `changes` entries
        # (Foundry-shaped: attack.roll.bonus / damage.bonus /
        # ac.bonus / save.bonus / save.<ability>.bonus). Fold their
        # int-valued mode=add changes into the engine's attack and
        # save sidecar surfaces so the resolvers see them on
        # monster-driven turns. Dice formulas ("1d4") pass through as
        # additive strings — the handler's existing parser already
        # handles them.
        #
        # When an effect carries an
        # ``applicable_action_types`` restriction (e.g. a +1 weapon
        # tagged ["attack"]), the attack/damage sidecar is
        # action-type-agnostic and would silently buff spell
        # attacks too. Filter those buckets here: a weapon-tagged
        # enchantment's attack.roll.bonus / damage.bonus
        # changes don't reach the engine-sidecar path. A host's
        # own dispatch can still apply them
        # correctly for player-dispatched attack actions; this
        # only means a monster-driven attack handler will not
        # see them — which is the conservative outcome because
        # the sidecar can't action-type-disambiguate.
        applicable = active_effect.flags.get("applicable_action_types")
        applicable_set: set[str] | None = None
        if isinstance(applicable, list) and applicable:
            applicable_set = {str(a).lower() for a in applicable}
        # Foundry models a "+1d4 to attack rolls" buff (Bless) /
        # "-1d4" debuff (Bane) as four sibling change keys —
        # ``system.bonuses.{mwak,msak,rsak,rwak}.attack`` — one per
        # attack category. A creature makes exactly one attack at a
        # time (it is melee XOR ranged, weapon XOR spell), so the
        # four siblings are mutually exclusive; folding all four into
        # the action-agnostic ``passive_to_hit_bonus`` would quadruple
        # the modifier. Fold the attack bonus once per effect.
        attack_bonus_folded = False
        for change in active_effect.changes:
            # Rage's ``system.traits.dr.value`` resistance change.
            # Foundry ``mode=2`` on this key means "add to the resistance SET"
            # (value is a damage-type string like ``"bludgeoning"``), not a
            # numeric bonus — so it must bypass BOTH the ``mode != "add"`` guard
            # and the signed-string coercion below. Append the cleaned type into
            # the ``resistances`` sidecar list ``apply.py`` unions with the
            # target's static resistances. Producer-only fix.
            if change.key in (
                _FOUNDRY_RESISTANCE_KEY,
                _FOUNDRY_VULNERABILITY_KEY,
                _PROJECTED_VULNERABILITY_KEY,
            ):
                dmg_type = str(change.value).strip().strip('"').strip()
                if dmg_type:
                    bucket = (
                        "resistances"
                        if change.key == _FOUNDRY_RESISTANCE_KEY
                        else "vulnerabilities"
                    )
                    existing = per_target_dmg.get(bucket)
                    types_list = list(existing) if existing else []
                    if dmg_type not in types_list:
                        types_list.append(dmg_type)
                    per_target_dmg[bucket] = types_list
                    dmg_dirty = True
                continue
            if change.key == _CRITICAL_HIT_IMMUNE_KEY:
                # Adamantine Armor. Checked BEFORE the mode guard above: a
                # "your crit becomes a normal hit" rule is a flag, not an
                # arithmetic bonus, so it never carries mode "add".
                per_target_entry["critical_hit_immune"] = True
                continue
            if change.mode != "add":
                continue
            # F1d — the three D20-test buckets land on INT sidecars, so they are
            # folded before the signed-dice-string coercion below.
            if (
                change.key in _FOUNDRY_CHECK_BONUS_KEYS
                or change.key in _FOUNDRY_SKILL_BONUS_KEYS
                or change.key in _FOUNDRY_PER_ABILITY_SAVE_KEYS
            ):
                _fold_d20_test_bonus(change, per_target_check, per_target_entry)
                continue
            val = change.value
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                signed_str = f"{val:+d}"
            elif isinstance(val, str) and val:
                signed_str = val if val.startswith("-") else f"+{val}"
            else:
                continue
            key = change.key
            # Route attack/damage by action-type tag. Effects
            # tagged ["attack"] (right-hand weapon enchantments)
            # write to a weapon-only sidecar surface
            # (passive_weapon_to_hit_bonus / passive_weapon_damage_bonus);
            # untagged effects (Bless, Bane) write to the
            # broadly-applicable passive_to_hit_bonus /
            # passive_damage_bonus that buff weapon AND spell
            # attacks alike. Defensive buckets (ac/save) ignore
            # the tag — they apply against any attacker.
            weapon_only = applicable_set is not None and "attack" in applicable_set
            # Foundry-native attack-bonus keys (Bless/Bane carry the
            # four ``system.bonuses.{mwak,msak,rsak,rwak}.attack``
            # siblings). Normalize them to the internal
            # ``attack.roll.bonus`` surface, folding once per effect
            # (see ``attack_bonus_folded`` above).
            if key in _FOUNDRY_ATTACK_BONUS_KEYS:
                if attack_bonus_folded:
                    continue
                key = "attack.roll.bonus"
                attack_bonus_folded = True
            elif key in _FOUNDRY_DAMAGE_BONUS_KEY_TO_SIDECAR:
                # Rage's ``system.bonuses.mwak.damage`` (melee weapon attack
                # damage) and its ``rwak``/``msak``/``rsak`` siblings
                # 's ranged-weapon analog + melee/ranged spell-attack
                # damage). Each normalizes into its OWN category-scoped
                # damage-bonus sidecar; attack.py gates each on the swing's
                # own melee/ranged + weapon/spell shape.
                sidecar_field = _FOUNDRY_DAMAGE_BONUS_KEY_TO_SIDECAR[key]
                existing = per_target_dmg.get(sidecar_field)
                per_target_dmg[sidecar_field] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
                dmg_dirty = True
                continue
            elif key == _FOUNDRY_SPELL_DC_BONUS_KEY:
                # A flat/dice bonus to the CASTER's own spell save DC (e.g. a
                # Rod of the Pact Keeper). Folds into the save-DC path
                # (build_context.py::_spell_dc_bonus) on top of the real
                # spellcasting-ability formula from item 1.
                existing = per_target_dmg.get("passive_spell_dc_bonus")
                per_target_dmg["passive_spell_dc_bonus"] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
                dmg_dirty = True
                continue
            elif key == "system.bonuses.abilities.save":
                key = "save.bonus"
            elif key == "system.attributes.ac.bonus":
                # SRD Shield's own change key (Foundry-native path). Alias to
                # the existing "ac.bonus" branch below — not a new branch
                # the branch already existed, the key never matched).
                key = "ac.bonus"
            if key == "attack.roll.bonus":
                field = "passive_weapon_to_hit_bonus" if weapon_only else "passive_to_hit_bonus"
                existing = per_target_dmg.get(field)
                per_target_dmg[field] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
                dmg_dirty = True
            elif key == "save.bonus":
                # project ONLY the
                # generic save.bonus into the action-agnostic
                # sidecar. Per-ability buckets (save.wisdom.bonus,
                # save.dexterity.bonus) would silently leak into
                # every saving throw via passive_save_bonus.
                # combat.saving_throw and resolve_check read
                # per-ability buckets directly from active_effects
                # via apply_changes_to_check so the
                # per-ability path is functional without the
                # sidecar projection.
                existing = per_target_entry.get("passive_save_bonus")
                per_target_entry["passive_save_bonus"] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
            elif key == "ac.bonus":
                existing = per_target_entry.get("passive_ac_bonus")
                per_target_entry["passive_ac_bonus"] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
            elif key == "damage.bonus":
                field = "passive_weapon_damage_bonus" if weapon_only else "passive_damage_bonus"
                existing = per_target_dmg.get(field)
                per_target_dmg[field] = (
                    f"{existing} {signed_str}" if existing else signed_str.lstrip("+")
                )
                dmg_dirty = True
    return dmg_dirty


#: The ability an ITEM activity lends when the hero resolves no spellcasting
#: ability of their own (a fighter with a magic shield). ``use_item`` never
#: sets one, so an item's own ``@mod`` formula would otherwise crash. Only the
#: item's own attack reaches for it: a spell's own DC/attack already come from
#: the item block or a blanket override.
_ITEM_USER_ABILITY: Final = "charisma"

def _project_target_modifiers(
    c: Combatant,
    live: _LiveCombat,
    passive_damage_modifiers: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project one combatant's per-target save/check entries, folding SRD
    conditions, per-creature resistances/immunities, and active-effect changes.

    Mutates ``passive_damage_modifiers[c.entity_id]`` in place (the damage
    projection lands there directly). Returns ``(save_entry, check_entry)``;
    both are always present (F1d — every combatant carries the six ability-check
    modifiers, so the check entry is no longer condition-gated).
    """
    cond_names = [ac.condition for ac in c.conditions]
    damage_proj = project_passive_damage_modifiers(cond_names)
    save_proj = project_passive_save_modifiers(cond_names)
    check_proj = project_passive_check_modifiers(
        cond_names, fear_source_in_sight=_fear_source_in_sight(live, c)
    )
    # Merge per-creature damage_resistances / damage_immunities (from the
    # monster/character stat block) into the condition-derived projection.
    # SRD §Damage Resistance / §Damage Immunity — both sources are
    # additive (resistance + resistance does not stack per SRD, but
    # union-set membership reflects that correctly: the handler only
    # checks set membership, not count).
    if c.damage_resistances:
        merged_res = list(damage_proj.get("resistances", []) or [])
        for dt in c.damage_resistances:
            if dt not in merged_res:
                merged_res.append(dt)
        damage_proj["resistances"] = merged_res
    if c.damage_immunities:
        merged_imm = list(damage_proj.get("immunities", []) or [])
        for dt in c.damage_immunities:
            if dt not in merged_imm:
                merged_imm.append(dt)
        damage_proj["immunities"] = merged_imm
    # fold the creature's static damage vulnerabilities into the same
    # sidecar (the ONLY producer — vulnerability has no condition-derived
    # source). ``apply.py`` reads ``sidecar["vulnerabilities"]`` and doubles a
    # matching hit. Mirrors the resistances/immunities merge above exactly.
    if c.damage_vulnerabilities:
        merged_vuln = list(damage_proj.get("vulnerabilities", []) or [])
        for dt in c.damage_vulnerabilities:
            if dt not in merged_vuln:
                merged_vuln.append(dt)
        damage_proj["vulnerabilities"] = merged_vuln
    if any(damage_proj.values()):
        passive_damage_modifiers[c.entity_id] = dict(damage_proj)
    # Per-target ``saves`` ability-code → modifier projection. SRD 5.2
    # §Saving Throws: ``d20 + ability modifier + proficiency bonus (if
    # proficient in that save)``. All six abilities project through
    # ``actor_stats.save_modifier`` (F1c), which reads the hydrated
    # ability scores, ``save_proficiencies`` and the level/CR-derived
    # proficiency bonus off the ``Combatant``. save.py reads
    # ``entry["saves"][ability]`` on the lower-case ability key.
    per_target_entry: dict[str, Any] = dict(save_proj)
    per_target_entry["saves"] = {ab: save_modifier(c, ab).total for ab in ABILITY_CODES}

    # Active-effect projection: fold each live effect's Foundry-shaped
    # ``changes`` (Bless +1d4 save, Bane −1d4 save, +1 weapon, etc.) into
    # the per-target save_modifiers and attack-side
    # passive_damage_modifiers. The canonical store is
    # ``live.active_effects[entity_id]`` (event-log-derived). The typed
    # change-fold below is the sole source of these passive modifiers;
    # condition-derived save adv/dis comes from the SRD-condition
    # projection (``project_passive_save_modifiers``).
    # Per-actor ability/skill CHECK projection. SRD 5.2 §D20 Tests: an ability
    # check is ``d20 + ability modifier``, plus the proficiency bonus when the
    # actor is proficient in the skill used (doubled with Expertise). All six
    # ability modifiers and every proficient skill project through
    # ``actor_stats.check_modifier`` (F1a); the condition-derived adv/dis lists
    # (``passive_check_adv`` / ``passive_check_dis``) are MERGED, not replaced,
    # and ``disadvantage`` is their resolved boolean for consumers that want the
    # answer rather than the list. ``activities/check.py`` reads
    # ``entry["skills"][slug]`` first, falling back to
    # ``entry["ability_mods"][ability]``.
    check_entry: dict[str, Any] = dict(check_proj)
    check_entry["ability_mods"] = {ab: check_modifier(c, ab).total for ab in ABILITY_CODES}
    skills: dict[str, int] = {}
    for skill in c.skill_proficiencies:
        ability = skill_ability(skill)
        if ability is None:
            # Not an SRD skill slug (a tool proficiency, or a Foundry 3-letter
            # code): no governing ability to project, so the check falls back to
            # the ability modifier rather than guessing.
            continue
        skills[skill] = check_modifier(c, ability, skill).total
    check_entry["skills"] = skills
    check_entry["disadvantage"] = bool(check_proj.get("passive_check_dis"))

    active = live.active_effects.get(c.entity_id, [])
    if active:
        per_target_dmg = passive_damage_modifiers.get(c.entity_id, dict(damage_proj))
        dmg_dirty = _fold_active_effect_changes(
            active, per_target_dmg, per_target_entry, check_entry
        )
        if dmg_dirty:
            passive_damage_modifiers[c.entity_id] = per_target_dmg
        ac_override = _effect_ac_calc_override(c, active)
        if ac_override is not None:
            per_target_entry[_AC_OVERRIDE_KEY] = ac_override
        ac_floor = ac_floor_from_changes(
            change for effect in _enabled_effects(active) for change in effect.changes
        )
        if ac_floor is not None:
            per_target_entry[_AC_FLOOR_KEY] = ac_floor

    return per_target_entry, check_entry


#: ``save_modifiers[id]`` key carrying an active effect's requested Armor
#: Class calculation. Written by ``_project_target_modifiers`` (a LIVE effect
#: can ask for one), read by ``activities/build_context.py``, which reruns the
#: sheet's own ``armor_class`` with that mode and lands the number on
#: ``ActivityResolutionContext.target_ac`` (attack.py prefers it over
#: ``Combatant.ac``).
_AC_OVERRIDE_KEY: Final[str] = "ac_calc_override"

#: ``save_modifiers[id]`` key carrying an active effect's Armor Class FLOOR
#: (SRD 5.2 Barkskin: "the target's Armor Class can't be less than 17"). A
#: floor is neither a bonus nor a mode, so it rides its own key; read by
#: ``activities/build_context.py`` into ``ActivityResolutionContext.target_ac``.
_AC_FLOOR_KEY: Final[str] = "ac_floor"


def _effect_ac_calc_override(c: Combatant, active: Sequence[ActiveEffect]) -> str | None:
    """The alternate Armor Class calculation a creature's live effects ask for.

    SRD 5.2 Mage Armor: "The target's base AC becomes 13 plus its Dexterity
    modifier" — the corpus spells that as ``ac.calc = mage`` with the note
    that "the spell ends early if the target dons armor or ... a Shield". The
    sheet derives the SAME number from the same mode for a feature that is
    always on (``ac_modes_from_changes``); a spell is not always on, so it
    has to be re-derived from the LIVE effect list on every resolution.
    Without this the spell applied an effect nobody read: AC stayed 12
    (measured) and the target never learned the spell had ended by donning
    armor.

    ``None`` when no live effect asks for a mode — the default path, so every
    combat without such an effect is byte-identical to before. A mode the
    creature's worn equipment forbids (``ac_mode_eligible``: armor, or a
    Shield for the Monk's version) is NOT applied and is reported as the
    spell's own ending condition — ``ac_calc_override_refused`` names it
    rather than silently doing nothing.
    """
    from dnd5e_engine.rules.character import ac_calc_overrides, ac_mode_eligible

    requested = ac_calc_overrides(
        change for effect in _enabled_effects(active) for change in effect.changes
    )
    if not requested:
        return None
    wearing_armor = c.worn_armor is not None
    wielding_shield = bool(c.shield_equipped)
    for mode in requested:
        if ac_mode_eligible(mode, wearing_armor=wearing_armor, wielding_shield=wielding_shield):
            return mode
        _LOGGER.warning(
            "ac_calc_override_refused entity_id=%s mode=%s wearing_armor=%s shield=%s",
            c.entity_id,
            mode,
            wearing_armor,
            wielding_shield,
        )
    return None


def _project_caster_pools(
    live: _LiveCombat, caster: Combatant
) -> tuple[dict[Any, dict[str, Any]], dict[str, dict[str, int]], dict[str, Any]]:
    """Project the active caster's spell book, available slots, and counter
    pool. Pure — reads ``live`` and ``caster``, mutates nothing.

    Returns ``(spell_book, available_slots, counter_state)``.
    """
    spell_book: dict[Any, dict[str, Any]] = {}
    available_slots: dict[str, dict[str, int]] = {}
    counter_state: dict[str, Any] = {"custom_counters": {}, "spell_slots": {}}
    # Slots — handler keys by str(level); CharacterSpec carries int keys.
    slots = live.spell_slots_by_entity.get(caster.entity_id, {})
    if slots:
        slot_str_keyed = {str(level): int(count) for level, count in slots.items()}
        available_slots[caster.entity_id] = slot_str_keyed
        counter_state["spell_slots"] = dict(slot_str_keyed)
    # Spell book — resolve slugs through the bundled lib corpus. Unknown
    # slugs are silently dropped (the lib is the source of truth for what
    # can be cast). Post-cutover this maps to typed ``Spell`` instances; the
    # live ``cast``-delegation seam (``_build_cast_spell_book``) keys spells
    # by Foundry uuid off the resolved intent's own activities instead, so
    # this projection is not that seam's source — it is kept as a per-caster
    # known-spells view (narration/UI), separate from delegation.
    spells_known = live.spells_known_by_entity.get(caster.entity_id, [])
    if spells_known:
        lib_loader = get_lib_loader()
        caster_book: dict[str, Any] = {}
        for slug in spells_known:
            spell = lib_loader.get_spell(slug)
            if spell is not None:
                caster_book[slug] = spell
        if caster_book:
            spell_book[caster.entity_id] = caster_book
    # Custom counters — per-caster bag flowed into the single-caster
    # counter_state dict (the handler reads the global accessor).
    counters = live.custom_counters_by_entity.get(caster.entity_id, {})
    if counters:
        counter_state["custom_counters"] = {k: dict(v) for k, v in counters.items()}
    return spell_book, available_slots, counter_state


def _build_hydration_payload(live: _LiveCombat, caster: Combatant | None = None) -> dict[str, Any]:
    """Project the hydration-payload kwargs from live combat state.

    Two projection scopes:

    * **Per-combatant** (keyed by ``entity_id``): ``existing_temp_hp``,
      ``passive_damage_modifiers``, ``save_modifiers``, ``check_modifiers``,
      ``existing_concentration``. Derived from canonical
      ``Combatant`` fields (``temp_hp``,
      ``conditions``, ``concentration_effect_id``) plus the SRD-condition
      projection in ``conditions``.

    * **Per-caster** (single dict for the current evaluator turn): the
      ``_counter_state`` accessor is a single dict the handler reads as
      "the active caster's pool", so we project from ``caster``'s
      ``spell_slots`` and ``custom_counters`` only. ``spell_book`` and
      ``available_slots`` are also per-caster maps (``{caster_id: {...}}``)
      but the spell handler only ever reads the active caster's row, so
      we narrow projection to ``caster`` to avoid loading the SRD asset
      corpus for every combatant.

    Active-effect modifier projection (Bless +1d4, Bane −1d4, +1 weapon,
    etc.) is folded in from each combatant's
    ``active_effects`` row by reading the effects'
    Foundry-shaped ``changes`` directly (the typed change-fold). The
    int/dice ``add`` changes surface on the per-target ``save_modifiers`` /
    ``passive_damage_modifiers`` entries under ``passive_save_bonus`` /
    ``passive_to_hit_bonus`` / ``passive_ac_bonus`` / ``passive_damage_bonus``
    keys. Condition-derived save adv/dis come from the SRD-condition
    projection (``project_passive_save_modifiers`` →
    ``passive_save_adv`` / ``passive_save_dis``).

    ``caster=None`` (e.g. the start-of-combat hydration test path)
    projects per-combatant surfaces only; per-caster pools resolve to
    the canonical empty shape.
    """
    existing_temp_hp: dict[str, int] = {c.entity_id: int(c.temp_hp) for c in live.initiative}

    # ── Per-target passive modifiers (from SRD conditions) ──────────────────
    passive_damage_modifiers: dict[str, dict[str, Any]] = {}
    save_modifiers: dict[str, dict[str, Any]] = {}
    check_modifiers: dict[str, dict[str, Any]] = {}
    for c in live.initiative:
        per_target_entry, check_entry = _project_target_modifiers(c, live, passive_damage_modifiers)
        save_modifiers[c.entity_id] = per_target_entry
        check_modifiers[c.entity_id] = check_entry

    # SRD 5.2 §Actions in Combat — Dodge: "you make Dexterity saving throws
    # with Advantage" while the benefit is active. Folded onto the SAME
    # per-target ``passive_save_adv`` list ``build_context.py`` reshapes
    # into ``ActivityResolutionContext.passive_save_adv`` — no separate
    # sidecar. SRD 5.2 gives this DEX-save half no "can see" conjunct at
    # all — unlike the sibling attack-disadvantage half, whose "if you can
    # see the attacker" conjunct is applied at the ``target_dodging`` ctx
    # sites (C16b, ``_combatant_can_see``), this half stays unconditioned
    # on vision.
    for c in live.initiative:
        if _dodge_benefit_active(live, c):
            save_modifiers[c.entity_id]["passive_save_adv"].append("DEX")

    # ── C12 — SRD 5.2 Exhaustion ``-2 x level`` on every D20 Test, per entity ─
    # Only exhausted entities get a row, so an unexhausted combat hands the
    # resolvers an EMPTY dict and every d20 is byte-identical to before.
    d20_penalty: dict[str, int] = {}
    for c in live.initiative:
        penalty = d20_test_penalty(c.conditions)
        if penalty != 0:
            d20_penalty[c.entity_id] = penalty

    # ── Per-combatant concentration map ─────────────────────────────────────
    # SRD §Concentration — surface ``{effect_name, effect_id}`` per the
    # spell-handler contract (it reads ``effect_name``). The orchestrator
    # does not yet carry effect-name metadata on Combatant; we mirror
    # ``effect_id`` as the name so the single-conc check fires (matching
    # the stub-fixture behavior in ``test_orchestrator_hydration``).
    existing_concentration: dict[str, dict[str, Any]] = {}
    for c in live.initiative:
        if c.concentration_effect_id:
            existing_concentration[c.entity_id] = {
                "effect_id": c.concentration_effect_id,
                "effect_name": c.concentration_effect_id,
            }

    # ── Per-caster spell book + slots + counter pool ───────────────────────
    if caster is not None:
        spell_book, available_slots, counter_state = _project_caster_pools(live, caster)
    else:
        spell_book = {}
        available_slots = {}
        counter_state = {"custom_counters": {}, "spell_slots": {}}

    # IEffect parent/child graph: empty initially; the per-evaluation
    # ``triggering_ieffect`` flows through ``ctx.variables`` for now.
    ieffect_graph: dict[str, Any] = {}

    # C18 §Monster action economy — Legendary Resistance. Fresh
    # COPIES per resolution: ``activities/save_primitive.roll_save`` mutates
    # both dicts in place on a conversion, and ``_sync_legendary_resistance``
    # reads the resulting ``LegendaryResistanceUsed`` events afterward rather
    # than these dicts directly, so mutating a throwaway copy here is safe.
    legendary_resistance_armed = dict(live.legendary_resistance_armed)
    legendary_resistances_remaining_by_entity = {
        c.entity_id: c.legendary_resistances_remaining
        for c in live.initiative
        if MonsterTraitMechanic.LEGENDARY_RESISTANCE in c.trait_mechanics
    }
    return {
        "passive_damage_modifiers": passive_damage_modifiers,
        "save_modifiers": save_modifiers,
        "check_modifiers": check_modifiers,
        "d20_test_penalty": d20_penalty,
        "existing_temp_hp": existing_temp_hp,
        "counter_state": counter_state,
        "spell_book": spell_book,
        "available_slots": available_slots,
        "existing_concentration": existing_concentration,
        "ieffect_graph": ieffect_graph,
        "legendary_resistance_armed": legendary_resistance_armed,
        "legendary_resistances_remaining_by_entity": legendary_resistances_remaining_by_entity,
    }


# ── Areas of effect ─────────────────────────────────────────────────────────

# SRD 5.2 §Areas of Effect — an activity that resolves against creatures and
# carries a measured template (Foundry ``target.template``: Fireball's
# sphere/20, Burning Hands' cone/15) affects the creatures in its area, whatever
# the intent: a spell, a feature (a Breath Weapon) or an item (the Pipes of
# Haunting). ``dnd5e_engine.areas`` holds the pure geometry and selection; this
# section places the area, applies it and reports it (``AreaTargeted``).

# The intents that resolve activities: the ones whose area, or whose
# excluded_target_ids, the gate checks. An attack never places an area (_area_plan).
_AREA_INTENTS: Final[frozenset[IntentType]] = frozenset(
    {"attack", "cast_spell", "use_item", "use_feature"}
)


@dataclass(frozen=True)
class _AreaPlan:
    """How an intent's area activity resolves (``_area_plan``)."""

    activity: Any
    #: ``None`` for a template the engine can't place (a ``wall``, a formula size).
    template: AreaTemplate | None
    #: "up to N creatures" (``areas.creature_count``).
    count: int | None
    #: The creatures a counted area names: ``target_ids``, else a lone
    #: ``target_id`` for a count of one ("a creature you can see").
    named_ids: tuple[str, ...]

    @property
    def places_template(self) -> bool:
        """The engine enumerates the template's cells: a template it can place,
        unless the intent names the creatures a counted area affects."""
        return self.template is not None and not (self.count is not None and self.named_ids)


def _area_plan(intent: PlayerIntent, activities: Sequence[Any]) -> _AreaPlan | None:
    """The intent's area activity and how it resolves, or ``None`` when its
    activities resolve against a named target.

    SRD 5.2 §Making an Attack: an attack roll always targets one creature or
    object, so no intent whose resolved activities include an attack roll
    ever area-expands — an ``attack`` intent itself, or a multi-function item
    or feature (the Rod of Lordly Might's buttons, Javelin of Lightning's
    thrown attack alongside its Lightning Bolt) that bundles one with a
    templated save, damage or heal. A ``use_item`` that resolves more than
    one activity because no ``activity_id`` picked one and none is charged
    is itself ambiguous and never area-expands either (Horn of Blasting's
    Blow Horn alongside its other functions); an item whose area activity is
    chosen via ``activity_id`` or is the charged one still gets its area. A
    feature with several activities never gets here without ``activity_id``
    (``_resolve_feature_invocation``).
    """
    # A stat-block COMMAND naming a save action (a breath weapon) arrives as an
    # ``attack`` intent by wire shape but resolves an area, not an attack roll:
    # it area-expands like the same action on the monster's own turn
    # (``_resolve_monster_parts``). A weapon swing still never does.
    if (intent.intent_type == "attack" and not intent.stat_block_action_id) or any(
        a.kind == "attack" for a in activities
    ):
        return None
    if intent.intent_type == "use_item" and not intent.activity_id and len(activities) > 1:
        return None
    activity = area_activity(activities)
    if activity is None:
        return None
    count = creature_count(activity)
    named: tuple[str, ...] = ()
    if count is not None:
        if intent.target_ids:
            named = tuple(intent.target_ids)
        elif count == 1 and intent.target_id:
            named = (intent.target_id,)
    return _AreaPlan(
        activity=activity, template=area_template(activity), count=count, named_ids=named
    )


def _aoe_direction(
    live: _LiveCombat, caster_id: str, intent: PlayerIntent
) -> tuple[int, int] | None:
    """Aim vector for a directional template: the intent's ``direction``, else
    caster → named target (sign per axis); ``None`` when neither exists."""
    if intent.direction is not None:
        return intent.direction
    caster_cell = live.actor_zone.get(caster_id)
    target_cell = live.actor_zone.get(intent.target_id) if intent.target_id else None
    if caster_cell is None or target_cell is None or caster_cell == target_cell:
        return None
    cc, cr = parse_cell(caster_cell)
    tc, tr = parse_cell(target_cell)
    return ((tc > cc) - (tc < cc), (tr > cr) - (tr < cr))


def _area_origin(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent, template: AreaTemplate
) -> str:
    """The area's SRD 5.2 point of origin: the named target's cell for a
    Sphere or Cylinder (Fireball), else the actor's cell (Burning Hands,
    Thunderwave, an Emanation)."""
    actor_cell = live.actor_zone[actor_id]
    if template.anchor == "target" and intent.target_id:
        return live.actor_zone.get(intent.target_id, actor_cell)
    return actor_cell


def _area_cover_origin(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent, activities: Sequence[Any]
) -> str | None:
    """SRD 5.2 §Cover — "if a target is behind an area of effect's point of
    origin, measure cover from that point": the placed area's origin, or
    ``None`` (the actor's cell) when the intent places no template."""
    plan = _area_plan(intent, activities)
    if plan is None or plan.template is None or not plan.places_template:
        return None
    return _area_origin(live, actor_id, intent, plan.template)


def _area_target_failure(
    live: _LiveCombat,
    current: Combatant,
    actor_id: str,
    intent: PlayerIntent,
    feature_invocation: _FeatureInvocation | None,
) -> CombatEvent | None:
    """``target_invalid`` for an area the intent can't resolve, before anything
    is spent; ``None`` otherwise. One of the ``pre_resolution_gates``.

    - A Cone, Cube or Line with no aim: no ``direction`` and no other named
      creature (SRD 5.2: it extends "in a direction its creator chooses").
    - ``excluded_target_ids`` naming a creature not in the combat, or sent with
      an intent that resolves no area of your choice.
    - A counted area naming more creatures than it affects, one creature
      twice, or a creature not in the combat.
    """
    if intent.intent_type not in _AREA_INTENTS:
        return None
    if intent.intent_type == "attack":
        if intent.excluded_target_ids is not None:
            return AttackFailed(
                actor_id=actor_id, target_id=intent.target_id, reason="target_invalid"
            )
        # A stat-block save command that places a directional template needs an
        # aim: the named target supplies one, a bare command has none.
        if intent.stat_block_action_id:
            activities = _resolve_intent_activities(
                intent,
                None,
                current,
                stat_block_slug=_current_stat_block_slug(live, actor_id),
            ).activities
            plan = _area_plan(intent, activities)
            if (
                plan is not None
                and plan.template is not None
                and plan.places_template
                and plan.template.directional
                and _aoe_direction(live, actor_id, intent) is None
            ):
                return AttackFailed(
                    actor_id=actor_id, target_id=intent.target_id, reason="target_invalid"
                )
        return None
    activities = _resolve_intent_activities(
        intent,
        feature_invocation,
        current,
        stat_block_slug=_current_stat_block_slug(live, actor_id),
    ).activities
    plan = _area_plan(intent, activities)
    in_combat = {c.entity_id for c in live.initiative}
    invalid = (
        (
            plan is not None
            and plan.template is not None
            and plan.places_template
            and plan.template.directional
            and _aoe_direction(live, actor_id, intent) is None
        )
        or (
            intent.excluded_target_ids is not None
            and (
                plan is None
                or not is_choice(plan.activity)
                or not set(intent.excluded_target_ids) <= in_combat
            )
        )
        or (
            plan is not None
            and plan.count is not None
            and (
                len(plan.named_ids) > plan.count
                or len(set(plan.named_ids)) != len(plan.named_ids)
                or not set(plan.named_ids) <= in_combat
            )
        )
    )
    if not invalid:
        return None
    return CastFailed(actor_id=actor_id, spell_id=intent.spell_id or "", reason="target_invalid")


def _activities_bear_effects(activities: Sequence[Any]) -> bool:
    """True iff any activity carries effect riders (``effects[]``).

    A self-buff (Shield, Mirror Image, Disguise Self) hangs its mechanical
    payload as effect riders on a ``UtilityActivity``; with no riders there is
    nothing to apply to the caster and the self-target default is a no-op we
    should skip.
    """
    return any(getattr(a, "effects", None) for a in activities)


def _activity_effects_target_self(activities: Sequence[Any]) -> bool:
    """True iff every activity is a ``utility`` naming no target type.

    An item activation that can only affect the creature using it states no
    target: Boots of Speed's "Click Heels" (``target.affects.type == ""``),
    and the token/potion shapes that affect their user. The payload is an
    effect rider, and the rider can only land on the wearer.

    The three conditions together -- typed ``utility`` kind, no stated target
    type, and the rider/foe-kind guard at the call site -- are what keep this
    from catching a hostile activation: an unaimed wand names a ``cast``
    activity and a potion of poison names a ``save``. Eleven corpus items
    match, every one of them self-applied.
    """

    return all(
        getattr(activity, "kind", None) == "utility"
        and not getattr(getattr(getattr(activity, "target", None), "affects", None), "type", None)
        for activity in activities
    )


def _activities_target_self(activities: Sequence[Any]) -> bool:
    """True iff every activity's ``target.affects.type`` is ``"self"``.

    A class-feature invocation (Rage's self-buff, Second Wind's self-heal) names
    no foe, so the named-target filter yields ``[]`` and the rider/heal would
    apply to nobody. The typed ``target.affects.type == "self"`` is the
    authoritative self-target signal (mirrors the spell self-target default).
    """
    return all(
        getattr(getattr(a.target, "affects", None), "type", None) == "self" for a in activities
    )



def _spell_is_self_or_targetless(cast_spell: Spell | None, named_target_id: str | None) -> bool:
    """True iff a cast resolves onto the caster rather than a named foe.

    Two shapes qualify: a spell whose typed ``range.units`` is ``self``/``touch``
    (Shield, Mirror Image, Disguise Self), OR any cast that named no target and
    is not an AoE (the AoE branch sets its own target list from the template).
    """
    if cast_spell is not None and cast_spell.range.units in (
        SpellRangeUnits.SELF,
        SpellRangeUnits.TOUCH,
    ):
        return True
    return named_target_id is None


def _area_targets(
    live: _LiveCombat, actor: Combatant, intent: PlayerIntent, plan: _AreaPlan
) -> list[Combatant]:
    """The creatures an intent's area affects (SRD 5.2 §Areas of Effect).

    A template the engine can place is placed at its point of origin, aimed,
    trimmed to the cells with line of effect, and reported in an
    ``AreaTargeted``; ``areas.select_affected`` then applies the activity's
    ``affects`` (an "each enemy" type, "of your choice", "up to N"), the
    caster included only where the geometry and the choice say so — Fireball
    still hits the caster in its own radius. A counted area whose creatures
    the intent names affects exactly those. A template the engine can't place
    (a ``wall``, a formula size) affects the named target only.
    """
    alive = [c for c in live.initiative if c.is_alive and c.entity_id not in live.dead_ids]
    excluded = intent.excluded_target_ids or ()
    if plan.template is None:
        _LOGGER.warning(
            "aoe_template_unsupported type=%s size=%r — falling back to the named target",
            plan.activity.target.template.type,
            plan.activity.target.template.size,
        )
        return [c for c in alive if c.entity_id == intent.target_id and c.entity_id not in excluded]
    by_id = {c.entity_id: c for c in alive}
    if not plan.places_template:
        return [by_id[i] for i in plan.named_ids if i in by_id and i not in excluded]
    template = plan.template
    origin = _area_origin(live, actor.entity_id, intent, template)
    # The ``_area_target_failure`` gate already refused an unaimed Cone, Cube
    # or Line before anything was spent.
    direction = _aoe_direction(live, actor.entity_id, intent) if template.directional else None
    cells = area_cells(live.topology, template, origin, direction)
    in_area = [c.entity_id for c in alive if live.actor_zone.get(c.entity_id) in cells]
    allies = _allied_ids(live, actor.entity_id)
    selection = select_affected(
        in_area,
        affects_type=plan.activity.target.affects.type,
        choice=is_choice(plan.activity),
        count=plan.count,
        harmful=is_harmful([plan.activity]),
        excluded_ids=intent.excluded_target_ids,
        is_enemy=lambda other: _is_enemy(live, actor.entity_id, other),
        is_ally=allies.__contains__,
    )
    _emit(
        live,
        AreaTargeted(
            actor_id=actor.entity_id,
            source_id=_area_source_id(intent),
            shape=template.shape,
            size_ft=template.size_ft,
            origin=origin,
            direction=direction,
            affected_ids=list(selection.affected_ids),
            excluded_ids=list(selection.spared_ids),
        ),
    )
    return [by_id[i] for i in selection.affected_ids]


def _area_source_id(intent: PlayerIntent) -> str:
    """The slug of what makes the area: the spell, item or feature the intent
    names."""
    source = {
        "cast_spell": intent.spell_id,
        "use_item": intent.item_id,
        "use_feature": intent.feature_id,
        "attack": intent.stat_block_action_id,
    }
    return source.get(intent.intent_type) or ""


# ── Concentration writeback ─────────────────────────────────────────────────


def _writeback_concentration(live: _LiveCombat, caster: Combatant, pre_event_count: int) -> None:
    """Project post-evaluation concentration events onto the caster's Combatant.

    Per SRD §Concentration the caster carries at most one concentration
    effect at a time. When the evaluator emits ``EffectApplied(is_concentration=True)``
    during this turn, the caster's ``concentration_effect_id`` must be
    updated so the next ``_build_hydration_payload`` projects the existing
    concentration onto the sidecar (the ieffect2 handler's single-conc
    rule reads it). Symmetric: when ``ConcentrationDropped`` or an
    ``EffectExpired`` for the caster's tracked effect fires, clear it.

    ``EffectApplied`` does not carry the caster's id; we rely on the
    canonical pairing — the *current actor* (active caster on this turn)
    is the one who emits the concentration spell. Events on prior turns
    are ignored via ``pre_event_count`` slicing.
    """
    new_events = live.event_log[pre_event_count:]
    tracked_name: str | None = None
    for ev in new_events:
        if isinstance(ev, EffectApplied) and ev.effect.flags.get("concentration"):
            # The active caster is the one who initiated this concentration
            # spell. ``EffectApplied`` carries ``target_id`` on the
            # embedded ActiveEffect (the affected combatant) but not the
            # source caster — pairing by "active turn" is the canonical
            # projection here.
            tracked_name = ev.effect.id
        elif isinstance(ev, ConcentrationDropped) and ev.target_id == caster.entity_id:
            tracked_name = None
    # Find caster's index in initiative and update in place. ``Combatant``
    # is a pydantic model; ``model_copy`` returns a new instance.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == caster.entity_id:
            if tracked_name is not None and c.concentration_effect_id != tracked_name:
                live.initiative[idx] = c.model_copy(
                    update={"concentration_effect_id": tracked_name}
                )
            elif tracked_name is None and c.concentration_effect_id is not None:
                # Only clear if a ConcentrationDropped was observed this turn —
                # not when no conc events fired at all (the absent EffectApplied
                # case is "nothing happened", not "concentration dropped").
                cleared = any(
                    isinstance(ev, ConcentrationDropped) and ev.target_id == caster.entity_id
                    for ev in new_events
                )
                if cleared:
                    live.initiative[idx] = c.model_copy(update={"concentration_effect_id": None})
            break


# ── Persistent IEffect-graph lifecycle linkage ──────────────────────────────


def _record_effect_lifecycle_links(
    live: _LiveCombat,
    caster: Combatant,
    pre_event_count: int,
    *,
    concentration_max_rounds: int | None = None,
) -> None:
    """Project this turn's effect-application events into persistent lifecycle state.

    The ieffect2 handler's ``ctx.parent_chain`` is per-evaluation; the
    cross-turn cascade walks (concentration drop → expire dependent
    effects → remove sourced conditions) need a persistent index. We
    walk the slice of ``live.event_log`` produced by this evaluator
    call and fold three pieces of structure into ``_LiveCombat``:

    * ``concentration_chain[caster_id][effect_name] = [target_ids]`` —
      every ``EffectApplied(is_concentration=True)`` emitted while
      ``caster`` was the active actor is owned by that caster. The
      damage-driven concentration save in ``_emit`` consults this map
      to decide whether to roll a save; ``_drop_concentration`` walks
      it to cascade EffectExpired across every target.

    * ``conditions_by_effect[(target_id, effect_name)] = [conditions]``
      — every ``ConditionApplied`` that lands on a target within the
      same evaluator call as an ``EffectApplied`` for that target is
      attributed to that effect. SRD §Hold Person installs the
      paralyzed condition as a structured passive on the ieffect2 node,
      so the canonical pairing in the event stream is *(EffectApplied,
      ConditionApplied)* on the same target inside the save's fail
      branch. The orchestrator does not need IR-level parent links to
      observe this — the emit order is the load-bearing signal.

    * ``repeat_save_on_turn_end[target_id]`` — when a save just failed
      against the same target inside the same evaluator call and a
      condition was then applied with a concurrent
      ``EffectApplied(is_concentration=True)``, the target rolls a
      repeat save at the end of each of its turns (SRD §Hold Person:
      *"At the end of each of its turns, the target repeats the save,
      ending the spell on itself on a success."*). We record the
      ability + DC from the original failed save so the end-of-turn
      hook can re-roll without re-parsing the IR.

    Linkage scope: only events emitted by THIS evaluator call (the
    ``pre_event_count`` slice). Events from prior turns have already
    been folded in; re-walking them would double-count.
    """
    new_events = live.event_log[pre_event_count:]
    # SRD 5.2 §Concentration — "You lose Concentration on an effect the
    # moment you start casting a spell that requires Concentration."
    # (Foundry mixin.mjs:470-476 ends the oldest effect before beginning
    # the new one; attributes.concentration.limit is 1 for every SRD
    # creature.) When THIS resolution applied a new concentration effect
    # and the caster already had a chain from a PRIOR resolution, cascade
    # the old drop first. Identities applied within this same slice (one
    # multi-target cast) are never dropped against each other, and a
    # same-spell/same-target recast (identical identity tuple) is a
    # silent refresh rather than a drop-and-reapply.
    new_conc_identities = [
        (ev.effect.target_id, ev.effect.id, ev.effect.origin)
        for ev in new_events
        if isinstance(ev, EffectApplied) and ev.effect.flags.get("concentration")
    ]
    if new_conc_identities:
        prior = [
            entry
            for entry in live.concentration_chain.get(caster.entity_id, [])
            if entry not in new_conc_identities
        ]
        if prior:
            live.concentration_chain[caster.entity_id] = prior
            _drop_concentration(live, caster.entity_id)
        if concentration_max_rounds is not None:
            live.concentration_rounds_remaining[caster.entity_id] = concentration_max_rounds
        else:
            live.concentration_rounds_remaining.pop(caster.entity_id, None)
    # Per-target tracking within this slice.
    last_failed_save_by_target: dict[str, SaveRolled] = {}
    last_effect_by_target: dict[str, ActiveEffect] = {}
    for ev in new_events:
        if isinstance(ev, SaveRolled) and not ev.succeeded:
            last_failed_save_by_target[ev.target_id] = ev
            continue
        if isinstance(ev, EffectApplied):
            applied = ev.effect
            if applied.flags.get("concentration"):
                chain = live.concentration_chain.setdefault(caster.entity_id, [])
                identity = (applied.target_id, applied.id, applied.origin)
                if identity not in chain:
                    chain.append(identity)
            last_effect_by_target[applied.target_id] = applied
            continue
        if isinstance(ev, ConditionApplied):
            eff = last_effect_by_target.get(ev.target_id)
            if eff is None:
                continue
            key = (ev.target_id, eff.id, eff.origin)
            live.conditions_by_effect.setdefault(key, []).append(ev.condition)
            failed_save = last_failed_save_by_target.get(ev.target_id)
            # Repeat-save lineage requires:
            #   - a same-evaluation failed save on this target (the
            #     spell's gating save, which the SRD repeat-save flow
            #     mirrors at end-of-turn),
            #   - the concurrent EffectApplied is a concentration effect
            #     (SRD §Hold Person / §Hold Monster / §Dominate Person are
            #     all concentration spells with the repeat-save clause).
            # Non-concentration condition applies (e.g. ghoul claw →
            # paralyzed, which is SRD instantaneous and has no repeat-save
            # mechanic) are skipped.
            if failed_save is not None and eff.flags.get("concentration"):
                live.repeat_save_on_turn_end.setdefault(key, []).append(
                    {
                        "ability": failed_save.ability,
                        "dc": failed_save.dc,
                        "effect_name": eff.name,
                        "condition": ev.condition,
                        "caster_id": caster.entity_id,
                    }
                )
            continue
    # _writeback_concentration ran before this fold and pointed
    # concentration_effect_id at the new effect; _drop_concentration's
    # inline clear (correct for every other drop path) wiped it. Restore.
    if new_conc_identities:
        new_effect_id = new_conc_identities[-1][1]
        for idx, c in enumerate(live.initiative):
            if c.entity_id == caster.entity_id:
                if c.concentration_effect_id != new_effect_id:
                    live.initiative[idx] = c.model_copy(
                        update={"concentration_effect_id": new_effect_id}
                    )
                break


#: ``ActiveEffect.flags`` key marking a concentration anchor: the caster-side
#: effect a concentration spell holds when its resolution applied no
#: concentration effect of its own.
_ANCHOR_FLAG: Final = "concentration_anchor"


def _anchor_identity(spell_slug: str, caster_id: str) -> tuple[str, str, str]:
    """``caster_id``'s anchor for ``spell_slug`` as a ``concentration_chain``
    entry, ``(target_id, effect_id, origin)``: the caster holds it."""
    return (caster_id, f"effect:{spell_slug}", f"cast:{spell_slug}:{caster_id}")


def _apply_concentration_anchor(
    live: _LiveCombat, caster: Combatant, spell: Spell | None, pre_event_count: int
) -> None:
    """Anchor a concentration spell whose resolution applied no concentration
    effect (a summon, a construct, a save every target passed) on its caster.

    Concentration begins only from an applied concentration effect, yet SRD 5.2
    holds for every such spell: "You lose Concentration on an effect the moment
    you start casting a spell that requires Concentration". The anchor carries
    no changes and no duration of its own, so C13 governs it like any other
    concentration effect: one at a time, the damage CON save, Incapacitated and
    death, the spell's maximum duration and the drop intent. C13 keeps an
    identical chain entry across resolutions, so a recast of the same spell
    first ends the running one — otherwise its dependents would outlive it."""
    if spell is None or not spell.concentration:
        return
    if any(
        isinstance(ev, EffectApplied) and ev.effect.flags.get("concentration")
        for ev in live.event_log[pre_event_count:]
    ):
        return
    identity = _anchor_identity(spell.slug, caster.entity_id)
    if identity in live.concentration_chain.get(caster.entity_id, []):
        _drop_concentration(live, caster.entity_id)
    target_id, effect_id, origin = identity
    _emit(
        live,
        EffectApplied(
            effect=ActiveEffect(
                id=effect_id,
                name=spell.name,
                origin=origin,
                target_id=target_id,
                flags={"concentration": True, _ANCHOR_FLAG: True},
            )
        ),
    )


def _fold_resolution_outcome(
    live: _LiveCombat,
    caster: Combatant,
    *,
    spell: Spell | None,
    actx: ActivityResolutionContext | None,
    pre_event_count: int,
    concentration_max_rounds: int | None = None,
) -> None:
    """Fold one resolution into live state, the same way at every cast site
    (on-turn, monster, readied). The anchor comes first so C13's writeback and
    lifecycle links see it like any concentration effect: they record the
    chain and end a different concentration the caster held before this
    resolution. ``actx`` is the resolution's context, ``None`` when nothing
    resolved."""
    _apply_transform_requests(live, caster, actx)
    _apply_transform_riders(live, caster, actx, pre_event_count)
    _end_superseded_enchantments(live, caster, actx, pre_event_count)
    _apply_concentration_anchor(live, caster, spell, pre_event_count)
    _apply_construct_requests(live, caster, actx)
    _apply_summon_requests(live, caster, actx)
    _report_save_without_consequence(live, caster, spell, actx, pre_event_count)
    _writeback_concentration(live, caster, pre_event_count)
    _record_effect_lifecycle_links(
        live, caster, pre_event_count, concentration_max_rounds=concentration_max_rounds
    )


def _hook_run_end_of_turn_saves(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — adapt ``_run_end_of_turn_saves`` to ``TurnHook``."""
    if actor_id is None:
        return
    _run_end_of_turn_saves(live, actor_id)


def _hook_tick_durations(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — adapt ``_tick_durations_at_turn_end`` to ``TurnHook``."""
    if actor_id is None:
        return
    _tick_durations_at_turn_end(live, actor_id)


def _hook_expire_timed_effects(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — adapt ``_expire_timed_effects_at_turn_end`` to ``TurnHook``."""
    if actor_id is None:
        return
    _expire_timed_effects_at_turn_end(live, actor_id)


def _hook_concentration_expiry(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — SRD 5.2 §Concentration maximum duration.

    Decrements the caster-keyed rounds counter at the CASTER's own turn end
    and cascades the drop (reason="duration") at zero. The cast turn's own
    turn end counts as round 1 of the duration. No RNG."""
    if actor_id is None:
        return
    remaining = live.concentration_rounds_remaining.get(actor_id)
    if remaining is None:
        return
    if remaining > 1:
        live.concentration_rounds_remaining[actor_id] = remaining - 1
        return
    _drop_concentration(live, actor_id, reason="duration")


def _hook_expire_vex_grants(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — SRD 5.2 §Weapon Mastery / Vex:
    "before the end of your NEXT turn". Decrements every rounds-remaining
    counter under the ENDING actor's own ``live.vex_grants`` entry (this
    actor is the GRANTOR, never the grantee); a counter reaching 0 drops —
    the grant fired the moment its holder made ITS next attack roll against
    that target (``_pop_vex_grants``) before this can ever be reached in
    the common case, so this hook is chiefly the "never attacked again"
    lapse path. No RNG."""
    if actor_id is None:
        return
    grants = live.vex_grants.get(actor_id)
    if not grants:
        return
    for target_id in list(grants):
        grants[target_id] -= 1
        if grants[target_id] <= 0:
            del grants[target_id]
    if not grants:
        del live.vex_grants[actor_id]


_RAGE_FEATURE: Final = "rage"
_PERSISTENT_RAGE_FEATURE: Final = "persistent-rage"
# The id ``passive_effect_to_active_effect`` gives the corpus effect named
# "Rage"; a Rage a host seeds through ``start_combat`` carries it too.
_RAGE_EFFECT_ID: Final = "effect:rage"

_WILD_SHAPE_FEATURE: Final = "wild-shape"
# The effect a Wild Shape form rides (C21): not concentration; it ends through
# ``_end_wild_shape``.
_WILD_SHAPE_EFFECT_ID: Final = "effect:wild-shape"


def _rage_effect(live: _LiveCombat, entity_id: str) -> ActiveEffect | None:
    """``entity_id``'s live Rage, or ``None`` when it isn't raging."""
    return _live_effect(live, entity_id, _RAGE_EFFECT_ID)


def _end_rage(live: _LiveCombat, entity_id: str, reason: EffectExpiryReason) -> None:
    """End ``entity_id``'s Rage, if it has one."""
    rage = _rage_effect(live, entity_id)
    if rage is not None:
        _emit(
            live,
            EffectExpired(
                effect_id=rage.id, target_id=entity_id, origin=rage.origin, reason=reason
            ),
        )


def _has_persistent_rage(live: _LiveCombat, entity_id: str) -> bool:
    """SRD 5.2 Persistent Rage (Barbarian 15): "your Rage is so fierce that it
    now lasts for 10 minutes without you needing to do anything to extend it
    from round to round." """
    c = _find_combatant(live, entity_id)
    return c is not None and _PERSISTENT_RAGE_FEATURE in _granted_feature_slugs(c)


def _end_rage_on_incapacitation(live: _LiveCombat, entity_id: str, condition: str) -> None:
    """SRD 5.2 Rage: "it ends early if you don Heavy armor or have the
    Incapacitated condition." ``condition`` is Incapacitated or implies it.
    Persistent Rage: "Your Rage ends early if you have the Unconscious
    condition (not just the Incapacitated condition)"."""
    if _rage_effect(live, entity_id) is None:
        return
    unconscious = is_condition_active(Condition.UNCONSCIOUS, [condition])
    if not unconscious and _has_persistent_rage(live, entity_id):
        return
    _end_rage(live, entity_id, "incapacitated")


#: SRD 5.2 Barbarian 11 "Relentless Rage" — the feature whose trigger is the
#: fall to 0 Hit Points, handled in ``_emit_apply_damage``.
_RELENTLESS_RAGE_FEATURE: Final = "relentless-rage"


def _relentless_rage_hold(
    live: _LiveCombat, entity_id: str, *, hp_before: int, damage: int
) -> int:
    """SRD 5.2 Relentless Rage: the Hit Points a raging barbarian holds at
    after dropping to 0, or ``0`` when the feature does not fire.

    Fires only for a creature that actually FELL this hit: SRD 5.2 "If you
    drop to 0 Hit Points while your Rage is active and don't die outright".
    Two gates follow the printed wording, both measured against the state
    BEFORE the damage (the critic of r177 caught their absence):

    * ``hp_before <= 0`` — damage taken AT 0 Hit Points is a Death Saving
      Throw failure (SRD "Damage at 0 Hit Points"), never a second hold; the
      feature would otherwise heal a downed barbarian back up on every hit.
    * Massive Damage — "the character dies if the remainder equals or
      exceeds their Hit Point maximum", and a creature that dies outright
      cannot hold on.

    Then it fires only for a RAGING character the class grants ``relentless-rage``,
    with uses left in its own rest-recharged pool (``uses.max`` 20, spent
    per attempt). DC is ``10 + 5 x uses already spent`` — the printed "each
    time you use this feature after the first, the DC increases by 5", and
    the sidecar counter is cleared by a Short/Long Rest, which is the
    printed reset. The attempt is automatic: the engine has no "decline a
    lifesaving save" intent, and holding on is never worse for the player.

    On success the Hit Points become ``2 x Barbarian level`` (the corpus
    activity's ``@classes.barbarian.levels * 2``), NOT a flat 1. Emits
    ``SaveRolled`` either way so a host can narrate the attempt.
    """
    if hp_before <= 0:
        return 0
    if damage - hp_before >= _hp_max_for(live, entity_id):
        return 0
    c = _find_combatant(live, entity_id)
    if c is None or c.entity_type != "Character":
        return 0
    if _RELENTLESS_RAGE_FEATURE not in _granted_feature_slugs(c):
        return 0
    if _rage_effect(live, entity_id) is None:
        return 0
    spent = _feature_use_spent(live, entity_id, _RELENTLESS_RAGE_FEATURE)
    feature = get_lib_loader().get_feature(_RELENTLESS_RAGE_FEATURE)
    cap = (
        _feature_use_cap(feature, _uses_roll_data(c, _scale_values_of(c)))
        if feature is not None
        else None
    )
    if cap is not None and spent >= cap:
        return 0
    dc = 10 + 5 * spent
    modifier = save_modifier(c, "con").total + d20_test_penalty(c.conditions)
    roll = roll_d20_test(live.rng, modifier, AdvantageSources())
    succeeded = roll.total >= dc
    _emit(
        live,
        SaveRolled(
            target_id=entity_id,
            ability="con",
            dc=dc,
            roll_total=roll.total,
            succeeded=succeeded,
            advantage=roll.mode,
            natural=roll.kept,
            modifier=roll.modifier,
            sources=list(roll.sources),
        ),
    )
    _increment_feature_use(live, entity_id, _RELENTLESS_RAGE_FEATURE)
    if not succeeded:
        return 0
    level = _class_levels(c).get("barbarian") or c.character_level
    return 2 * level


def _extends_rage(live: _LiveCombat, actor_id: str, event: CombatEvent) -> bool:
    """One of SRD 5.2 Rage's roll extensions: "Make an attack roll against an
    enemy. Force an enemy to make a saving throw." ``SaveRolled`` names no
    source, so an enemy's save rolled during the barbarian's own turn counts as
    one it forced. An enemy's ``ConcentrationCheck`` does not: the attack or
    save that dealt the damage already extends Rage."""
    if isinstance(event, AttackRolled):
        return event.attacker_id == actor_id and _is_enemy(live, actor_id, event.target_id)
    return isinstance(event, SaveRolled) and _is_enemy(live, actor_id, event.target_id)


def _hook_rage_extension(live: _LiveCombat, actor_id: str | None) -> None:
    """``turn_end`` hook — SRD 5.2 Rage: "The Rage lasts until the end of your
    next turn ... Each time the Rage is extended, it lasts until the end of
    your next turn." At the barbarian's own turn end its Rage survives when
    this turn entered it or extended it — the Bonus Action, or a roll
    ``_extends_rage`` accepts — and otherwise ends with
    ``reason="not_extended"``. A Rage seeded through ``start_combat`` wasn't
    entered this turn, so its first turn must extend it. A Persistent Rage
    (Barbarian 15) needs no extension (``_has_persistent_rage``). No RNG."""
    if actor_id is None:
        return
    extended_by_bonus_action = actor_id in live.rage_bonus_extensions
    live.rage_bonus_extensions.discard(actor_id)
    rage = _rage_effect(live, actor_id)
    if (
        rage is None
        or extended_by_bonus_action
        or _effect_applied_during_current_turn(live, actor_id, (actor_id, rage.id, rage.origin))
        or any(_extends_rage(live, actor_id, ev) for ev in _current_turn_events(live, actor_id))
        or _has_persistent_rage(live, actor_id)
    ):
        return
    _end_rage(live, actor_id, "not_extended")


def _register_default_turn_hooks(live: _LiveCombat) -> None:
    """Register the engine's built-in turn-boundary hooks on ``live``.

    Registration order IS execution order. The two F3a hooks (duration tick,
    reaction-effect expiry) reproduce exactly where each used to run inline, so
    seeded replays are byte-identical apart from the new ``TurnPhase`` markers.
    F3b's ``engine:timed-effect-expiry`` is appended AFTER the round tick, so an
    effect carrying both a ``rounds`` counter and a ``seconds`` duration is
    resolved by the ``rounds`` counter first and the seconds branch then sees
    ``rounds is not None`` and stands down ("rounds wins" — see
    ``_expire_timed_effects_at_turn_end``).
    ``engine:concentration-expiry`` (C13) is appended LAST of the ``turn_end``
    hooks, after ``engine:timed-effect-expiry``, so a same-boundary repeat save
    (``engine:repeat-save``, registered first) still rolls against a live
    effect before the concentration cap can cascade its drop.
    ``engine:rage-extension`` (C20) is appended LAST among the ``turn_end``
    hooks: it reads only the ending turn's own events and Bonus-Action mark,
    and it runs after the ``rounds`` tick, so the corpus Rage's ``rounds: 10``
    stays the outer cap.
    ``engine:repeat-save`` is registered FIRST among the ``turn_end`` hooks: the
    SRD repeat save (Hold Person / Hold Monster / Dominate Person) must resolve
    while its source effect is still live, so it runs before
    ``engine:duration-tick`` could expire that effect on the same boundary.
    Before F3a-follow-up this ran as a hand-placed call at two sites *above* the
    ``TurnPhase(turn_end)`` marker, which also let a bonus action trigger a
    second repeat save in the same turn; as a hook it runs exactly once per turn
    end, inside the phase it belongs to.
    Monster-side "start of turn" mechanics (recharge rolls, regeneration,
    legendary-action reset) do NOT append here — they run once per driven
    turn from ``_run_monster_turn_start``, called at the top of
    ``advance_monster_turn`` BEFORE the ``TurnStarted`` a host's legendary-
    action window is driven from. Later ongoing-damage-style clusters that
    genuinely fire on the engine's own ``TurnStarted``/``TurnEnded`` boundary
    append here rather than editing the advance path.
    """
    live.lifecycle.register("turn_end", _hook_run_end_of_turn_saves, key="engine:repeat-save")
    live.lifecycle.register("turn_end", _hook_tick_durations, key="engine:duration-tick")
    live.lifecycle.register(
        "turn_end", _hook_expire_timed_effects, key="engine:timed-effect-expiry"
    )
    live.lifecycle.register(
        "turn_end", _hook_concentration_expiry, key="engine:concentration-expiry"
    )
    live.lifecycle.register("turn_end", _hook_expire_vex_grants, key="engine:vex-expiry")
    live.lifecycle.register("turn_end", _hook_rage_extension, key="engine:rage-extension")
    live.lifecycle.register(
        "turn_start", _hook_expire_reaction_effects, key="engine:reaction-effect-expiry"
    )


def _tick_durations_at_turn_end(live: _LiveCombat, actor_id: str) -> None:
    """Decrement ``ActiveEffect.duration.rounds`` on the actor's owned
    maintained effects at turn-end and emit ``EffectExpired``
    (reason=duration) when a count reaches zero.

    SRD §Combat: spells with a "rounds" duration count down once per
    round, at the **caster's** turn-end. Bless cast on three allies
    should still last 10 rounds total — not 10/3 ≈ 3 — so the tick is
    keyed to the caster, not each affected target. Effects without an
    active rounds counter (``rounds is None`` — permanent / item-
    equipped / non-rounds duration) skip the tick.

    Caster identification: effect ``origin`` follows the convention
    ``"cast:<slug>:<caster_id>"`` for spells; ``"item:<item_id>:<id>"``
    for equipped items. Only the cast-origin effects tick here, only
    when their caster's turn ends.

    For non-cast-origin effects with rounds (e.g. environmental traps
    or a future seed pattern that doesn't carry a caster), fall back to
    the target-turn-end tick so they still expire eventually.

    Pure on ``live.active_effects``: ``_emit`` consumes the
    ``EffectExpired`` we emit and removes the effect from the
    registry. We snapshot identities first so in-loop emissions don't
    invalidate the iteration.
    """
    to_expire: list[tuple[str, str, str]] = []
    # Walk every (target_id, effect) pair. Tick when the caster (parsed
    # from origin) matches the actor whose turn ended; for orphan
    # origins (no "cast:" prefix), tick at the target's turn-end as the
    # fallback.
    for target_id, target_effects in list(live.active_effects.items()):
        for idx, eff in enumerate(list(target_effects)):
            if eff.duration.rounds is None:
                continue
            # Concentration-gated effects (Bless, Bane, Faerie Fire, Hold
            # Person, Hunter's Mark) live and die with the caster's
            # concentration, not a round counter — SRD §Concentration. The
            # Foundry packs carry a ``duration.rounds`` purely for the
            # turn-tracker display (and some packs ship a too-short value,
            # e.g. Bane's ``rounds: 1``), so ticking it would expire the
            # spell prematurely. The concentration cascade (_drop_concentration
            # on a failed CON save / caster death) and the per-turn repeat
            # save own these effects' lifetimes.
            if eff.flags.get("concentration"):
                continue
            # Caster-keyed, with a target-keyed fallback for item /
            # environment / non-spell origins so they still expire (see
            # ``_duration_tick_matches_actor`` — F3b extracted this predicate
            # verbatim so the seconds branch keys off the same owner).
            if not _duration_tick_matches_actor(
                origin=eff.origin or "", target_id=target_id, actor_id=actor_id
            ):
                continue
            new_rounds = eff.duration.rounds - 1
            if new_rounds > 0:
                # Immutable replacement: previously-emitted EffectApplied
                # events hold references to the same ActiveEffect instance,
                # so in-place mutation would silently mutate captured event
                # payloads. Replace the slot in active_effects with a fresh
                # copy via model_copy and leave the original intact.
                new_duration = eff.duration.model_copy(update={"rounds": new_rounds})
                new_eff = eff.model_copy(update={"duration": new_duration})
                target_effects[idx] = new_eff
                continue
            to_expire.append((target_id, eff.id, eff.origin))

    for target_id, effect_id, origin in to_expire:
        _emit(
            live,
            EffectExpired(
                effect_id=effect_id,
                target_id=target_id,
                origin=origin,
                reason="duration",
            ),
        )


#: SRD 5.2 §Duration — "a round represents about 6 seconds in the game world".
#: The conversion factor for ``ActiveEffectDuration.seconds`` -> rounds.
_SECONDS_PER_ROUND = 6


def _concentration_max_rounds(spell: Spell | None) -> int | None:
    """SRD 5.2 §Concentration maximum duration, in rounds (6 s each, SRD
    §Duration). ``None`` = no engine-tracked cap: non-concentration spells,
    the monster/no-spell paths, and non-metric durations (instantaneous /
    until-dispelled / until-destroyed / permanent / special)."""
    if spell is None or not spell.concentration:
        return None
    duration = spell.duration
    if duration.value is None:
        return None
    per_unit = {
        SpellDurationUnits.ROUND: 1,
        SpellDurationUnits.MINUTE: 10,
        SpellDurationUnits.HOUR: 600,
        SpellDurationUnits.DAY: 14400,
    }.get(duration.units)
    if per_unit is None:
        return None
    return duration.value * per_unit


def _duration_tick_matches_actor(*, origin: str, target_id: str, actor_id: str) -> bool:
    """Whether a round-scoped duration ticks now, at ``actor_id``'s turn end.

    Effect ``origin`` follows ``"cast:<slug>:<caster_id>"`` for spells and
    ``"item:<item_id>:<id>"`` for equipped items. Cast-origin effects tick at
    the CASTER's turn end (SRD §Combat: Bless on three allies still lasts ten
    rounds, not ten thirds). Everything else — item, environment, or a seeded
    effect with no caster in its origin — falls back to the TARGET's turn end
    so it still expires eventually.
    """
    if origin.startswith("cast:"):
        parts = origin.split(":", 2)
        caster_id = parts[2] if len(parts) == 3 else ""
        return caster_id == actor_id
    return target_id == actor_id


def _current_turn_events(live: _LiveCombat, actor_id: str) -> list[CombatEvent]:
    """The events since ``actor_id``'s most recent ``TurnStarted`` — from a
    ``turn_end`` hook, the turn now ending. Empty before its first turn."""
    for i in range(len(live.event_log) - 1, -1, -1):
        ev = live.event_log[i]
        if isinstance(ev, TurnStarted) and ev.actor_id == actor_id:
            return live.event_log[i + 1 :]
    return []


def _effect_applied_during_current_turn(
    live: _LiveCombat, actor_id: str, identity: tuple[str, str, str]
) -> bool:
    """Was ``identity`` applied during the turn of ``actor_id`` that is ending?

    Read-only over ``live.event_log``: find the most recent
    ``TurnStarted(actor_id)`` (the turn now ending, since this is only called
    from a ``turn_end`` hook) and look for a matching ``EffectApplied`` after
    it. This is how "until the end of your NEXT turn" gets its one-turn grace
    without a side table — an effect applied on your own turn survives that
    turn's end; one applied on somebody else's turn dies at your very next
    one. Effects seeded into ``start_combat`` emit no ``EffectApplied`` and so
    read as "not applied this turn", which is the right answer for them.
    """
    target_id, effect_id, origin = identity
    return any(
        isinstance(ev, EffectApplied)
        and ev.effect.target_id == target_id
        and ev.effect.id == effect_id
        and ev.effect.origin == origin
        for ev in _current_turn_events(live, actor_id)
    )


def _expire_timed_effects_at_turn_end(live: _LiveCombat, actor_id: str) -> None:
    """F3b — the three duration shapes ``_tick_durations_at_turn_end`` does not own.

    Registered as the ``engine:timed-effect-expiry`` ``turn_end`` hook, right
    after the ``rounds`` tick. Every branch emits
    ``EffectExpired(reason="duration")`` and, like the round tick, snapshots the
    identities first so in-loop ``_emit`` calls (which pop the effect out of
    ``live.active_effects``) cannot invalidate the iteration. No RNG.

    ``turns`` — SRD durations counted in the *subject's* turns ("until the end
    of its next turn", Foundry's ``duration.turns``). Decremented at the
    TARGET's own turn end, not the caster's, and independent of any ``rounds``
    counter: whichever counter reaches zero first expires the effect.

    ``seconds`` — SRD §Duration puts a round at 6 seconds, so a seconds-valued
    duration is ``ceil(seconds / 6)`` rounds ticked exactly like ``rounds``
    (caster-keyed, via ``_duration_tick_matches_actor``). The derived counter is
    materialised ONCE into ``duration.rounds`` — decremented in the same pass,
    so ``seconds=12`` is indistinguishable from ``rounds=2`` — after which the
    pre-existing round tick owns it and this branch stands down. ``seconds`` is
    never mutated; it stays readable as the effect's narrative-time duration.
    An effect that arrives with BOTH ``rounds`` and ``seconds`` (Bless ships
    ``rounds=10, seconds=60``) is governed by ``rounds``: the seconds branch
    only fires when ``rounds is None``.

    ``flags["until_end_of_next_turn_of"] = <actor_id>`` — "until the end of
    your next turn". Expires at that actor's next turn end, with a one-turn
    grace when the effect was applied during that same actor's turn
    (``_effect_applied_during_current_turn``).

    Concentration-flagged effects are exempt from all three branches, exactly
    as they are from the round tick: the concentration cascade and the
    per-turn repeat save own their lifetime, and the Foundry packs ship
    display-only counters on them (Hunter's Mark's ``seconds=600``).
    """
    to_expire: list[tuple[str, str, str]] = []
    for target_id, target_effects in list(live.active_effects.items()):
        for idx, eff in enumerate(list(target_effects)):
            if eff.flags.get("concentration"):
                continue
            duration = eff.duration
            expired = False

            # (1) turns — the TARGET's own turn end.
            if duration.turns is not None and target_id == actor_id:
                turns_left = duration.turns - 1
                if turns_left > 0:
                    duration = duration.model_copy(update={"turns": turns_left})
                else:
                    expired = True

            # (2) seconds -> ceil(seconds / 6) rounds, caster-keyed. Skipped
            # entirely once a rounds counter exists (rounds wins).
            if (
                not expired
                and duration.rounds is None
                and duration.seconds is not None
                and _duration_tick_matches_actor(
                    origin=eff.origin or "", target_id=target_id, actor_id=actor_id
                )
            ):
                whole_rounds = -(-duration.seconds // _SECONDS_PER_ROUND)
                rounds_left = whole_rounds - 1
                if rounds_left > 0:
                    duration = duration.model_copy(update={"rounds": rounds_left})
                else:
                    expired = True

            # (3) until the end of <actor>'s next turn.
            if (
                not expired
                and eff.flags.get("until_end_of_next_turn_of") == actor_id
                and not _effect_applied_during_current_turn(
                    live, actor_id, (target_id, eff.id, eff.origin)
                )
            ):
                expired = True

            if expired:
                to_expire.append((target_id, eff.id, eff.origin))
            elif duration is not eff.duration:
                # Immutable replacement — previously emitted EffectApplied
                # events hold the same ActiveEffect instance (see the round
                # tick's note), so never mutate the duration in place.
                target_effects[idx] = eff.model_copy(update={"duration": duration})

    for target_id, effect_id, origin in to_expire:
        _emit(
            live,
            EffectExpired(
                effect_id=effect_id,
                target_id=target_id,
                origin=origin,
                reason="duration",
            ),
        )


def _run_end_of_turn_saves(live: _LiveCombat, actor_id: str) -> None:
    """SRD §Hold Person / §Hold Monster / §Dominate Person — *"At the end
    of each of its turns, the target repeats the save, ending the spell
    on itself on a success."*

    Walks ``live.repeat_save_on_turn_end[actor_id]`` and rolls one save
    per pending spec. Emit order per spec: ``SaveRolled`` first, then
    on success an ``EffectExpired(reason=duration)`` for the source
    effect + ``ConditionRemoved`` for the applied condition (mirrors
    the cascade ``_drop_concentration`` performs on concentration drop,
    minus the ConcentrationDropped event — the caster keeps their
    concentration if the target shakes free on their own turn). Also
    clears the matching entry from ``concentration_chain[caster_id]``
    so the caster's concentration tracking reflects the target's exit.

    The repeat save applies the target's real ability modifier +
    proficiency bonus (F1c, via ``actor_stats.save_modifier``), matching
    the IR-level Save handler's per-target sidecar projection, and the d20
    goes through the shared ``roll_d20_test`` primitive (F2c) with no
    advantage source — a single draw, as before.
    """
    # Collect every repeat-save spec keyed on the actor_id-prefixed
    # identity tuples. Identity is (target_id, effect.id, effect.origin).
    pending_keys = [k for k in live.repeat_save_on_turn_end if k[0] == actor_id]
    if not pending_keys:
        return
    # One lookup for the whole call: every spec here re-saves for ``actor_id``
    # (the creature whose turn is ending), so the combatant is invariant across
    # both loops. The ``else 0`` below cannot fire in practice — this function
    # is only reached from the turn-advance path for a combatant that is in
    # ``live.initiative`` — but ``_run_end_of_turn_saves`` runs inside the
    # non-throwing turn-boundary contract, so a missing combatant degrades to an
    # unmodified save rather than aborting the turn.
    target = _find_combatant(live, actor_id)
    for identity in pending_keys:
        target_id, effect_id, origin = identity
        specs = live.repeat_save_on_turn_end.get(identity, [])
        surviving: list[dict[str, Any]] = []
        for spec in specs:
            ability = spec["ability"]
            dc = int(spec["dc"])
            condition = str(spec["condition"])
            caster_id = str(spec["caster_id"])
            # SRD 5.2 Conditions on the repeat save: the same auto-fail /
            # disadvantage projection the activity save path gets, plus the
            # Exhaustion D20-Test penalty. An unconditioned target projects
            # nothing, so the seeded stream is unmoved.
            save_proj = project_passive_save_modifiers(_condition_names(target)) if target else {}
            ability_upper = ability.upper()
            if ability_upper in save_proj.get("passive_save_auto_fail", []):
                # SRD 5.2 Paralyzed / Stunned / Petrified / Unconscious —
                # automatic failure, no d20 drawn (mirrors save_primitive).
                roll_total, succeeded = 0, False
                mode: AdvantageMode = "normal"
                natural: int | None = None
                roll_modifier = 0
                roll_sources: list[AdvantageSource] = []
            else:
                modifier = (
                    save_modifier(target, ability).total + d20_test_penalty(target.conditions)
                    if target
                    else 0
                )
                dis: tuple[AdvantageSource, ...] = (
                    ("condition:target",)
                    if ability_upper in save_proj.get("passive_save_dis", [])
                    else ()
                )
                roll = roll_d20_test(live.rng, modifier, AdvantageSources(disadvantage=dis))
                roll_total, succeeded = roll.total, roll.total >= dc
                mode, natural, roll_modifier = roll.mode, roll.kept, roll.modifier
                roll_sources = list(roll.sources)
            # C18 §Monster action economy — Legendary Resistance: this save
            # bypasses ``activities/save_primitive.roll_save`` entirely (it
            # rolls its own d20 above), so the conversion is decided here,
            # BEFORE ``SaveRolled`` is emitted, via the shared orchestrator-
            # level helper (``_consume_armed_legendary_resistance`` — also
            # used by the concentration check); ``LegendaryResistanceUsed``
            # follows the ``SaveRolled``.
            lr_remaining = (
                _consume_armed_legendary_resistance(live, target)
                if not succeeded and target is not None
                else None
            )
            if lr_remaining is not None:
                succeeded = True
            _emit(
                live,
                SaveRolled(
                    target_id=actor_id,
                    ability=ability,
                    dc=dc,
                    roll_total=roll_total,
                    succeeded=succeeded,
                    advantage=mode,
                    natural=natural,
                    modifier=roll_modifier,
                    sources=roll_sources,
                ),
            )
            if lr_remaining is not None:
                _emit_legendary_resistance_used(live, actor_id, lr_remaining)
            if not succeeded:
                surviving.append(spec)
                continue
            # Save succeeded — the spell ends on the target. Expire the
            # effect and remove its sourced condition. The caster's
            # concentration_chain entry for this target is pruned so a
            # future damage-driven CON save knows the target no longer
            # carries this effect (the caster's concentration itself
            # persists if other targets remain — SRD §Hold Person
            # higher-slot casts target multiple humanoids).
            _emit(
                live,
                EffectExpired(
                    target_id=actor_id,
                    effect_id=effect_id,
                    origin=origin,
                    reason="duration",
                ),
            )
            _emit(
                live,
                ConditionRemoved(
                    target_id=actor_id,
                    condition=condition,
                ),
            )
            live.conditions_by_effect.pop(identity, None)
            chain = live.concentration_chain.get(caster_id)
            if chain is not None:
                survivors = [entry for entry in chain if entry != (target_id, effect_id, origin)]
                if survivors:
                    live.concentration_chain[caster_id] = survivors
                else:
                    live.concentration_chain.pop(caster_id, None)
                    live.concentration_rounds_remaining.pop(caster_id, None)
        if surviving:
            live.repeat_save_on_turn_end[identity] = surviving
        else:
            live.repeat_save_on_turn_end.pop(identity, None)


# ── Public seam ─────────────────────────────────────────────────────────────


def _hit_dice_counter_key() -> str:
    """The punched-out sidecar counter that mirrors spent Hit Point Dice."""
    return "hit_dice_spent"

def _activity_hit_dice_cost(activities: Sequence[Any]) -> int:
    """How many Hit Point Dice ONE resolution of activities expends.

    SRD 5.2 Arcane Vigor: the activity NAMES the spend in its own
    ``consumption.targets[]`` — ``{type: hitDice, value: 2}`` — *and* writes
    the same number into the healing formula (``2d@attributes.hd.largestFace
    + @mod``). The consumption entry is the authoritative statement: it is
    the number the corpus asks for regardless of which die is rolled, while
    the formula only describes the healing. A resolution that names neither
    spends nothing.

    A ``(hit dice type removed after reading)`` form of the *same* activity —
    Arcane Vigor's five variants, one per die size — therefore costs what its
    consumption says, which is 2 for every variant.
    """
    from dnd5e_engine.activities.formula import hit_dice_expended

    for activity in activities:
        consumption = getattr(activity, "consumption", None)
        for entry in getattr(consumption, "targets", ()) or ():
            if getattr(entry, "type", None) != "hitDice":
                continue
            value = getattr(entry, "value", None)
            if isinstance(value, str) and value.strip().isdigit():
                count = int(value.strip())
                if count:
                    return count
    for activity in activities:
        healing = getattr(activity, "healing", None)
        if healing is None:
            continue
        custom = getattr(healing, "custom", None)
        spent = hit_dice_expended(getattr(custom, "formula", "") or "")
        if spent:
            return spent
    return 0


def _expend_hit_dice(live: _LiveCombat, c: Combatant, activities: Sequence[Any]) -> None:
    """Spend the Hit Point Dice this resolution rolled, refusing an overspend.

    Only an unexpended die can be rolled, so a cast that asks for more than
    the pool holds is refused with a named CastFailed and changes nothing.
    The largest face drains first, matching what the formula read.
    """
    count = _activity_hit_dice_cost(activities)
    if not count:
        return
    remaining = dict(c.hit_dice_remaining)
    if count > sum(remaining.values()):
        _emit(live, CastFailed(actor_id=c.entity_id, spell_id="", reason="no_uses_remaining"))
        return
    left = count
    for face in sorted(remaining, reverse=True):
        while left and remaining[face] > 0:
            remaining[face] -= 1
            left -= 1
    _update_combatant(live, c.entity_id, hit_dice_remaining=remaining)
    # Mirror the spend onto the sidecar counter the host already reads
    # (``custom_counters_by_entity`` → hero resources), so a combat's Hit
    # Point Dice spend survives into the durable hero state the way spell
    # slots and item charges do.
    counters = live.custom_counters_by_entity.setdefault(c.entity_id, {})
    counter = counters.setdefault(_hit_dice_counter_key(), {"spent": 0})
    counter["spent"] = counter.get("spent", 0) + count

def _hit_dice_cast_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """Refuse a cast that would roll more Hit Point Dice than the pool holds.

    SRD 5.2 Arcane Vigor rolls "one or two of your unexpended Hit Point Dice",
    so the pool must cover the roll BEFORE the slot or the Action is spent —
    a refusal that healed and then failed would leave a half-applied action
    (§9). ``None`` for every other intent and for a cast the pool covers.

    Going BELOW zero after the roll counts as well: ``_expend_hit_dice`` runs
    after the healing has already been applied, so a pool that covers the
    cost exactly would be left at 0 by the cheapest die and the spell would
    then refuse a pool it had just emptied. The pool must cover the cost
    *and* keep a die in hand, which is the same rule stated from the
    surviving state rather than from the pre-cast one.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id:
        return None
    spell = get_lib_loader().get_spell(intent.spell_id)
    if spell is None:
        return None
    activities = [
        a
        for a in (getattr(spell, "activities", None) or ())
        if intent.activity_id in (None, "", getattr(a, "id", None))
    ]
    count = _activity_hit_dice_cost(activities)
    if not count:
        return None
    if count < sum(current.hit_dice_remaining.values()):
        return None
    return CastFailed(
        actor_id=current.entity_id,
        spell_id=intent.spell_id,
        reason="no_uses_remaining",
    )

def _hit_dice_spent(c: Combatant) -> int | None:
    """Hit Point Dice ``c`` has already expended, or ``None`` when it holds none."""
    if not c.hit_dice_total:
        return None
    return sum(c.hit_dice_total.values()) - sum(c.hit_dice_remaining.values())


def _hit_dice_pool_size(c: Combatant) -> int | None:
    """The number of Hit Point Dice ``c`` holds at full, or ``None``."""
    if not c.hit_dice_total:
        return None
    return sum(c.hit_dice_total.values())


def _largest_hit_die_face(c: Combatant) -> int | None:
    """The largest die size in ``c``'s pool (Foundry ``@attributes.hd.largestFace``).

    SRD 5.2 Arcane Vigor rolls "one or two of your unexpended Hit Point Dice";
    a multiclass pool can hold several sizes, so the largest face that still
    has an unspent die is the one the formula reads. ``None`` for a creature
    with no Hit Dice at all.
    """
    live_faces = [face for face, count in c.hit_dice_remaining.items() if count > 0]
    if not live_faces:
        return None
    return max(live_faces)


def _hit_dice_pool(c: Combatant | PartyMemberSpec) -> dict[int, int]:
    """The Hit Point Dice ``c`` holds: ``{face: count}``.

    SRD 5.2 Multiclassing: "Add together the Hit Dice granted by all your
    classes to form your pool of Hit Dice." Each class contributes one die of
    its own die size per level, so the pool is read from the class documents
    the corpus carries — never from a formula guessed here. A creature with no
    classes (a stat-block monster) holds none, which is why the field is empty
    rather than a default d8.
    """
    classes = _class_levels(c)
    if not classes:
        return {}
    loader = get_lib_loader()
    pool: dict[int, int] = {}
    for slug, level in classes.items():
        doc = loader.get_class(slug)
        if doc is None or not level:
            continue
        size = hit_die_size(str(doc.hit_die))
        pool[size] = pool.get(size, 0) + int(level)
    return pool

def _pc_condition_immunities(pc: PartyMemberSpec) -> list[str]:
    """Union the PC spec's ``condition_immunities`` with those projected from
    its always-on granted-feature ``system.traits.ci.value`` changes.

    A PC built via ``build_party_member`` already carries its projected
    condition immunities on the spec; a host that constructs a raw
    ``PartyMemberSpec`` (as the S02 druid does) still gets its always-on
    subclass/class condition immunities projected here at combat-build time —
    Nature's Ward on a level-10 Circle-of-Land druid. Idempotent: the union
    dedupes, so re-projecting a ``build_party_member`` PC is a no-op. Scoped to
    condition immunities only — a brand-new field, so this cannot change any
    existing combat's damage/senses behavior; resistances/senses/movement stay
    owned by ``build_party_member`` (re-projecting them here would double-count
    the walk-speed bonus).
    """
    immunities = list(pc.condition_immunities)
    classes = _class_levels(pc)
    if not (classes or pc.subclass_slug or pc.species_slug):
        return immunities
    loader = get_lib_loader()
    owners = feature_owners(
        classes=classes,
        subclass_slug=pc.subclass_slug,
        species_slug=pc.species_slug,
        level=pc.character_level,
        loader=loader,
    )
    changes: list[Any] = []
    for slug in leveled_feature_slugs([(doc, level) for _, doc, level in owners]):
        feature = loader.get_feature(slug)
        if feature is None:
            continue
        for passive in feature.passive_effects:
            if passive.transfer and not passive.disabled:
                changes.extend(passive.changes)
    derived = interpret_passive_stats(changes=changes, trait_grants=(), species_senses=None)
    for ci in derived.condition_immunities:
        if ci not in immunities:
            immunities.append(ci)
    return immunities


def _is_proficient_with_weapon(current: Combatant, weapon: Weapon | None) -> bool:
    """SRD 5.2 §Weapon Proficiency — "Anyone can wield a weapon, but you must
    have proficiency with it to add your Proficiency Bonus to an attack roll
    you make with it." (packs/_source/content24/chapter-6/equipment.yml, id
    dWQ2ZTLOuKr3PMAx). "A monster is proficient with any weapon in its stat
    block" (same source) — monsters never carry an explicit
    ``weapon_proficiencies`` list, so ``current.weapon_proficiencies is None``
    covers them for free.

    ``current.weapon_proficiencies is None`` is the unset sentinel: the host
    never opted into enforcement (``PartyMemberSpec.weapon_proficiencies``
    unset), so proficiency is assumed — this reproduces every pre-C15
    fixture byte-identically. ``weapon is None`` covers non-weapon resolution
    paths (spells, features) where proficiency never applies. Otherwise,
    proficient iff the weapon's category or its own slug is in the caster's
    explicit (possibly empty — "proficient in nothing") list.
    """
    if current.weapon_proficiencies is None or weapon is None:
        return True
    return (
        weapon.weapon_category in current.weapon_proficiencies
        or weapon.slug in current.weapon_proficiencies
    )


def _sidecar_hit_dice_spent(counters: dict[str, dict[str, int]] | None) -> int:
    """Hit Point Dice the host says are already spent (``hit_dice_spent`` sidecar).

    The host projects the durable hero's ``custom_counters`` into every
    ``PartyMemberSpec``; ``hit_dice_spent`` is the one counter this engine
    both writes (``_expend_hit_dice``) and reads back here, so a spend in one
    fight is not forgotten by the next.
    """
    if not counters:
        return 0
    entry = counters.get("hit_dice_spent") or {}
    spent = entry.get("spent", 0)
    return int(spent) if isinstance(spent, int) and spent > 0 else 0


def _drained_hit_dice_pool(
    c: Combatant | PartyMemberSpec, spent: int
) -> dict[int, int]:
    """The full Hit Point Dice pool with ``spent`` dice already punched out.

    The largest face drains first, exactly as ``_expend_hit_dice`` does, so a
    pool reloaded from the host matches the one the previous fight left
    behind. A ``spent`` larger than the pool drains it completely: the pool
    is the authority on its own size, and the counter cannot invent dice.
    """
    remaining = dict(_hit_dice_pool(c))
    left = max(0, spent)
    for face in sorted(remaining, reverse=True):
        if not left:
            break
        taken = min(left, remaining[face])
        remaining[face] -= taken
        left -= taken
    return remaining


def _build_pc_combatants(
    party: list[PartyMemberSpec],
    combatants: list[Combatant],
    actor_zone: dict[str, str],
    tracked_hp: dict[str, int],
    spell_slots_by_entity: dict[str, dict[int, int]],
    pact_slots_by_entity: dict[str, dict[int, int]],
    spells_known_by_entity: dict[str, list[str]],
    custom_counters_by_entity: dict[str, dict[str, dict[str, int]]],
    hit_dice_spent_by_entity: dict[str, int] | None = None,
) -> None:
    """Append a ``Combatant`` per party member and populate the passed
    accumulator dicts (actor_zone, tracked_hp, spell_slots/pact_slots/
    spells_known/custom_counters) in place. Mutation-only helper — returns
    ``None``.
    """
    for pc in party:
        worn_armor, shield_equipped = _worn_armor(pc.equipment)
        combatants.append(
            Combatant(
                entity_id=pc.entity_id,
                entity_type="Character",
                name=pc.name,
                initiative=pc.initiative,
                hp_current=pc.hp_current,
                hp_max=pc.hp_max,
                ac=pc.ac,
                # C15 sentinel (mirrors weapon_proficiencies below): ``None``
                # when the host never explicitly set
                # ``PartyMemberSpec.attack_bonus`` — lets a bare-ability PC's
                # weapon attack fall through to the real ability-mod +
                # proficiency-bonus computation instead of being pinned to a
                # 0 override. See ``Combatant.attack_bonus``.
                attack_bonus=(pc.attack_bonus if "attack_bonus" in pc.model_fields_set else None),
                strength=pc.strength,
                dexterity=pc.dexterity,
                constitution=pc.constitution,
                intelligence=pc.intelligence,
                wisdom=pc.wisdom,
                charisma=pc.charisma,
                concentration_effect_id=pc.concentration_effect_id,
                creature_type=pc.creature_type,
                damage_resistances=list(pc.damage_resistances),
                damage_immunities=list(pc.damage_immunities),
                damage_vulnerabilities=list(pc.damage_vulnerabilities),
                ignored_difficult_terrain=tuple(pc.ignored_difficult_terrain),
                languages=tuple(pc.languages),
                attuned_items=tuple(pc.attuned_items),
                condition_immunities=_pc_condition_immunities(pc),
                senses=pc.senses,
                character_level=pc.character_level,
                base_speed=pc.base_speed,
                movement_remaining=pc.base_speed,
                movement_modes=pc.movement_modes,
                hit_dice_remaining=_drained_hit_dice_pool(
                    pc,
                    (hit_dice_spent_by_entity or {}).get(pc.entity_id, 0),
                ),
                hit_dice_total=_hit_dice_pool(pc),
                melee_reach_ft=pc.reach_ft,
                class_slug=pc.class_slug,
                classes=dict(pc.classes),
                fighting_styles=styles_from_feats(pc.feats, pc.fighting_style),
        chosen_features=frozenset(pc.chosen_features),
                feats=tuple(pc.feats),
                spell_abilities=dict(pc.spell_abilities),
                slotless_casts=tuple(pc.slotless_casts),
                worn_armor=worn_armor,
                shield_equipped=shield_equipped,
                subclass_slug=pc.subclass_slug,
                species_slug=pc.species_slug,
                save_proficiencies=list(pc.save_proficiencies),
                skill_proficiencies=list(pc.skill_proficiencies),
                skill_expertise=list(pc.skill_expertise),
                # SRD 5.2 Bard 2 "Jack of All Trades": the sheet flag rides a
                # granted class feature, so the live combatant derives it from
                # the same repertoire the USE_FEATURE gate reads — a class the
                # rules never grant it to cannot acquire it through the sheet.
                jack_of_all_trades=("jack-of-all-trades" in _granted_feature_slugs(pc)),
                # The unset sentinel: thread ``None`` when the host never
                # explicitly set the spec field (legacy "assume proficient"
                # behaviour, byte-identical to every pre-C15 fixture);
                # thread the real (possibly empty) list only when the host
                # opted in. See ``Combatant.weapon_proficiencies``.
                weapon_proficiencies=(
                    list(pc.weapon_proficiencies)
                    if "weapon_proficiencies" in pc.model_fields_set
                    else None
                ),
                # Same sentinel contract for mastery: unset keeps a monster's
                # printed mastery authoritative, while an explicit (possibly
                # empty) list means the hero chose those weapon kinds.
                weapon_masteries=(
                    list(pc.weapon_masteries)
                    if "weapon_masteries" in pc.model_fields_set
                    else None
                ),
            )
        )
        actor_zone[pc.entity_id] = pc.zone_id
        tracked_hp[pc.entity_id] = pc.hp_current
        if pc.spell_slots:
            spell_slots_by_entity[pc.entity_id] = dict(pc.spell_slots)
        if pc.pact_slots:
            pact_slots_by_entity[pc.entity_id] = dict(pc.pact_slots)
        if pc.spells_known:
            spells_known_by_entity[pc.entity_id] = list(pc.spells_known)
        if pc.custom_counters:
            custom_counters_by_entity[pc.entity_id] = {
                k: dict(v) for k, v in pc.custom_counters.items()
            }


def _build_foe_combatants(
    encounter: list[EncounterMemberSpec],
    combatants: list[Combatant],
    actor_zone: dict[str, str],
    tracked_hp: dict[str, int],
    monster_slug_by_entity: dict[str, str],
    xp_value_by_entity: dict[str, int],
) -> None:
    """Append a ``Combatant`` per encounter foe and populate the passed
    accumulator dicts (actor_zone, tracked_hp, monster_slug, xp_value) in
    place. Mutation-only helper — returns ``None``.
    """
    for foe in encounter:
        # hydrate the monster's SRD-canonical damage vulnerabilities
        # from its template when the spec leaves the field empty (the Skeleton's
        # ``["bludgeoning"]``). Scoped to vulnerabilities — the field is new, so
        # this cannot change any existing combat's behavior; resistances/
        # immunities stay host-populated by the existing convention.
        vulnerabilities = list(foe.damage_vulnerabilities)
        # C18 — hydrate damage resistances/immunities from the SRD 5.2
        # template when the spec leaves BOTH empty (host never authored
        # them). SRD 5.2 stat blocks are unconditional (no "from nonmagical
        # attacks" carve-out), so a template-hydrated resistance list always
        # pairs with ``physical_resistances_nonmagical_only=False`` — unlike
        # ``foe.physical_resistances_nonmagical_only`` (host-authored,
        # defaults True for the SRD 5.1 convention).
        resistances = list(foe.damage_resistances)
        immunities = list(foe.damage_immunities)
        nonmagical_only = foe.physical_resistances_nonmagical_only
        # F1b (2026-08-26) — hydrate the five non-DEX ability scores,
        # proficiency bonus, and save/skill proficiencies from the SRD
        # monster template when one is set. Dexterity is spec-authoritative
        # only when the host moved it away from the ``10`` default sentinel
        # (an EncounterMemberSpec can't distinguish "left at 10" from
        # "explicitly set to 10", so 10 always defers to the template).
        # Read by ``activities/actor_stats`` on every save/check path (F1c/F1d);
        # a foe with no ``monster_template_slug`` keeps the spec's values.
        # SRD 5.2 stat blocks list Senses and Condition Immunities: the
        # template supplies both unless the host set them (``senses`` not
        # ``None``; a non-empty ``condition_immunities``).
        template_kw: dict[str, Any] = {}
        senses = foe.senses
        condition_immunities = list(foe.condition_immunities)
        if foe.monster_template_slug:
            monster = get_lib_loader().get_monster(foe.monster_template_slug)
            if monster is not None:
                if senses is None:
                    senses = _monster_senses(monster)
                if not condition_immunities:
                    condition_immunities = list(monster.condition_immunities)
                if not vulnerabilities:
                    vulnerabilities = list(monster.damage_vulnerabilities)
                if not resistances and not immunities:
                    resistances = list(monster.damage_resistances)
                    immunities = list(monster.damage_immunities)
                    nonmagical_only = False
                sc = monster.ability_scores
                template_kw = {
                    "strength": sc.str,
                    "constitution": sc.con,
                    "intelligence": sc.int,
                    "wisdom": sc.wis,
                    "charisma": sc.cha,
                    "proficiency_bonus_override": monster.proficiency_bonus,
                    "save_proficiencies": [
                        a
                        for a in ("str", "dex", "con", "int", "wis", "cha")
                        if getattr(monster.saving_throws, a) is not None
                    ],
                    "skill_proficiencies": [
                        k for k, v in monster.skills.model_dump().items() if v is not None
                    ],
                    "trait_mechanics": [
                        a.mechanic for a in monster.special_abilities if a.mechanic is not None
                    ],
                    # SRD §Spellcasting — the ability a monster's innate/
                    # prepared spells key off. ``None`` for a
                    # template with no cast-bearing actions (unchanged
                    # ``Combatant`` default).
                    "spellcasting_ability": monster.spellcasting_ability,
                }
                legendary_actions_max = _legendary_action_uses_max(monster)
                if legendary_actions_max:
                    template_kw["legendary_actions_max"] = legendary_actions_max
                    template_kw["legendary_actions_remaining"] = legendary_actions_max
                legendary_resistances_max = _legendary_resistance_max(monster)
                if legendary_resistances_max:
                    template_kw["legendary_resistances_max"] = legendary_resistances_max
                    template_kw["legendary_resistances_remaining"] = legendary_resistances_max
                if foe.dexterity == 10:
                    template_kw["dexterity"] = sc.dex
        combatants.append(
            Combatant(
                entity_id=foe.entity_id,
                entity_type=foe.entity_type,
                name=foe.name,
                initiative=foe.initiative,
                hp_current=foe.hp_current,
                hp_max=foe.hp_max,
                ac=foe.ac,
                attack_bonus=foe.attack_bonus,
                damage_dice=foe.damage_dice,
                damage_type=foe.damage_type,
                behavior_profile=foe.behavior_profile,
                dexterity=template_kw.pop("dexterity", foe.dexterity),
                creature_type=foe.creature_type,
                damage_resistances=resistances,
                damage_immunities=immunities,
                damage_vulnerabilities=vulnerabilities,
                condition_immunities=condition_immunities,
                senses=senses if senses is not None else CombatantSenses(),
                physical_resistances_nonmagical_only=nonmagical_only,
                base_speed=foe.base_speed,
                movement_remaining=foe.base_speed,
                **template_kw,
            )
        )
        actor_zone[foe.entity_id] = foe.zone_id
        tracked_hp[foe.entity_id] = foe.hp_current
        if foe.monster_template_slug:
            monster_slug_by_entity[foe.entity_id] = foe.monster_template_slug
        if foe.xp_value > 0:
            xp_value_by_entity[foe.entity_id] = foe.xp_value


def _resolve_topology(
    party: list[PartyMemberSpec],
    encounter: list[EncounterMemberSpec],
    grid_scene: GridScene,
) -> GridTopology:
    """The combat's ``GridTopology``, after validating every start cell. Raises
    ``ValueError`` when ``grid_scene`` is missing or a start cell is out of
    bounds or blocked.
    """
    if grid_scene is None:
        raise ValueError("start_combat: grid_scene is required")
    grid = GridTopology(grid_scene)
    # Reject combatants whose start cell is out of bounds or impassable —
    # an illegal start position would silently disable the range/move gates
    # for that actor (they read actor_zone, which would hold a bad cell).
    members: list[PartyMemberSpec | EncounterMemberSpec] = [*party, *encounter]
    for spec in members:
        if not grid.is_valid_cell(spec.zone_id):
            raise ValueError(
                f"start_combat: {spec.entity_id} start cell {spec.zone_id!r} "
                f"is out of bounds or blocked"
            )
    return grid


def _seed_active_effects(live: _LiveCombat, active_effects: Sequence[ActiveEffect]) -> None:
    """Seed ``live`` from caller-supplied ActiveEffects, mutating ``live`` in
    place: append to active_effects, parse the concentration chain (and write
    the caster's concentration_effect_id), build conditions_by_effect, and
    project active_conditions + per-combatant Combatant.conditions.
    """
    for eff in active_effects:
        live.active_effects.setdefault(eff.target_id, []).append(eff)

        # Concentration chain: parse caster_id from origin convention
        # "cast:<slug>:<caster_id>". Equipped-item enchantments use
        # "item:<item_id>:<effect_id>" — no caster, skipped.
        if eff.flags.get("concentration"):
            origin = eff.origin or ""
            caster_id: str | None = None
            if origin.startswith("cast:"):
                parts = origin.split(":", 2)
                if len(parts) == 3:
                    caster_id = parts[2]
            if caster_id:
                chain = live.concentration_chain.setdefault(caster_id, [])
                identity = (eff.target_id, eff.id, eff.origin)
                if identity not in chain:
                    chain.append(identity)
                # also write the caster's
                # Combatant.concentration_effect_id so _build_hydration_payload
                # can derive existing_concentration. Without this, a seeded
                # concentration effect (Bless carried over from a prior
                # combat, future cross-combat persistence) looks like
                # the caster isn't concentrating and the next cast won't
                # drop the old one.
                for idx, c in enumerate(live.initiative):
                    if c.entity_id != caster_id:
                        continue
                    if c.concentration_effect_id is None:
                        live.initiative[idx] = c.model_copy(
                            update={"concentration_effect_id": eff.id}
                        )
                    break

        # SRD §Condition Immunity — the same gate the runtime
        # ``EffectApplied`` fold and ``activities/effects.py`` apply: a status
        # the target is immune to never attaches, on EITHER store. A seeded
        # effect is the one path that writes ``live.active_conditions``
        # directly, so without this the host-facing view (``views.py``) and
        # ``Combatant.conditions`` would BOTH carry a condition the creature
        # cannot suffer. The ActiveEffect itself is still seeded (its non-
        # condition riders stay live), exactly as the emit-path keeps the
        # ``EffectApplied`` and drops only the ``ConditionApplied``.
        target_combatant = _find_combatant(live, eff.target_id)
        statuses = {
            s
            for s in eff.statuses
            if target_combatant is None
            or not is_condition_immune(target_combatant, s, imposed=eff.statuses)
        }

        # Conditions-by-effect: every status the effect imposes is
        # attributed to (target_id, id, origin), so expire/concentration
        # cascade can find them.
        if statuses:
            key = (eff.target_id, eff.id, eff.origin)
            existing = live.conditions_by_effect.get(key)
            if existing is None:
                live.conditions_by_effect[key] = list(statuses)
            else:
                for status in statuses:
                    if status not in existing:
                        existing.append(status)

        if not statuses:
            continue
        # Also project into live.active_conditions — the coarse per-entity
        # name set a host mirrors into its own storage — so a seeded status
        # is there on the next sync. Without this, statuses only land on
        # initiative[*].conditions (set below) and are silently dropped when
        # a host rebuilds its own condition storage from active_conditions.
        live.active_conditions.setdefault(eff.target_id, set()).update(statuses)
        for idx, c in enumerate(live.initiative):
            if c.entity_id != eff.target_id:
                continue
            current_conditions = c.conditions
            existing_keys = {(ac.condition, ac.source_effect_id) for ac in current_conditions}
            new_conditions = list(current_conditions)
            dirty = False
            for status in statuses:
                if (status, eff.id) in existing_keys:
                    continue
                new_conditions.append(
                    ActiveCondition(
                        condition=status,
                        source_entity_id="implied:effect",
                        scope="combat",
                        source_effect_id=eff.id,
                    )
                )
                dirty = True
            if dirty:
                live.initiative[idx] = c.model_copy(update={"conditions": new_conditions})
            break


def _seeded_incapacitated_ids(active_effects: Sequence[ActiveEffect]) -> frozenset[str]:
    """Entity ids whose SEEDED ``active_effects`` (passed into ``start_combat``,
    before any ``Combatant`` exists) carry a status that is Incapacitated or
    implies it (Paralyzed / Petrified / Stunned / Unconscious — SRD 5.2
    ``CONDITION_IMPLIES``). Reuses the same implication chain
    ``is_condition_active`` walks for the live-combat Incapacitated read
    (``_sneak_ally_adjacent_map`` et al.), applied here to the pre-seat
    effect statuses rather than ``Combatant.conditions``.
    """
    ids: set[str] = set()
    for eff in active_effects:
        if is_condition_active(Condition.INCAPACITATED, list(eff.statuses)):
            ids.add(eff.target_id)
    return frozenset(ids)


def _resolve_initiative(
    spec: PartyMemberSpec | EncounterMemberSpec,
    rng: random.Random,
    seeded_incapacitated: frozenset[str],
) -> int:
    """SRD 5.2 Initiative: "every participant rolls Initiative; they make a
    Dexterity check". ``spec.initiative`` being an explicit int always wins
    (zero RNG draws — the legacy / host-supplied path). ``None`` opts into an
    engine-rolled d20 + DEX modifier, plus a character's Proficiency Bonus with
    Alert (``_initiative_bonus``). Surprise (and Incapacitated at roll
    time — both SRD-cited on the spec fields / ``_seeded_incapacitated_ids``)
    impose Disadvantage per SRD 5.2 Surprise / the Incapacitated glossary
    entry.
    """
    if spec.initiative is not None:
        return spec.initiative
    disadvantage = spec.is_surprised or spec.entity_id in seeded_incapacitated
    # SRD 5.2 Ioun Stone of Awareness: "Advantage on Initiative rolls." The
    # item mode folds into the spec, so the roll takes it here. Advantage
    # and Disadvantage cancel (SRD 5.2 D20 Tests: "If you have both, you roll
    # once"), which the primitive already does on an empty source set.
    item_advantage = item_disadvantage = False
    if isinstance(spec, PartyMemberSpec):
        item_advantage = spec.initiative_advantage > 0
        item_disadvantage = spec.initiative_advantage < 0
    advantage = item_advantage and not disadvantage
    disadvantage = (disadvantage or item_disadvantage) and not item_advantage
    sources = AdvantageSources(
        advantage=("item:initiative",) if advantage else (),
        disadvantage=("condition:attacker",) if disadvantage else (),
    )
    modifier = ability_modifier(_initiative_dexterity(spec)) + _initiative_bonus(spec)
    return roll_d20_test(rng, modifier, sources).total


# SRD 5.2 feat slugs this module applies (``PartyMemberSpec.feats``).
_ALERT: Final = "alert"


def _initiative_bonus(spec: PartyMemberSpec | EncounterMemberSpec) -> int:
    """SRD 5.2 Alert: "When you roll Initiative, you can add your Proficiency
    Bonus to the roll." Only an engine-rolled Initiative reaches here: a host
    that rolls its own adds the bonus itself."""
    if isinstance(spec, PartyMemberSpec) and _ALERT in spec.feats:
        return proficiency_bonus(spec.character_level)
    return 0


def _initiative_dexterity(spec: PartyMemberSpec | EncounterMemberSpec) -> int:
    """The Dexterity score an engine-rolled Initiative adds. An encounter
    member's ``10`` defers to its resolvable ``monster_template_slug``, exactly
    as the combatant's own ``dexterity`` does (``_build_foe_combatants``), so
    the roll and the tie-break read the same score."""
    if (
        isinstance(spec, EncounterMemberSpec)
        and spec.dexterity == 10
        and spec.monster_template_slug
    ):
        monster = get_lib_loader().get_monster(spec.monster_template_slug)
        if monster is not None:
            return monster.ability_scores.dex
    return spec.dexterity


#: Version of the ``serialize_combat`` payload shape. Bumped whenever a field
#: changes meaning or a new required field appears; a snapshot of another
#: version is refused rather than reinterpreted (§10: an old save gets a
#: checked refusal, an explicit migration, or a clear message — never a
#: silent re-reading of the same keys under different rules).
COMBAT_SNAPSHOT_FORMAT: Final = 1

#: Edition the snapshot's numbers were computed under. The rules corpus is
#: SRD 5.2; a snapshot that claims another edition cannot be resumed here.
COMBAT_SNAPSHOT_EDITION: Final = "5.2"

#: Fields of ``_LiveCombat`` that hold LIVE Python objects and cannot travel as
#: JSON. ``rng`` is rebuilt from its own serialized state (so the next draw is
#: the one the saved fight would have made), ``event_queue`` starts empty (a
#: restored fight has no consumer for events already delivered), and
#: ``event_listeners`` belong to whichever call is running right now.
_UNSERIALIZED_LIVE_FIELDS = frozenset({
    "rng",
    "event_queue",
    "event_listeners",
    "lifecycle",
})


def serialize_combat(handle: CombatHandle) -> dict[str, Any]:
    """Snapshot a live combat as plain JSON-ready data.

    Separates the fields that survive a process boundary from the ones that
    cannot: ``rng`` is stored via ``random.Random.getstate()`` rather than
    reset, so resuming does NOT restart the die stream (replaying a fight
    from the top would make a save/load pair an exploit — §9 "повтор
    запроса не должен выдавать награду").

    ``lifecycle`` is dropped on purpose: it is a registry of module-level
    hooks rebuilt by ``_register_default_turn_hooks`` on resume, so carrying
    it would duplicate registrations (``TurnLifecycle.register`` raises on a
    repeated key).
    """
    live = _get_live(handle)
    payload: dict[str, Any] = {}
    for spec in dataclasses_fields(live):
        if spec.name in _UNSERIALIZED_LIVE_FIELDS:
            continue
        payload[spec.name] = getattr(live, spec.name)
    payload["rng_state"] = live.rng.getstate()
    payload["snapshot_format"] = COMBAT_SNAPSHOT_FORMAT
    payload["rules_edition"] = COMBAT_SNAPSHOT_EDITION
    # ``GridTopology`` is a plain class over ``GridScene``; its private maps
    # do not survive JSON. The scene is stored instead and the grid is rebuilt
    # from it on resume — one source of truth for the battlefield.
    payload["grid_scene"] = live.topology.scene
    return json.loads(json.dumps(_json_ready(payload), default=_json_default))


def _json_ready(value: Any) -> Any:
    """Convert a payload tree to JSON primitives WITHOUT losing type.

    ``json.dumps(default=...)`` only sees values it cannot encode, and a
    Pydantic model is not directly encodable in every version — falling back
    to ``str()`` there silently turned ``GridTopology`` and ``Combatant``
    into their ``repr``, which then failed to resume. So models and
    dataclasses are converted HERE, where their type is still known.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _json_ready(dump(mode="json"))
    if dataclasses_is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _json_ready(getattr(value, f.name)) for f in dataclasses_fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_ready(v) for v in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

def _json_default(value: Any) -> Any:
    """JSON fallback for the combat payload: Pydantic models dump
    themselves; sets/tuples become lists (the resume side re-types them)."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def resume_combat(payload: Mapping[str, Any]) -> CombatHandle:
    """Re-register a fight snapshotted by ``serialize_combat``.

    The restored handle serves intents exactly where the saved fight stood:
    same turn index, same round, same HP, slots, counters, effects and
    concentration chain, and the same RNG position. Nothing is replayed —
    ``start_combat`` is NOT called, because it would re-roll initiative,
    re-fire the initiative-triggered regains and duplicate the effect
    lifecycle links.

    Raises ``ValueError`` when the payload lacks an identity field, so a
    truncated save refuses instead of becoming a plausible empty fight
    (§10: a stale save gets a checked refusal, never a silent repair).
    """
    for required in ("handle_id", "session_id", "initiative", "topology"):
        if required not in payload:
            raise ValueError(
                f"resume_combat: payload lacks {required!r}; refusing to build a partial fight"
            )
    # Version check BEFORE anything is built: a snapshot of another format or
    # another rules edition must not be reinterpreted under today's engine.
    # The state would look plausible and compute differently — the exact
    # silent substitution §10 forbids.
    format_version = payload.get("snapshot_format")
    if format_version != COMBAT_SNAPSHOT_FORMAT:
        raise ValueError(
            f"resume_combat: snapshot format {format_version!r} is not the "
            f"supported {COMBAT_SNAPSHOT_FORMAT}; refusing to reinterpret it; "
            "an older fight needs an explicit migration, not a re-read of the "
            "same keys under different rules."
        )
    edition = payload.get("rules_edition")
    if edition != COMBAT_SNAPSHOT_EDITION:
        raise ValueError(
            f"resume_combat: snapshot was computed under rules edition "
            f"{edition!r}, this build runs {COMBAT_SNAPSHOT_EDITION!r}; "
            "the state would not be equivalent."
        )

    data = dict(payload)
    data.pop("snapshot_format", None)
    data.pop("rules_edition", None)
    rng_state = data.pop("rng_state", None)
    if rng_state is None:
        raise ValueError(
            "resume_combat: payload lacks rng_state; the die stream would restart"
        )
    handle_id = str(data["handle_id"])
    if handle_id in _REGISTRY:
        raise ValueError(f"combat {handle_id!r} is already live; refusing to overwrite it")
    data["initiative"] = [
        c if isinstance(c, Combatant) else Combatant.model_validate(c)
        for c in data["initiative"]
    ]
    scene_payload = data.pop("grid_scene", None)
    data["topology"] = (
        data["topology"]
        if isinstance(data["topology"], GridTopology)
        else GridTopology(GridScene.model_validate(scene_payload))
    )
    for name, kind in _RESUME_SET_FIELDS.items():
        if name in data:
            data[name] = kind(data[name])
    if "active_effects" in data:
        data["active_effects"] = {
            target: [ActiveEffect.model_validate(e) for e in effects]
            for target, effects in data["active_effects"].items()
        }
    if "deaths_recorded" in data:
        data["deaths_recorded"] = [
            _revive_dataclass(DeathRecord, d) for d in data["deaths_recorded"]
        ]
    for name in _RESUME_DICT_OF_INT_KEYS:
        if name in data:
            data[name] = {
                entity: {int(level): count for level, count in slots.items()}
                for entity, slots in data[name].items()
            }

    rng = random.Random()
    rng.setstate(_rng_state_from_json(rng_state))
    data["rng"] = rng
    data["event_queue"] = asyncio.Queue()
    data["event_listeners"] = []
    data.pop("lifecycle", None)
    data.pop("snapshot_format", None)
    data.pop("rules_edition", None)

    live = _LiveCombat(**data)
    _register_default_turn_hooks(live)
    _REGISTRY[handle_id] = live
    return CombatHandle(handle_id=handle_id)


#: Fields serialized as JSON lists but typed as sets in ``_LiveCombat``.
_RESUME_SET_FIELDS: Final = {
    "party_ids": set, "encounter_ids": set, "dead_ids": set, "hidden_entities": set, "alert_swaps_used": set
}

#: Slot pools keyed by entity then spell level. JSON turns the int level
#: into a string key, which would make every ``slots[1]`` lookup miss.
_RESUME_DICT_OF_INT_KEYS: Final = ("spell_slots_by_entity", "pact_slots_by_entity")


def _revive_dataclass(cls: Any, data: Any) -> Any:
    """Rebuild a plain dataclass from its JSON form (``GridTopology``,
    ``DeathRecord``). An already-typed instance passes through."""
    if isinstance(data, cls):
        return data
    if not isinstance(data, Mapping):
        raise ValueError(f"expected a mapping for {cls.__name__}, got {type(data).__name__}")
    return cls(**data)


def _rng_state_from_json(state: Any) -> tuple[Any, ...]:
    """Reverse ``random.Random.getstate()`` after a JSON round-trip.

    The Mersenne state is a nested list in JSON; ``setstate`` demands the
    exact shape ``getstate`` emitted (an inner tuple), so it is rebuilt here
    rather than trusted.
    """
    if not isinstance(state, (list, tuple)) or len(state) != 3:
        raise ValueError("resume_combat: malformed rng_state")
    version, internal, gauss = state
    return (int(version), tuple(int(v) for v in internal), gauss)


async def start_combat(
    *,
    session_id: str,
    party: list[PartyMemberSpec],
    encounter: list[EncounterMemberSpec],
    grid_scene: GridScene,
    rng_seed: int,
    scene_location_id: str = "loc:unknown",
    active_effects: Sequence[ActiveEffect] = (),
) -> StartCombatResult:
    """Open a combat, materialize runtime state, kick off the initiative loop.

    Returns a ``StartCombatResult`` envelope wrapping the ``CombatHandle``
    the caller threads through subsequent seam calls and the events emitted
    during open (round-start + first turn-start).

    ``grid_scene`` is the battlefield: every ``zone_id`` on ``party`` and
    ``encounter`` must name one of its in-bounds, unblocked cells.
    """
    if not party:
        raise ValueError("start_combat: party must be non-empty")
    if not encounter:
        raise ValueError("start_combat: encounter must be non-empty")

    # Hoisted so the SAME instance seeds both the initiative-rolling draws
    # below and every subsequent in-combat draw via ``_LiveCombat.rng``.
    rng = random.Random(rng_seed)

    # SRD 5.2 Initiative: "every participant rolls Initiative; they make a
    # Dexterity check". Entities with a fixed int ``initiative`` skip the
    # RNG entirely (zero draws — every host-int-initiative caller keeps its
    # exact pre-C14 draw sequence). ``None`` entities roll in SPEC ORDER
    # (party first, then encounter) so results are reproducible per seed.
    seeded_incapacitated = _seeded_incapacitated_ids(active_effects)
    party = [
        p.model_copy(update={"initiative": _resolve_initiative(p, rng, seeded_incapacitated)})
        for p in party
    ]
    encounter = [
        e.model_copy(update={"initiative": _resolve_initiative(e, rng, seeded_incapacitated)})
        for e in encounter
    ]

    # Initiative order: descending by initiative, ties broken by dex then
    # by entity_id for deterministic order. Mirrors the existing session
    # path's deterministic-tie-break convention.
    combatants: list[Combatant] = []
    actor_zone: dict[str, str] = {}
    monster_slug_by_entity: dict[str, str] = {}
    xp_value_by_entity: dict[str, int] = {}
    tracked_hp: dict[str, int] = {}
    tracked_temp_hp: dict[str, int] = {}
    spell_slots_by_entity: dict[str, dict[int, int]] = {}
    pact_slots_by_entity: dict[str, dict[int, int]] = {}
    spells_known_by_entity: dict[str, list[str]] = {}
    custom_counters_by_entity: dict[str, dict[str, dict[str, int]]] = {}
    # SRD 5.2: Hit Point Dice are a per-creature pool that survives a
    # combat — the host reports what has already been spent on its
    # ``hit_dice_spent`` sidecar (#2), and the pool starts that much
    # drained instead of full.
    hit_dice_spent_by_entity = {
        pc.entity_id: _sidecar_hit_dice_spent(pc.custom_counters)
        for pc in party
    }
    hit_dice_spent_by_entity = {
        entity_id: spent
        for entity_id, spent in hit_dice_spent_by_entity.items()
        if spent
    }
    _build_pc_combatants(
        party,
        combatants,
        actor_zone,
        tracked_hp,
        spell_slots_by_entity,
        pact_slots_by_entity,
        spells_known_by_entity,
        custom_counters_by_entity,
        hit_dice_spent_by_entity,
    )
    _build_foe_combatants(
        encounter,
        combatants,
        actor_zone,
        tracked_hp,
        monster_slug_by_entity,
        xp_value_by_entity,
    )

    combatants.sort(
        key=lambda c: (-c.initiative, -c.dexterity, c.entity_id),
    )

    topology = _resolve_topology(party, encounter, grid_scene)

    handle_id = f"combat:{session_id}:{rng_seed:08x}"
    live = _LiveCombat(
        handle_id=handle_id,
        session_id=session_id,
        initiative=combatants,
        party_ids={p.entity_id for p in party},
        encounter_ids={e.entity_id for e in encounter},
        topology=topology,
        rng=rng,
        event_queue=asyncio.Queue(),
        scene_location_id=scene_location_id,
        scene_sunlight=grid_scene.sunlight,
        actor_zone=actor_zone,
        monster_slug_by_entity=monster_slug_by_entity,
        xp_value_by_entity=xp_value_by_entity,
        tracked_hp=tracked_hp,
        tracked_temp_hp=tracked_temp_hp,
        spell_slots_by_entity=spell_slots_by_entity,
        pact_slots_by_entity=pact_slots_by_entity,
        spells_known_by_entity=spells_known_by_entity,
        custom_counters_by_entity=custom_counters_by_entity,
        opportunity_attack_weapons={
            pc.entity_id: slug
            for pc in party
            if (slug := _opportunity_attack_weapon_slug(pc)) is not None
        },
    )
    # SRD 5.2 Persistent Rage: `start_combat` IS this engine's Initiative
    # roll, so the regain happens here, once, while the sidecar counters are
    # already wired to ``live``.
    _regain_rage_on_initiative(live)
    _regain_bardic_inspiration_on_initiative(live)
    _uncanny_metabolism_on_initiative(live, rng)
    _perfect_focus_on_initiative(live)
    # The this-roll marker has done its one job (letting Perfect Focus see
    # whether Uncanny Metabolism fired in THIS `start_combat`). It is dropped
    # again so a later fight starts with no marker — the once-a-rest budget
    # lives in the `feature_use:` counter, which is what must persist.
    for _counters in live.custom_counters_by_entity.values():
        _counters.pop(_UNCANNY_METABOLISM_THIS_ROLL_KEY, None)
    # Every pool the host reported as spent is a fact about a creature that
    # survived an earlier fight; when the pool came back FULL (a larger pool
    # than the counter described, or a creature with no classes), the counter
    # is stale and is dropped rather than silently swallowing the difference.
    for entity_id, spent in hit_dice_spent_by_entity.items():
        pool = next((c for c in combatants if c.entity_id == entity_id), None)
        if pool is None or spent <= sum(pool.hit_dice_remaining.values()):
            continue
        _LOGGER.warning(
            "hit_dice_counter_stale entity_id=%s spent=%s pool=%s",
            entity_id,
            spent,
            sum(pool.hit_dice_remaining.values()),
        )
        custom_counters_by_entity.pop(entity_id, None)
        _update_combatant(live, entity_id, hit_dice_remaining=pool.hit_dice_total)
    # C18 — per-foe limited-use state (recharge actions, N/Day trait uses),
    # hydrated from the same monster template each foe's other stats came
    # from. Absent for foes with no resolvable ``monster_template_slug``.
    for entity_id, slug in monster_slug_by_entity.items():
        monster = get_lib_loader().get_monster(slug)
        if monster is not None:
            live.monster_action_uses_by_entity[entity_id] = _hydrate_monster_action_uses(monster)
    _REGISTRY[handle_id] = live
    _register_default_turn_hooks(live)

    # Seed _LiveCombat.active_effects from the caller: the equipped-magic-item
    # enchantments a host projects, or any other effect it carries in.
    #
    # Lifecycle bookkeeping: in addition to active_effects + combatant
    # conditions, seeded effects must also populate the concentration_chain
    # and conditions_by_effect indexes that runtime EffectApplied events
    # would have set via _record_effect_lifecycle_links. Without this,
    # a seeded concentration effect (Bless cast pre-combat, Hold Person
    # carried over) would never trigger concentration-drop on caster damage
    # and end-of-turn repeat saves would never fire. repeat_save_on_turn_end
    # is NOT seeded here — it requires a failed-save record we don't have
    # at seed time; the next runtime save will repopulate as needed.
    _seed_active_effects(live, active_effects)

    # C12 — a Character HYDRATED into combat already at 0 HP is Unconscious
    # (SRD 5.2 "Dropping to 0 Hit Points"). The runtime fold hangs off the
    # damage path, so without this a host resuming a saved combat with a downed
    # PC would get a PC that can act. ``_fold_condition_onto_combatant`` writes
    # BOTH condition stores, so the host-facing ``active_conditions`` view
    # agrees with ``Combatant.conditions``. The condition write itself is
    # state-only, no event — this is hydration of a state the host already
    # knows about, not a transition — and the death-save state stays exactly
    # as the host supplied it (the condition is what makes
    # ``_maybe_roll_death_save`` fire on the PC's turn; no failure is charged
    # here). Since C13, though, ``_fold_condition_onto_combatant`` also drops
    # concentration for any condition that implies Incapacitated (SRD 5.2:
    # "No Concentration. Your Concentration is broken."), so a 0-HP-hydrated
    # PC with a pre-seeded concentration effect on their sheet DOES emit here
    # — ``ConcentrationDropped`` / ``EffectExpired`` / ``ConditionRemoved`` —
    # which is SRD-correct, not a regression of the "no event" note above;
    # those events reach the queue (``narration_events``), not
    # ``StartCombatResult.events``, which only captures the seeding-loop slice
    # further up this function. Monsters are excluded — a monster at 0 HP is
    # dead, never Unconscious.
    for c in list(live.initiative):
        if c.entity_id in live.party_ids and c.is_alive and c.hp_current <= 0:
            _fold_condition_onto_combatant(live, c.entity_id, "unconscious")

    # C12 — the seeded conditions may already zero or reduce a Speed; project
    # every combatant's opening movement budget before the first turn opens.
    for c in list(live.initiative):
        _clamp_movement_budget(live, c.entity_id)

    # Emit the round-start + first turn-start so a consumer of
    # ``narration_events`` sees combat actually open. The evaluator
    # itself is invoked only from inside ``submit_player_intent`` once
    # an intent arrives — this matches the scaffold's
    # "RuntimeContext per evaluation" frozen-context model.
    start_events: list[CombatEvent] = []
    live.event_listeners.append(start_events.append)
    try:
        _begin_turn(live, new_round=True)
    finally:
        live.event_listeners.remove(start_events.append)

    return StartCombatResult(
        handle=CombatHandle(handle_id=handle_id),
        events=start_events,
    )


def _own_attacks_per_action(current: Combatant) -> int:
    """SRD 5.2 Extra Attack: one attack plus the highest qualifying tier's extra
    attacks (``rules.character.extra_attack_count``; tiers never add)."""
    if current.class_slug is None:
        return 1
    return 1 + extra_attack_count(_granted_feature_slugs(current))


def _attacks_per_action(live: _LiveCombat, current: Combatant) -> int:
    """The swings one Attack action gives ``current``: its form's count while
    it is transformed (C21 — the form's Multiattack, or for Wild Shape the
    higher of that and the creature's own Extra Attack, as ``_apply_transform``
    fixed it), a summon's Multiattack count at its spell's level (fixed when it
    was seated), else its own (``_own_attacks_per_action``)."""
    transform = _transform_of(live, current.entity_id)
    if transform is not None:
        return transform.attacks_per_action
    summon = live.summons.get(current.entity_id)
    if summon is not None:
        return summon.attacks_per_action
    return _own_attacks_per_action(current)


def _offhand_window_open(current: Combatant) -> bool:
    """SRD 5.2 Light property — True while the "extra attack with a
    different Light weapon" is still available this turn, REGARDLESS of how
    it is funded: a Light main-hand weapon was swung and no off-hand swing
    has been spent yet. Governs whether the main-hand attack tail keeps the
    turn. Nick: a wielder whose Bonus
    Action is already spent may still make the extra attack with a Nick
    weapon ("as part of the Attack action instead of as a Bonus Action"),
    and the orchestrator cannot know which off-hand weapon the host will
    submit until the intent arrives, so the turn must stay open. The
    Bonus-Action-FUNDED window is ``_twf_window_open``."""
    return current.light_weapon_swing_slug is not None and not current.offhand_attack_spent


def _twf_window_open(current: Combatant) -> bool:
    """SRD §Two-Weapon Fighting — True while a Bonus-Action off-hand swing
    is still available this turn: the Light extra-attack window is open
    (``_offhand_window_open``) AND the Bonus Action is unspent. A NON-Nick
    off-hand weapon needs this; a Nick weapon only needs the broader
    window (``_is_offhand_attack_swing``)."""
    return _offhand_window_open(current) and current.bonus_action_available


def _attack_action_is_spent(live: _LiveCombat, current: Combatant) -> bool:
    """SRD §Extra Attack — the Attack action is fully spent (and so the
    turn should end after a main-hand attack) only when NO swings remain
    this Action, the actor gets exactly one attack per Action (multi-attack
    actors always keep the turn until their budget is exhausted), and no
    Light off-hand window is open (Bonus-Action-funded OR Nick —
    ``_offhand_window_open``). Flurry of Blows strikes still owed
    keep the turn open the same way: the Focus Point and the Bonus Action
    already paid for them."""
    return (
        current.attacks_remaining <= 0
        and _attacks_per_action(live, current) == 1
        and not _offhand_window_open(current)
        and current.flurry_strikes_remaining <= 0
    )


def _class_levels(c: Combatant | PartyMemberSpec) -> dict[str, int]:
    """Per-class levels: the host's ``classes`` map, else the one class at the
    total character level (every host before 0.6)."""
    if c.classes:
        return dict(c.classes)
    return {c.class_slug: c.character_level} if c.class_slug else {}

def _formula_carriers(c: Combatant) -> dict[str, str | int]:
    """The caster's ScaleValue magnitudes, keyed by the BARE name.

    The token in a Foundry formula carries an ``@scale.`` prefix
    (``@scale.barbarian.rage-damage``), but every reader here strips it and
    looks the rest up: ``activities/formula._resolve_token``,
    ``rules/uses`` (``removeprefix``), ``attack.sneak_attack_dice``
    (``endswith``). The carrier therefore stores the bare key
    (``barbarian.rage-damage``). Measured: with the prefixed spelling kept
    here, every lookup missed and Rage / Sneak Attack raised
    "Unresolved @scale token".
    """
    # BARE keys — the same convention ``activities/formula._resolve_token``
    # (``token[len(_SCALE_PREFIX):]``), ``rules/uses`` (``removeprefix``) and
    # ``attack.sneak_attack_dice`` (``key.endswith``) all read. Storing the
    # prefixed form here made every ``@scale.*`` lookup miss, so Rage and
    # Sneak Attack raised "Unresolved @scale token" on ``use_feature``.
    return dict(_scale_values_of(c))


def _scale_values_of(c: Combatant) -> dict[str, int | str]:
    """``c``'s ``@scale.*`` values: each class and its subclass at that class's
    level, the species at the character level (``build_scale_values``)."""
    return build_scale_values(
        class_slug=c.class_slug,
        subclass_slug=c.subclass_slug,
        species_slug=c.species_slug,
        level=c.character_level,
        loader=get_lib_loader(),
        classes=_class_levels(c),
    )


def _granted_feature_slugs(caster: Combatant) -> frozenset[str]:
    """Feature slugs the caster's class(es) (+ subclass) + species grants at/below
    each source's own level.

    The USE_FEATURE repertoire gate: a PC may only invoke a feature its
    class, subclass, or species ``granted_features`` list grants at a level no
    higher than that source's own level — each class at its own level, the
    subclass at its class's level, the species at character level. A host
    routes both class AND species features through USE_FEATURE, so the
    gate must accept either source. Monsters / casters with no classes and no
    ``species_slug`` grant nothing (empty set ⇒ every USE_FEATURE rejected,
    the correct default).
    """
    owners = feature_owners(
        classes=_class_levels(caster),
        subclass_slug=caster.subclass_slug,
        species_slug=caster.species_slug,
        level=caster.character_level,
        loader=get_lib_loader(),
    )
    # Feature-choice POOLS (Sorcerer Metamagic, ...) never appear in a
    # `granted_features` table, so walking the owner docs cannot find the pick
    # the player made. `chosen_features` carries them from the sheet, and the
    # pick is only honoured when a pool the character actually reaches offers
    # it — a stale slug must not smuggle in an ability (§7).
    offered: set[str] = set()
    for _slug, doc, _level in owners:
        for choice in getattr(doc, "feature_choices", ()) or ():
            for option in getattr(choice, "pool", ()) or ():
                offered.add(option.slug)
    chosen = frozenset(
        slug for slug in getattr(caster, "chosen_features", ()) if slug in offered
    )
    return frozenset(
        leveled_feature_slugs([(doc, level) for _, doc, level in owners])
    ) | chosen


# The kinds of a ``special``-activation activity that costs nothing: it only
# marks or grants (Action Surge's extra action, Sacred Weapon's enchantment).
# Brutal Strike's ``special`` damage rides a Reckless Attack hit; invoked on its
# own it has no attack to ride, so it keeps the Action a feature use costs
# rather than dealing its 1d10 for free, again and again.
_FREE_SPECIAL_ACTIVITY_KINDS: Final[frozenset[str]] = frozenset({"utility", "enchant"})


@dataclass(frozen=True)
class _FeatureInvocation:
    """A USE_FEATURE intent resolved to ONE concrete activity, after the
    repertoire gate + single-activity validation pass.

    ``is_bonus_action`` is read by the action-economy block to decide whether
    the invocation spends the Bonus Action (Rage, Second Wind) or the Action.
    Resolving this BEFORE consuming any budget is the fix for the economy
    ordering bug: a gate-rejected or multi-activity-no-op feature returns
    ``None`` from ``_resolve_feature_invocation`` and spends nothing.
    """

    activities: list[Any]
    passive_effects: list[Any]
    is_bonus_action: bool
    # SRD 5.2 — an activity used as part of something else
    # (``activation.type == "special"``) that resolves nothing by itself
    # (``_FREE_SPECIAL_ACTIVITY_KINDS``: Action Surge, Sacred Weapon) takes no
    # Action, Bonus Action or Reaction and keeps the turn.
    is_free_action: bool = False
    # SRD 5.2 Rage — a ``rage`` invocation by a creature already raging is the
    # Bonus-Action extension, not a new Rage: it applies nothing
    # (``_resolve_intent_activities``) and spends no use (``use_cost`` 0).
    extends_rage: bool = False
    # SRD 5.2 Wild Shape — "You can also leave the form early as a Bonus
    # Action." A ``wild-shape`` invocation without ``form_id`` by a creature in
    # a Wild Shape form: it applies nothing (``_resolve_intent_activities``),
    # spends no use (``use_cost`` 0), and ``_leave_wild_shape`` ends the form.
    leaves_form: bool = False
    # SRD 5.2 §Limited-Use Features — the per-rest use cap resolved
    # from the feature's typed ``uses`` block (a literal or a ``@scale.*`` max
    # resolved against the caster's ScaleValue map), or ``None`` when the feature
    # is uncapped: no ``uses`` block, empty ``max``, or a symbolic ``max`` that
    # cannot be resolved. See ``_feature_use_cap``.
    use_cap: int | None = None
    # Uses this invocation spends — ``_feature_activity_cost``.
    use_cost: int = 1
    # ``@scaling`` for this invocation: the pool points drawn, when the
    # activity scales by amount.
    scaling_value: int | None = None


def _uses_roll_data(caster: Combatant, scale_values: Mapping[str, int | str]) -> UsesRollData:
    """The caster's numbers for a ``uses.max`` formula."""
    return UsesRollData(
        proficiency_bonus=proficiency_bonus_of(caster),
        ability_modifiers={code: ability_modifier_of(caster, code) for code in ABILITY_CODES},
        class_levels=_class_levels(caster),
        scale_values=scale_values,
    )


def _feature_use_cap(feature: Any, roll_data: UsesRollData) -> int | None:
    """Resolve a feature's per-rest use cap from its typed ``uses`` block.

    ``None`` — UNCAPPED, never gated — for no ``uses`` block, an empty
    ``uses.max``, a formula ``rules.uses.evaluate_uses_formula`` can't
    evaluate, or a result below 1 (no SRD feature has zero uses).
    """
    uses = getattr(feature, "uses", None)
    if uses is None:
        return None
    max_raw = str(getattr(uses, "max", "") or "").strip()
    if not max_raw:
        return None
    cap = evaluate_uses_formula(max_raw, roll_data)
    return cap if cap is not None and cap > 0 else None


def _feature_use_counter_key(feature_id: str) -> str:
    """The ``custom_counters`` sidecar key namespacing a feature's use tally."""
    return f"{FEATURE_USE_COUNTER_PREFIX}{feature_id}"


def _feature_use_spent(live: _LiveCombat, entity_id: str, feature_id: str) -> int:
    """Uses of ``feature_id`` already spent by ``entity_id`` this rest cycle."""
    return (
        live.custom_counters_by_entity.get(entity_id, {})
        .get(_feature_use_counter_key(feature_id), {})
        .get("spent", 0)
    )


def _increment_feature_use(
    live: _LiveCombat, entity_id: str, feature_id: str, amount: int = 1
) -> None:
    """Record ``amount`` spent uses of ``feature_id`` on the caster's sidecar counter."""
    counters = live.custom_counters_by_entity.setdefault(entity_id, {})
    counter = counters.setdefault(_feature_use_counter_key(feature_id), {"spent": 0})
    counter["spent"] = counter.get("spent", 0) + amount


#: The sidecar counter that records "uses of Rage were already regained this
#: way" until the next Long Rest — SRD 5.2 Persistent Rage, second sentence:
#: "After you regain uses of Rage in this way, you can't do so again until you
#: finish a Long Rest."
#: it lives under the ``feature_use:`` prefix so ``rest.recover_feature_uses``
#: clears it on a Long Rest exactly as it clears Rage's own uses — the flag
#: is a feature use that a rest must return.
_PERSISTENT_RAGE_REGAIN_KEY: Final = "feature_use:persistent_rage_regained"

#: SRD 5.2 Bard 18 "Superior Inspiration" — the feature whose uses are
#: floored at two when Initiative is rolled.
_SUPERIOR_INSPIRATION_FEATURE: Final = "superior-inspiration"

#: SRD 5.2 Monk 2 "Uncanny Metabolism" and Monk's Focus — the pool it returns.
_UNCANNY_METABOLISM_FEATURE: Final = "uncanny-metabolism"
_MONKS_FOCUS_FEATURE: Final = "monks-focus"

#: SRD 5.2 Monk 15 "Perfect Focus" — floors Focus at four unless Uncanny
#: Metabolism was used on the same Initiative roll.
_PERFECT_FOCUS_FEATURE: Final = "perfect-focus"

#: Marker written by Uncanny Metabolism when it fires on an Initiative roll,
#: read by Perfect Focus in the same `start_combat`. It is NOT a resource and
#: carries no `feature_use:` prefix on purpose: it describes one roll, not a
#: spend a rest could restore. Both features run inside the same call, so the
#: marker never needs clearing between fights.
_UNCANNY_METABOLISM_THIS_ROLL_KEY: Final = "uncanny_metabolism_this_roll"


def _uncanny_metabolism_on_initiative(live: _LiveCombat, rng: random.Random) -> None:
    """SRD 5.2 Uncanny Metabolism (Monk 2): "When you roll Initiative, you can
    regain all expended Focus Points. When you do so, roll your Martial Arts
    die, and regain a number of Hit Points equal to your Monk level plus the
    number rolled. Once you use this feature, you can't use it again until you
    finish a Long Rest."

    The regain is automatic for the same reason Persistent Rage's is (see
    ``_regain_rage_on_initiative``): its only cost is a once-per-rest budget,
    and the engine has no "decline a free regain" intent.

    Spending the feature is recorded BEFORE the heal so a heal that cannot be
    applied — a creature with no `tracked_hp` entry — still cannot use the
    feature twice today. That is deliberate: the book's cost is "once you USE
    this feature", not "once the healing lands".
    """
    for c in live.initiative:
        if c.entity_type != "Character":
            continue
        if _UNCANNY_METABOLISM_FEATURE not in _granted_feature_slugs(c):
            continue
        counters = live.custom_counters_by_entity.setdefault(c.entity_id, {})
        hint = _feature_use_counter_key(_UNCANNY_METABOLISM_FEATURE)
        if counters.get(hint, {}).get("spent", 0):
            continue
        focus_key = _feature_use_counter_key(_MONKS_FOCUS_FEATURE)
        focus_spent = counters.get(focus_key, {}).get("spent", 0)
        if not focus_spent:
            # Nothing to regain. The book's trigger is a choice made when you
            # roll Initiative; spending the once-a-rest budget on a monk who
            # lost nothing would be a strictly worse trade.
            continue
        counters[focus_key] = {"spent": 0}
        counters[hint] = {"spent": 1}
        counters[_UNCANNY_METABOLISM_THIS_ROLL_KEY] = {"spent": 1}
        # ``monk.die`` is the die STRING ("d6" … "d12") the scale value
        # resolves to; a shape other than `d<digits>` is data this engine
        # cannot roll, and guessing a size would be worse than healing
        # nothing but the level's flat part.
        die = str(_scale_values_of(c).get("monk.die") or "")
        digits = die[1:] if die.startswith("d") else ""
        faces = int(digits) if digits.isdigit() else None
        rolled = rng.randint(1, faces) if faces else 0
        healed = c.character_level + rolled
        current_hp = live.tracked_hp.get(c.entity_id)
        if current_hp is None:
            continue
        live.tracked_hp[c.entity_id] = min(current_hp + healed, c.hp_max)
        _update_combatant(
            live, c.entity_id, hp_current=live.tracked_hp[c.entity_id]
        )


def _perfect_focus_on_initiative(live: _LiveCombat) -> None:
    """SRD 5.2 Perfect Focus (Monk 15): "When you roll Initiative and don't use
    Uncanny Metabolism, you regain expended Focus Points until you have 4 if
    you have 3 or fewer."

    The two features are one decision, not two: the monk either spends the
    once-a-rest Uncanny Metabolism (full refill plus healing) or takes this
    floor. Order matters and is fixed by the caller — Uncanny Metabolism
    runs first, and the flag it sets is exactly what suppresses this.

    A floor of four, like Superior Inspiration's floor of two: a monk with
    four or more Focus left is untouched. The pool itself is
    ``@scale.monk.focus`` (Monk level from 2 up), so before level 4 the pool
    is SMALLER than the floor and the floor cannot raise it — the cap wins,
    and ``max(0, cap - 4)`` handles that without a special case.
    """
    for c in live.initiative:
        if c.entity_type != "Character":
            continue
        if _PERFECT_FOCUS_FEATURE not in _granted_feature_slugs(c):
            continue
        counters = live.custom_counters_by_entity.setdefault(c.entity_id, {})
        # "and don't use Uncanny Metabolism" is about THIS Initiative roll, not
        # about the day: a monk who spent Uncanny Metabolism an hour ago still
        # gets the floor now. The once-a-rest counter cannot answer that, so
        # Uncanny Metabolism sets a separate marker for the roll it happened on
        # and `_uncanny_metabolism_on_initiative` clears it at the next one.
        used_uncanny_this_roll = counters.get(
            _UNCANNY_METABOLISM_THIS_ROLL_KEY, {}
        ).get("spent", 0)
        if used_uncanny_this_roll:
            continue
        feature = get_lib_loader().get_feature(_MONKS_FOCUS_FEATURE)
        if feature is None:
            continue
        cap = _feature_use_cap(feature, _uses_roll_data(c, _scale_values_of(c)))
        if not cap:
            continue
        floor_spent = max(0, cap - 4)
        focus_key = _feature_use_counter_key(_MONKS_FOCUS_FEATURE)
        spent = counters.get(focus_key, {}).get("spent", 0)
        if spent <= floor_spent:
            continue
        counters[focus_key] = {"spent": floor_spent}


def _regain_bardic_inspiration_on_initiative(live: _LiveCombat) -> None:
    """SRD 5.2 Superior Inspiration (Bard 18): "When you roll Initiative, you
    regain expended uses of Bardic Inspiration until you have two if you have
    fewer than that."

    A FLOOR, not a refill: a bard with three uses left is untouched, one with
    none comes back to two. The pool's size is whatever `uses.max` resolved to
    for this bard, so a level-5 bard (Charisma +3 → 3 uses) with all three
    spent comes back to ``spent == 1``, not to a full pool.
    """
    for c in live.initiative:
        if c.entity_type != "Character":
            continue
        if _SUPERIOR_INSPIRATION_FEATURE not in _granted_feature_slugs(c):
            continue
        counters = live.custom_counters_by_entity.setdefault(c.entity_id, {})
        counter_key = _feature_use_counter_key(_BARDIC_INSPIRATION)
        spent = counters.get(counter_key, {}).get("spent", 0)
        # The pool is Bardic Inspiration's own `uses.max`
        # (``max(1, @abilities.cha.mod)``), loaded the same way the feature
        # path loads it; a bard whose data is missing has no pool to floor, so
        # the loop leaves the counter alone rather than guessing a size.
        feature = get_lib_loader().get_feature(_BARDIC_INSPIRATION)
        if feature is None:
            continue
        cap = _feature_use_cap(
            feature, _uses_roll_data(c, _scale_values_of(c))
        )
        if not cap:
            continue
        floor_spent = max(0, cap - 2)
        if spent <= floor_spent:
            continue
        counters[counter_key] = {"spent": floor_spent}


def _regain_rage_on_initiative(live: _LiveCombat) -> None:
    """SRD 5.2 Persistent Rage: "When you roll Initiative, you can regain all
    expended uses of Rage." Once per Long Rest per barbarian.

    Runs once at ``start_combat`` — the one place this engine rolls
    Initiative. The regain is automatic rather than offered: it is never
    worse for the barbarian (the second sentence's only cost is the
    once-per-rest budget), and the engine has no "decline a free regain"
    intent. A barbarian without the feature, or one already recorded as
    having regained this way, is untouched — the flag is what makes a second
    combat in the same day a no-op instead of a second regain.
    """
    for c in live.initiative:
        if c.entity_type != "Character":
            continue
        if _PERSISTENT_RAGE_FEATURE not in _granted_feature_slugs(c):
            continue
        counters = live.custom_counters_by_entity.setdefault(c.entity_id, {})
        regained = counters.get(_PERSISTENT_RAGE_REGAIN_KEY) or {}
        if regained.get("spent", 0):
            continue
        counter_key = _feature_use_counter_key(_RAGE_FEATURE)
        rage_counter = counters.get(counter_key)
        if not rage_counter or not rage_counter.get("spent", 0):
            continue
        counters[counter_key] = {"spent": 0}
        counters[_PERSISTENT_RAGE_REGAIN_KEY] = {"spent": 1}

def _feature_spend_fits_cap(
    live: _LiveCombat, actor_id: str, feature_id: str, *, cap: int | None, cost: int
) -> bool:
    """True when spending ``cost`` more uses stays within ``cap`` — the one
    comparison ``_feature_uses_exhausted`` and ``_feature_pool_request_failure``
    both gate on, kept in a single place so it can't drift between them. An
    uncapped feature (``cap is None``) always fits.
    """
    return cap is None or _feature_use_spent(live, actor_id, feature_id) + cost <= cap


def _feature_uses_exhausted(
    live: _LiveCombat,
    actor_id: str,
    feature_id: str,
    feature_invocation: _FeatureInvocation,
) -> bool:
    """SRD §Limited-Use Features — True (after emitting ``CastFailed``) when a
    capped feature's per-rest uses are spent with no intervening rest.

    Emits ``CastFailed(reason="no_uses_remaining")`` — the ``no_action_economy``
    reject shape, extended from a per-turn budget to a per-rest one. Uncapped
    features (``use_cap is None``) never gate.
    """
    cost = feature_invocation.use_cost
    if cost == 0 or _feature_spend_fits_cap(
        live, actor_id, feature_id, cap=feature_invocation.use_cap, cost=cost
    ):
        return False
    _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="no_uses_remaining"))
    return True


def _record_capped_feature_use(
    live: _LiveCombat,
    actor_id: str,
    feature_id: str | None,
    feature_invocation: _FeatureInvocation | None,
) -> None:
    """Increment the per-rest use counter for a committed capped-feature invocation.

    No-op for a non-feature intent (``feature_id`` / ``feature_invocation`` is
    ``None``), an uncapped feature (``use_cap is None``), or a free activity
    (``use_cost == 0`` — Patient Defense's Disengage-only option) — only a
    within-cap, cost-bearing invocation reaches here past the early exhaustion
    gate.
    """
    if (
        feature_id is not None
        and feature_invocation is not None
        and feature_invocation.use_cap is not None
        and feature_invocation.use_cost > 0
    ):
        _increment_feature_use(live, actor_id, feature_id, feature_invocation.use_cost)


def _cross_feature_spends(
    caster: Combatant | None, feature_invocation: _FeatureInvocation | None
) -> list[tuple[str, int]]:
    """``(pool_slug, amount)`` for costs this invocation routes to another pool."""
    if caster is None or feature_invocation is None:
        return []
    activity = next(iter(feature_invocation.activities), None)
    if activity is None:
        return []
    resolved: dict[str, str] = {}
    spends: list[tuple[str, int]] = []
    for foundry_id, value in _cross_feature_cost_targets(
        activity, getattr(feature_invocation, "scaling_value", None)
    ):
        slug = _cross_feature_pool_slug(caster, foundry_id, resolved)
        if slug is None:
            _LOGGER.info(
                "cross_feature_pool_unavailable foundry_id=%s value=%s", foundry_id, value
            )
            continue
        spends.append((slug, value))
    return spends


def _cross_feature_spend_failure(
    live: _LiveCombat,
    caster: Combatant | None,
    feature_invocation: _FeatureInvocation | None,
) -> bool:
    """True (after emitting ``CastFailed``) when a pool this activity spends is
    missing or short — refused, not partially applied (SRD 5.2 Superior Defense
    needs 3 Focus Points; with 2 left the action does not happen and nothing is
    spent).

    The reject MUST be announced: the two sibling gates
    (``_feature_pool_request_failure``, ``_feature_uses_exhausted``) both emit
    ``CastFailed``; this one returned True silently, so a Sorcerer who had
    spent every Sorcery point got no event, no reason and no result from
    ``submit_player_intent`` — the intent simply vanished (§5).
    """
    if caster is None:
        return False
    for slug, value in _cross_feature_spends(caster, feature_invocation):
        feature = get_lib_loader().get_feature(slug)
        if feature is None:
            # A pool the corpus cannot resolve is a data gap, not a spent
            # resource — but the activity still cannot be paid for, so it is
            # refused loudly rather than applied for free.
            _emit(live, CastFailed(
                actor_id=caster.entity_id, spell_id="", reason="no_uses_remaining",
            ))
            return True
        cap = _feature_use_cap(feature, _uses_roll_data(caster, _scale_values_of(caster)))
        if not cap or value > cap - _feature_use_spent(live, caster.entity_id, slug):
            _emit(live, CastFailed(
                actor_id=caster.entity_id, spell_id="", reason="no_uses_remaining",
            ))
            return True
    return False


def _record_cross_feature_spend(
    live: _LiveCombat,
    caster: Combatant | None,
    feature_invocation: _FeatureInvocation | None,
) -> None:
    """Charge the pools a committed invocation named (see `_cross_feature_spends`)."""
    for slug, value in _cross_feature_spends(caster, feature_invocation):
        if caster is not None:
            _increment_feature_use(live, caster.entity_id, slug, value)


def _cross_feature_restores(
    caster: Combatant | None, feature_invocation: _FeatureInvocation | None
) -> list[tuple[str, int]]:
    """``(pool_slug, amount)`` a committed invocation gives back to another pool."""
    if caster is None or feature_invocation is None:
        return []
    activity = next(iter(feature_invocation.activities), None)
    if activity is None:
        return []
    level = int(getattr(caster, "character_level", 0) or 0)
    resolved: dict[str, str] = {}
    restores: list[tuple[str, int]] = []
    for foundry_id, amount in _cross_feature_restore_targets(activity, level):
        slug = _cross_feature_pool_slug(caster, foundry_id, resolved)
        if slug is None:
            _LOGGER.info(
                "cross_feature_restore_unavailable foundry_id=%s amount=%s", foundry_id, amount
            )
            continue
        restores.append((slug, amount))
    return restores


def _record_cross_feature_restore(
    live: _LiveCombat,
    caster: Combatant | None,
    feature_invocation: _FeatureInvocation | None,
) -> None:
    """Give spent uses back to the pools a committed invocation names.

    SRD 5.2 Sorcerous Restoration: "regain a number of expended Sorcery
    Points equal to half your Sorcerer level (round down)". The floor of zero
    and the cap are both enforced here — a pool cannot go below nothing spent
    and never above its maximum."""
    for slug, amount in _cross_feature_restores(caster, feature_invocation):
        if caster is None:
            continue
        counters = live.custom_counters_by_entity.setdefault(caster.entity_id, {})
        key = _feature_use_counter_key(slug)
        spent = counters.get(key, {}).get("spent", 0)
        if spent <= 0:
            continue
        counters[key] = {"spent": max(0, spent - amount)}


def _record_rage_extension(
    live: _LiveCombat, actor_id: str, feature_invocation: _FeatureInvocation | None
) -> None:
    """Mark a committed Bonus-Action Rage extension for this turn's
    ``engine:rage-extension`` hook; a no-op for every other intent."""
    if feature_invocation is not None and feature_invocation.extends_rage:
        live.rage_bonus_extensions.add(actor_id)


def _leave_wild_shape(
    live: _LiveCombat, actor_id: str, feature_invocation: _FeatureInvocation | None
) -> None:
    """End the form of a committed Bonus-Action Wild Shape leave; a no-op for
    every other intent."""
    if feature_invocation is not None and feature_invocation.leaves_form:
        _end_wild_shape(live, actor_id, "remove_ieffect")


def _item_use_counter_key(item_id: str) -> str:
    """The ``custom_counters`` sidecar key namespacing an item's charge tally."""
    return f"{ITEM_USE_COUNTER_PREFIX}{item_id}"


def _activity_item_use_cost(item_slug: str, activity: Any) -> int:
    """Positive literal ``itemUses`` cost of ONE activity.

    Symbolic or negative targets (``-3d4``, ``-@item.uses.spent``,
    ``@item.uses.max``) are recharge/whole-pool semantics, not a spend
    cost — skipped, never coerced.
    """
    cost = 0
    for target in activity.consumption.targets:
        if target.type != "itemUses":
            continue
        try:
            value = int(str(target.value).strip())
        except ValueError:
            _LOGGER.info("item_charge_target_symbolic slug=%s value=%r", item_slug, target.value)
            continue
        if value > 0:
            cost += value
    return cost


def _own_pool_targets(activity: Any) -> list[Any]:
    """The activity's ``itemUses`` consumption targets on its OWN feature's pool
    (an empty ``target``); a target naming another feature (Stunning Strike →
    Monk's Focus) spends a different pool."""
    return [t for t in activity.consumption.targets if t.type == "itemUses" and not t.target]


def _literal_int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


#: Foundry prefixes a cross-feature use-cost target with the pack it lives in:
#: ``Compendium.dnd5e.classes24.Item.phbmnkMonksFocus``. Only the trailing id
#: is data this engine can resolve; the pack names the book, not the rule.
_COMPENDIUM_ITEM_PREFIX: Final = "Item."


def _scaled_item_use_cost(raw: str, scaling_value: int | None) -> int | None:
    """A use-cost that the corpus writes as ``N * @scaling`` (SRD 5.2 Restoring
    Touch spends "5 Lay on Hands points"), or a plain integer. ``None`` for
    anything else — a symbolic recharge is not a spend and is never coerced.

    ``@scaling`` is the activity's own sub-amount (5 points of Lay on Hands),
    resolved by the caller exactly as the damage path resolves it."""
    text = str(raw).strip()
    literal = _literal_int(text)
    if literal is not None:
        return literal
    factor, _, token = text.partition("*")
    if token.strip() != "@scaling" or scaling_value is None:
        return None
    multiplier = _literal_int(factor)
    if multiplier is None:
        return None
    return multiplier * scaling_value


def _cross_feature_cost_targets(
    activity: Any, scaling_value: int | None = None
) -> list[tuple[str, int]]:
    """``(foundry_id, value)`` for the activity's use-cost entries naming ANOTHER
    feature's pool (SRD 5.2 Superior Defense spends 3 Focus Points, which live on
    Monk's Focus; Stunning Strike spends 1). Literal positive values only — a
    symbolic value is a recharge, not a spend, and is left to
    ``_activity_item_use_cost``'s symbolic path.
    """
    targets: list[tuple[str, int]] = []
    for target in activity.consumption.targets:
        if target.type != "itemUses" or not target.target:
            continue
        value = _scaled_item_use_cost(target.value, scaling_value)
        if value is None or value <= 0:
            continue
        targets.append((str(target.target).split(_COMPENDIUM_ITEM_PREFIX)[-1], value))
    return targets


def _cross_feature_restore_targets(activity: Any, caster_level: int) -> list[tuple[str, int]]:
    """``(foundry_id, amount)`` for entries that RESTORE another pool.

    The sign is the direction: Superior Defense writes a positive ``3`` and
    spends; Sorcerous Restoration writes ``-(floor(@classes.sorcerer.levels
    / 2))`` and gives back. Only the ``@classes.<class>.levels`` shape is
    evaluated here — it is the one form the data uses for a restoration, and
    guessing any other formula would invent a number the source never states.
    """
    restores: list[tuple[str, int]] = []
    for target in activity.consumption.targets:
        if target.type != "itemUses" or not target.target:
            continue
        raw = str(target.value).strip()
        if not raw.startswith("-"):
            continue
        # The data writes `-(floor(...))` — the minus is OUTSIDE the call, so
        # both it and the wrapping parentheses are stripped before parsing.
        inner = raw[1:].strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1].strip()
        amount = _literal_int(inner)
        if amount is None:
            if not (inner.startswith("floor(") and inner.endswith(")")):
                continue
            if "@classes." not in inner or ".levels" not in inner:
                continue
            divisor = 1
            if "/" in inner:
                divisor_text = inner.split("/", 1)[1].rstrip(")").strip()
                divisor = _literal_int(divisor_text) or 1
            amount = caster_level // divisor
        if amount <= 0:
            continue
        restores.append((str(target.target).split(_COMPENDIUM_ITEM_PREFIX)[-1], amount))
    return restores


def _cross_feature_pool_slug(
    caster: Combatant, foundry_id: str, resolved: dict[str, str]
) -> str | None:
    """The slug of a granted feature whose data carries ``foundry_id``.

    The id is resolved by asking the loader for each of the caster's granted
    features and comparing the id in ITS data — never by guessing a slug from
    the id (``phbmnkMonksFocus`` is not ``monks-focus`` in any obvious way the
    engine should assume). A pool the caster does not have is not spendable.
    """
    if foundry_id in resolved:
        return resolved[foundry_id] or None
    result: str | None = None
    for slug in sorted(_granted_feature_slugs(caster)):
        feature = get_lib_loader().get_feature(slug)
        if feature is None:
            continue
        # `foundry_id` is the dataset field that records the very id the
        # cross-feature target names; nothing is inferred from the name.
        if getattr(feature, "foundry_id", None) == foundry_id:
            result = slug
            break
    resolved[foundry_id] = result or ""
    return result


def _feature_activity_cost(
    activities: Sequence[Any], activity: Any, *, scaling_value: int | None
) -> int:
    """Uses one invocation of ``activity`` spends from its feature's pool.

    Foundry ``consumption.targets``: each literal ``itemUses`` value on the
    feature's own pool, plus one per step above the first when the target
    scales by amount (Lay on Hands' Heal spends the points it draws). An
    activity declaring no own-pool cost is free when a sibling activity does
    declare one — SRD 5.2 Patient Defense: "You can take the Disengage action as
    a Bonus Action. Alternatively, you can expend 1 Focus Point…" — and
    otherwise spends one use: a lone activity with no declared cost
    (Indomitable) is Foundry leaving the decrement to the player, and the SRD
    still limits the feature. A formula cost the engine can't evaluate (Font of
    Magic's slot conversion) spends one use.
    """
    own = _own_pool_targets(activity)
    # A cost the data routes to ANOTHER feature's pool (Superior Defense →
    # Monk's Focus) is still a cost: the activity's own pool is empty by
    # construction, so counting only `own` silently made those activities
    # free. `_cross_feature_cost_targets` names them; the caller spends them
    # out of the pool they name (`_cross_feature_spends`).
    if not own:
        # A cross-feature cost (Superior Defense → Monk's Focus) is charged to
        # THAT pool by `_record_cross_feature_spend`, and its availability is
        # gated by `_cross_feature_spend_failure` — both read the SAME
        # `_cross_feature_cost_targets`, so this function returns the feature's
        # own-pool cost only. Counting the foreign cost here as well would
        # refuse an affordable Superior Defense against an unrelated
        # per-feature cap.
        declared_elsewhere = any(
            _own_pool_targets(a) or _cross_feature_cost_targets(a)
            for a in activities
            if a is not activity
        )
        return 0 if declared_elsewhere else 1
    cost = 0
    for target in own:
        value = _literal_int(target.value)
        if value is None:
            _LOGGER.info(
                "feature_use_cost_symbolic activity_id=%s value=%r", activity.id, target.value
            )
            return 1
        if value <= 0:
            continue
        steps = scaling_value - 1 if scaling_value and target.scaling.mode == "amount" else 0
        cost += value + steps
    return cost


def _scales_by_amount(activity: Any) -> bool:
    """True when the caster chooses how many own-pool uses to spend (Foundry
    ``consumption.scaling`` with an ``amount`` target) — Lay on Hands' Heal,
    whose ``@scaling`` is the points drawn."""
    if not activity.consumption.scaling.allowed:
        return False
    if any(
        target.scaling.mode == "amount" and _literal_int(target.value) is not None
        for target in _own_pool_targets(activity)
    ):
        return True
    # SRD 5.2 Restoring Touch spends from ANOTHER pool — 5 Lay on Hands points,
    # written as `5 * @scaling` against the Lay on Hands item. The corpus marks
    # that pool spendable by amount, so the activity scales by amount even
    # though the target is not its own pool.
    return any(
        _scaled_item_use_cost(target.value, 1) is not None
        and "@scaling" in str(target.value)
        for target in activity.consumption.targets
        if target.type == "itemUses" and target.target
    )


def _feature_pool_request_failure(
    live: _LiveCombat,
    actor_id: str,
    intent: PlayerIntent,
    feature_invocation: _FeatureInvocation,
) -> bool:
    """True (after emitting ``CastFailed(reason="invalid_charge_spend")``) when
    ``pool_points`` names an activity that doesn't scale by amount, or asks for
    more than the pool has left — SRD 5.2 Lay on Hands: "up to the maximum
    amount remaining in the pool"."""
    if intent.pool_points is None or intent.feature_id is None:
        return False
    fits = _feature_spend_fits_cap(
        live,
        actor_id,
        intent.feature_id,
        cap=feature_invocation.use_cap,
        cost=feature_invocation.use_cost,
    )
    if feature_invocation.scaling_value is not None and fits:
        return False
    _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="invalid_charge_spend"))
    return True


def _item_charge_activity(item: Any, activity_id: str | None) -> Any | None:
    """The activity ``_item_charge_cost`` prices for this intent: the
    explicitly selected activity, else the first consuming activity
    (Foundry activities are alternative invocations, never a batch).

    Shared with charge-scaling validation so the cost and the scaling
    ceiling are always evaluated against the SAME activity.
    """
    if activity_id:
        for activity in item.activities:
            if activity.id == activity_id:
                return activity
        return None
    consuming = [
        activity for activity in item.activities if _activity_item_use_cost(item.slug, activity) > 0
    ]
    if len(consuming) > 1:
        _LOGGER.warning(
            "item_charge_ambiguous_activity slug=%s charging_first_of=%d",
            item.slug,
            len(consuming),
        )
    return consuming[0] if consuming else None


def _item_charge_cost(item: Any, activity_id: str | None) -> int:
    """Cost of ONE invocation: the selected activity's cost, else the first
    consuming activity's (Foundry activities are alternative invocations,
    never a batch)."""
    activity = _item_charge_activity(item, activity_id)
    if activity is None:
        return 0
    return _activity_item_use_cost(item.slug, activity)


_CONSUMPTION_MAX_MIN_RE = re.compile(r"^min\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)$")


def _eval_consumption_max(expr: str, *, remaining: int, cap: int | None) -> int | None:
    """Evaluate the closed ``consumption.scaling.max`` grammar of the SRD item corpus."""
    text = expr.strip()
    if not text:
        return None

    def _term(tok: str) -> int | None:
        tok = tok.strip()
        if tok == "@item.uses.value":
            return remaining
        if tok == "@item.uses.max":
            return cap
        try:
            return int(tok)
        except ValueError:
            return None

    match = _CONSUMPTION_MAX_MIN_RE.match(text)
    if match:
        left, right = _term(match.group(1)), _term(match.group(2))
        if left is None or right is None:
            _LOGGER.warning("consumption_max_unparseable expr=%r", expr)
            return None
        return min(left, right)
    value = _term(text)
    if value is None:
        _LOGGER.warning("consumption_max_unparseable expr=%r", expr)
    return value


def _item_charge_cap(item: Any) -> int | None:
    """Resolve an item's charge pool cap from its typed ``uses`` block.

    ``None`` — meaning UNCAPPED, never gated — for an item with no ``uses``
    block or an unparseable ``max``. Mirrors ``_feature_use_cap``'s
    fail-open contract: a capped resource is never wrongly rejected.
    """
    uses = getattr(item, "uses", None)
    if uses is None or not uses.max:
        return None
    try:
        return int(str(uses.max).strip())
    except ValueError:
        _LOGGER.warning("item_charge_cap_unparseable slug=%s max=%r", item.slug, uses.max)
        return None


def _item_charges_spent(live: _LiveCombat, entity_id: str, item_id: str) -> int:
    """Charges of ``item_id`` already spent by ``entity_id`` this rest cycle."""
    return (
        live.custom_counters_by_entity.get(entity_id, {})
        .get(_item_use_counter_key(item_id), {})
        .get("spent", 0)
    )


def _resolve_charge_request(
    live: _LiveCombat,
    actor_id: str,
    item_id: str,
    item: Any,
    intent: PlayerIntent,
    base_cost: int,
    cap: int,
) -> int | None:
    """Validate ``intent.charges_to_spend`` against the SAME activity
    ``_item_charge_cost`` priced. Returns the effective (requested) cost, or
    ``None`` after emitting ``CastFailed(reason="invalid_charge_spend")``
    when the activity doesn't allow scaling or the request is out of the
    evaluated bounds.

    I1: scaling is only honored on a ``kind == "cast"`` activity. Charge
    scaling on a non-cast activity (staff-of-striking's damage, ring-of-
    the-ram's attack) would need "extra dice per charge" semantics the
    resolver doesn't implement — honest reject rather than silently
    charging N and delivering the base effect.

    I3: also rejected when ``base_cost <= 0`` — an activity whose only
    ``itemUses`` targets are negative/symbolic (recharge, not spend; see
    ``_activity_item_use_cost``) has nothing for ``charges_to_spend`` to
    scale against (rope-of-climbing's scaling utility activity), so a
    request there would deduct charges for no effect. Already implied for
    most cases by the cast-only rule above (those are ``kind == "utility"``),
    but kept as an explicit guard — cheap, and a belt for host content packs
    whose "cast" activities might ship a zero/negative cost.
    """
    requested = intent.charges_to_spend
    activity = _item_charge_activity(item, intent.activity_id)
    scaling = activity.consumption.scaling if activity is not None else None
    if (
        scaling is None
        or not scaling.allowed
        or getattr(activity, "kind", "") != "cast"
        or base_cost <= 0
    ):
        _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="invalid_charge_spend"))
        return None
    remaining = cap - _item_charges_spent(live, actor_id, item_id)
    ceiling = _eval_consumption_max(scaling.max, remaining=remaining, cap=cap)
    upper_bound = min(ceiling, remaining) if ceiling is not None else remaining
    if requested is None or requested < base_cost or requested > upper_bound:
        _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="invalid_charge_spend"))
        return None
    return requested


# Activation types that spend the turn's economy. An activity carrying none of
# them (a bare "enchant" rider, or an empty type) is a passive description,
# not something the player spends an action on.
_COSTLY_ACTIVATIONS = frozenset({'action', 'bonus', 'reaction'})


def _named_activity(item: Any, activity_id: str | None) -> Any | None:
    """The activity ``activity_id`` names, else the item's one unambiguous activity.

    Used where the charge pool cannot answer (an activation that consumes no
    charges). The fallback NEVER guesses between differently priced
    activities: the old resolve-all path ran every activity while a single
    one was priced, so a reaction could execute for an ordinary action and a
    bonus activity could run after the action was spent.

    An activity without a cost (``activation.type`` empty or unknown) is a
    passive rider -- ``oathbow`` carries both "Attack Sworn Enemy" (an
    action) and a bare "Oathbow" enchantment. Only activities that actually
    cost something are candidates; if exactly one is left, or all of them cost
    the same thing, that one is what a bare ``use_item`` means. Anything else
    is genuinely ambiguous and returns ``None``.
    """
    activities = list(getattr(item, "activities", ()) or ())
    if activity_id:
        for activity in activities:
            if activity.id == activity_id:
                return activity
        return None
    if not activities:
        return None
    costly = [
        activity
        for activity in activities
        if getattr(getattr(activity, "activation", None), "type", None) in _COSTLY_ACTIVATIONS
    ]
    if len(activities) == 1 or len(costly) == 1:
        return costly[0] if costly else activities[0]
    costs = {getattr(activity.activation, 'type', None) for activity in costly}
    if len(costs) == 1:
        return costly[0]
    return None


def _item_needs_attunement_unmet(live: _LiveCombat, actor_id: str, item: Any) -> bool:
    """SRD 5.2 §Attunement -- True (after emitting ``CastFailed``) when the item
    requires attunement and the actor has not attuned to it.

    "Attunement: Some magic items require a creature to form a bond with them
    before their magical properties can be used." Using such an item is a
    magical property, so the activation is refused rather than silently
    granted -- without this, a character who never attuned could click a
    stranger's Boots of Speed and still double their Speed.
    """
    if not getattr(item, "requires_attunement", False):
        return False
    actor = _find_combatant(live, actor_id)
    attuned = tuple(getattr(actor, "attuned_items", ()) or ()) if actor is not None else ()
    if item.slug in attuned:
        return False
    _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="not_attuned"))
    return True


ACTIVITY_USE_COUNTER_PREFIX = "activity_use:"


def _spell_pool_counter_key(spell_id: str) -> str:
    """Counter key of the charge pool a spell carries (``@item.uses.value``)."""
    return f"spell_pool:{spell_id}"


def _item_uses_left(
    live: _LiveCombat,
    c: Combatant,
    item,
    intent: PlayerIntent,
    activities: Sequence[Any],
) -> int | None:
    """``@item.uses.value`` for a SPELL resolution: the charges still in play.

    A spell has no item, so its own ``@item.uses.value`` is the pool the spell
    itself has left. SRD 5.2 Delayed Blast Fireball: "Create Bead" spends one
    bead from ``@item.uses.value`` and the bead's pile lives on the spell's
    "Trigger Explosion" activity, which reads ``(@item.uses.value)d6``; SRD 5.2
    Tsunami reads the same pool. For an ITEM cast this is the item's own charge
    pool (``_item_cap_of`` minus what was spent); ``None`` when it states no cap.
    """
    if item is not None:
        cap = _item_cap_of(item)
        if cap is None:
            return None
        return max(0, cap - _item_charges_spent(live, c.entity_id, item.slug))
    spell = get_lib_loader().get_spell(intent.spell_id or "")
    if spell is None:
        return 0
    # SRD 5.2 Tsunami drives its own pool down (the wall lasts a minute and
    # deals "(@item.uses.value)d10"); Delayed Blast Fireball's bead pile lives
    # on its "Trigger Explosion" activity. Both read the spell's own pool, so
    # the value is the spell-level pool, not one activity's consumption.
    left = _spell_pool_left(live, c.entity_id, intent.spell_id or "spell")
    return 0 if left is None else left

def _item_uses_cap(
    live: _LiveCombat,
    c: Combatant,
    item,
    intent: PlayerIntent,
    activities: Sequence[Any],
) -> int | None:
    """``@item.uses.max`` — the item cap, else the spell's own bead pool."""
    if item is not None:
        return _item_cap_of(item)
    return _item_uses_left(live, c, item, intent, activities)

def _activity_consumes_spell_pool(activity: Any) -> bool:
    """True when the activity spends one charge of the spell own pool.

    SRD 5.2 Delayed Blast Fireball Create Bead: the consumption names
    @item.uses.value as the target of an itemUses spend. The cast that
    carries it is what creates a bead, so that is where the pool is credited.
    """
    consumption = getattr(activity, "consumption", None)
    for target in getattr(consumption, "targets", None) or ():
        if getattr(target, "type", None) == "itemUses":
            value = str(getattr(target, "value", "")).strip()
            if value in ("@item.uses.value", "@item.uses.max"):
                return True
    return False


def _spell_pool_charge_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> bool:
    """Refuse a cast that reads the spell own pool with no charge in play.

    SRD 5.2 Delayed Blast Fireball: "Trigger Explosion" needs the bead the
    spell created. The refusal comes BEFORE the action and the slot are spent,
    so a missing bead leaves no half-applied cast (§9). Returns True when a
    refusal was emitted.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id:
        return False
    spell = get_lib_loader().get_spell(intent.spell_id)
    if spell is None or _spell_pool_activity(spell) is None:
        return False
    activity = _pool_activity(intent, tuple(spell.activities or ()))
    if activity is None or not _reads_spell_pool(activity):
        return False
    if _activity_consumes_spell_pool(activity):
        # Creating a charge never needs one.
        return False
    if (_spell_pool_left(live, current.entity_id, spell.slug) or 0) > 0:
        return False
    _emit(
        live,
        CastFailed(
            actor_id=current.entity_id,
            spell_id=intent.spell_id,
            reason="no_uses_remaining",
        ),
    )
    _end_turn_and_advance(live, current.entity_id)
    return True


def _spell_pool_hooks(live: _LiveCombat, caster_id: str, intent: PlayerIntent, spell: Any) -> None:
    """SRD 5.2 Delayed Blast Fireball: keep the spell own bead pool
    (``@item.uses.value``) in step with the casts that happened.

    The casting activity (``9yLyrnGNwM8EL6Uy``, Create Bead) CONSUMES a
    charge and credits one; the activity that RESOLVES the pile
    (``M15GlfjeWy7Cdiqn``, Trigger Explosion) reads it and spends one of the
    beads already in play, crediting nothing of its own - so the pile really
    shrinks and a spent pile cannot be detonated twice. The two are told
    apart by the activity the intent actually resolved to, not by the spell:
    Create Bead submits the id of the resolved activity too, so assuming one
    id for the whole spell would credit the detonation a bead it never made.
    A caster with no bead in play is creating one - the detonating activity
    against an empty pile is refused earlier by
    ``_spell_pool_charge_failure``, before the action or any slot.
    """
    payer = _spell_pool_activity(spell)
    if payer is None:
        return
    bucket = _spell_pool_bucket(live, caster_id, spell.slug)
    credits = int(bucket.get("credits", 0))
    spent = int(bucket.get("spent", 0))
    chosen = _match_activity(tuple(spell.activities or ()), intent.activity_id)
    resolves = chosen is not None and _reads_spell_pool(chosen)
    if resolves and credits - spent > 0:
        bucket["spent"] = spent + 1
    else:
        bucket["credits"] = credits + 1

def _match_activity(activities: Sequence[Any], activity_id: str | None) -> Any | None:
    """The activity of ``activities`` whose id is ``activity_id``.

    Ids arrive from the corpus verbatim, so a plain equality match is enough;
    a missing id on the corpus side (some activities carry no id) never
    matches and the caller treats that as "no activity selected".
    """
    if not activity_id:
        return None
    for activity in activities:
        if getattr(activity, "id", None) == activity_id:
            return activity
    return None


def _spell_pool_bucket(
    live: _LiveCombat, entity_id: str, spell_id: str
) -> dict[str, int]:
    """The per-entity charge bookkeeping of one spell own pool."""
    key = _spell_pool_counter_key(spell_id)
    per_entity = live.custom_counters_by_entity.setdefault(entity_id, {})
    bucket = per_entity.setdefault(key, {})
    if not isinstance(bucket, dict):
        bucket = {}
        per_entity[key] = bucket
    bucket.setdefault("credits", 0)
    bucket.setdefault("spent", 0)
    return bucket


# A cast of Delayed Blast Fireball runs the same spell's activities through
# more than one activation, so the bead-accounting functions below must be
# idempotent per (cast, direction): the bead-creation cast credits once and
# the detonating cast spends once, no matter how many activities of the same
# cast reach the emitter.
def _spell_pool_left(live: _LiveCombat, entity_id: str, spell_id: str) -> int | None:
    """Beads of ``spell_id`` still in play, or ``None`` when none was created.

    SRD 5.2 Delayed Blast Fireball: a bead pile nobody created is ABSENT, not
    empty — the explosion is refused (``no_uses_remaining``) rather than
    rolled at zero. A spell whose formula reads ``(@item.uses.value)d10``
    (Tsunami) is handed a definite 0 by ``_item_uses_left`` instead, because
    there the engine describes a wave that carries nothing, not a missing
    number (#1: the two meanings are written in the docstrings, not guessed).
    """
    bucket = live.custom_counters_by_entity.get(entity_id, {}).get(
        _spell_pool_counter_key(spell_id)
    )
    if bucket is None:
        return None
    return max(0, int(bucket.get("credits", 0)) - int(bucket.get("spent", 0)))

def _spell_pool_activity(spell) -> Any | None:
    """The activity whose consumption names the spell own charge pool.

    SRD 5.2 Delayed Blast Fireball: Create Bead spends one charge of
    @item.uses.value, and the pile lives on Trigger Explosion, which reads
    (@item.uses.value)d6. Whoever carries the consumption owns the pool.
    """
    for activity in tuple(getattr(spell, "activities", None) or ()):
        if _activity_consumes_spell_pool(activity):
            return activity
    return None

def _reads_spell_pool(activity: Any) -> bool:
    """True when a formula of ``activity`` reads the spell own charge pool."""
    blob = repr(activity)
    return "@item.uses.value" in blob or "@item.uses.max" in blob

def _pool_activity(intent: PlayerIntent, activities: Sequence[Any]) -> Any | None:
    """The resolved activity whose own ``uses`` pool ``@item.uses.value`` reads."""
    for activity in activities:
        if getattr(activity, "id", None) == intent.activity_id:
            return activity
    if len(activities) == 1:
        return activities[0]
    return None

def _activity_use_counter_key(item_id: str, activity: Any) -> str:
    """The custom_counters key holding ONE activity own charge tally.

    ``activity`` is either an activity object or the id the intent named;
    both address the same counter, so a spend and a read never disagree
    about the key (this used to be two definitions with two key shapes).
    """
    activity_id = activity if isinstance(activity, str) else getattr(activity, "id", "")
    return f"{ACTIVITY_USE_COUNTER_PREFIX}{item_id}::{activity_id or ''}"


def _activity_uses_left(live: _LiveCombat, entity_id: str, item_id: str, activity) -> int | None:
    """Charges left on the activity own uses counter (None = uncapped).

    Foundry keeps a single-use activity pool on the ACTIVITY
    (activity.uses.max), separate from the item pool.
    """
    counters = live.custom_counters_by_entity.get(entity_id, {})
    bucket = counters.get(_activity_use_counter_key(item_id, activity))
    if bucket is not None and "max" in bucket:
        return max(0, int(bucket["max"]) - int(bucket.get("spent", 0)))
    try:
        value = int(str(getattr(getattr(activity, "uses", None), "max", "") or "").strip())
    except ValueError:
        value = 0
    return value if value > 0 else None

def _item_cap_of(item) -> int | None:
    """The charge cap an ITEM declares (`uses.max`), or None when it states none.

    Foundry keeps an item's own pool on `Item.uses.max`; an activity-level
    `uses` block is a different counter (`_activity_use_cap`). Only a literal
    positive integer is a cap — `@item.uses.value` and friends are the item
    describing itself and cannot resolve to a cap.
    """
    raw = str(getattr(getattr(item, "uses", None), "max", "") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None

def _activity_use_cap(activity):
    uses = getattr(activity, "uses", None)
    raw = str(getattr(uses, "max", "") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _activity_is_narrative(activity) -> bool:
    """True when the activity kind has no mechanical outcome in the source.

    Mirrors `activities/resolver.py`: `summon`, `enchant` and `transform`
    resolve only through a pre-validated `ConjurationCarrier` (an allowlisted
    source plus the input its kind needs), and `utility` applies effect riders
    only when it carries `effects[]`. Everything else in those kinds is a
    narrative passthrough there — so for `use_item` it must be a named
    refusal, not a silent no-op that still spends the Action.

    A `utility` activity WITH riders is mechanical (Bless, Hunter's Mark), so
    it is not narrative; the carrier-backed kinds are never narrative on the
    spell path, which is why this predicate is only consulted for `use_item`.
    """
    kind = getattr(activity, "kind", None)
    if kind in ("summon", "enchant", "transform"):
        return True
    if kind == "utility":
        return not (getattr(activity, "effects", None) or ())
    return False


def _delegated_spell_activities(activity: Any) -> list[Any]:
    """Активности заклинания, на которое ссылается `cast`-активность.

    Предмет часто лишь ПЕРЕДАЁТ каст: Dragon Orb -> Daylight (одна активность
    `utility` без effects), Chime of Opening -> Knock. Тогда механический
    исход определяется делегированным заклинанием, а не видом обёртки.
    Список пуст, если ссылка не активность `cast` или заклинание не найдено
    в корпусе (последнее — законная причина отказа, а не тишины).
    """
    if getattr(activity, "kind", None) != "cast":
        return []
    spell_ref = getattr(activity, "spell", None)
    uuid = getattr(spell_ref, "uuid", "") if spell_ref is not None else ""
    if not uuid:
        return []
    try:
        spell = get_lib_loader().get_spell_by_uuid(uuid)
    except Exception:  # корпус без этого заклинания — не повод падать
        return []
    return list(getattr(spell, "activities", ()) or ()) if spell is not None else []


def _activity_is_narrative_or_delegated(activity: Any) -> bool:
    """Как `_activity_is_narrative`, но `cast` судится по заклинанию-цели.

    Обёртка над неразрешимой ссылкой считается нарративной: молчаливого
    успеха не будет ни в одном случае.
    """
    if getattr(activity, "kind", None) == "cast":
        inner = _delegated_spell_activities(activity)
        return True if not inner else all(_activity_is_narrative(a) for a in inner)
    return _activity_is_narrative(activity)


def _conjures_a_combatant(activity: Any, intent: PlayerIntent) -> bool:
    """True when `activities/conjuration.py` can really perform this activity.

    An allowlisted source resolves through a pre-validated
    `ConjurationCarrier` - a fighter joins the initiative order, a weapon is
    enchanted, a Beast form is taken - and the activity kind must be the one
    the allowlist names. That is mechanics, not a narrative passthrough, and
    the narrative gate must not swallow it. An allowlisted source whose input
    is missing still gets its own named refusal from
    `_conjuration_gate_failure`.
    """
    source = (
        intent.spell_id
        if intent.intent_type == "cast_spell"
        else intent.feature_id
        if intent.intent_type == "use_feature"
        else intent.item_id
        if intent.intent_type == "use_item"
        else None
    ) or ""
    kind = CONJURATION_ALLOWLIST.get(source)
    return kind is not None and getattr(activity, "kind", None) == kind


def _activity_uses_spent(live, entity_id, item_id, activity_id):
    return (
        live.custom_counters_by_entity.get(entity_id, {})
        .get(_activity_use_counter_key(item_id, activity_id), {})
        .get("spent", 0)
    )


def _activity_uses_cost(activity):
    cost = 0
    for target in getattr(getattr(activity, "consumption", None), "targets", ()) or ():
        if target.type != "activityUses":
            continue
        try:
            value = int(str(target.value).strip())
        except ValueError:
            continue
        if value > 0:
            cost += value
    return cost


def _activity_use_activity(item, activity_id):
    if not activity_id:
        return None
    for activity in getattr(item, "activities", ()) or ():
        if getattr(activity, "id", None) == activity_id:
            return activity
    return None


def _activity_use_gate(live, actor_id, intent, item):
    activity = _activity_use_activity(item, intent.activity_id)
    if activity is None:
        return False
    cap = _activity_use_cap(activity)
    if cap is None:
        return False
    cost = _activity_uses_cost(activity)
    if cost <= 0:
        return False
    spent = _activity_uses_spent(live, actor_id, intent.item_id, intent.activity_id)
    if spent + cost > cap:
        _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="no_charges_remaining"))
        return True
    return False


def _record_activity_use_spend(live, actor_id, intent, item):
    activity = _activity_use_activity(item, intent.activity_id)
    if activity is None or _activity_use_cap(activity) is None:
        return
    cost = _activity_uses_cost(activity)
    if cost <= 0:
        return
    counters = live.custom_counters_by_entity.setdefault(actor_id, {})
    counter = counters.setdefault(
        _activity_use_counter_key(intent.item_id, intent.activity_id), {"spent": 0}
    )
    counter["spent"] = counter.get("spent", 0) + cost

def _item_activity_ambiguous(
    live: _LiveCombat, actor_id: str, item: Any, activity_id: str | None
) -> bool:
    """True (after emitting ``CastFailed``) when ``use_item`` names no activity
    the engine can pick without guessing.

    An item whose activities are all free riders of different kinds (the six
    beads of ``necklace-of-prayer-beads`), or several priced differently
    (``instant-fortress`: an action and a bonus), cannot be resolved from
    "use this item" alone. Refusing here -- before the action is spent -- is
    the honest answer; resolving nothing later would silently swallow the
    turn's budget.
    """
    if not getattr(item, "activities", None):
        return False
    if _item_charge_activity(item, activity_id) or _named_activity(item, activity_id):
        return False
    _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="activity_ambiguous"))
    return True


def _item_charge_gate(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> bool:
    """True (after emitting ``CastFailed``) when a capped item's remaining
    charges cannot cover this invocation's cost — reject before any budget
    is consumed. Items with no ``uses`` pool (``cap is None``) never gate,
    preserving today's fully ungated ``use_item`` behavior — UNLESS the
    intent carries ``charges_to_spend``, which has nothing to scale against
    on a pool-less item and rejects ``invalid_charge_spend``.
    """
    if intent.intent_type != "use_item" or not intent.item_id:
        return False
    item = get_lib_loader().get_item(intent.item_id)
    if item is None:
        return False  # unknown slug falls through to the existing empty-resolution path
    if _item_needs_attunement_unmet(live, actor_id, item):
        return True
    if _item_activity_ambiguous(live, actor_id, item, intent.activity_id):
        return True
    if _activity_use_gate(live, actor_id, intent, item):
        return True
    cap = _item_charge_cap(item)
    requested = intent.charges_to_spend
    if cap is None:
        if requested is not None:
            _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="invalid_charge_spend"))
            return True
        return False
    cost = _item_charge_cost(item, intent.activity_id)
    if requested is not None:
        resolved = _resolve_charge_request(live, actor_id, intent.item_id, item, intent, cost, cap)
        if resolved is None:
            return True
        cost = resolved
    elif cost <= 0:
        return False
    if _item_charges_spent(live, actor_id, intent.item_id) + cost > cap:
        _emit(live, CastFailed(actor_id=actor_id, spell_id="", reason="no_charges_remaining"))
        return True
    return False


def _gate_feature_and_item_uses(
    live: _LiveCombat,
    actor_id: str,
    current: Combatant,
    intent: PlayerIntent,
    feature_invocation: _FeatureInvocation | None,
) -> bool:
    """True (rejects) when a capped feature invocation's per-rest uses, or a
    capped item's charge pool, cannot cover this intent.

    Extracted out of ``submit_player_intent`` to keep it under the C901
    complexity ceiling; groups both gates at their shared placement point —
    after feature resolution, BEFORE any action-economy budget is consumed.

    SRD 5.2 Action Surge is capped per REST *and* once per turn. Its per-turn
    refusal is built here, ahead of the per-rest pool: the pool check would
    otherwise fire first and the player would read "no uses left" while a
    second surge is still owed after this turn.
    """
    if intent.feature_id == _ACTION_SURGE:
        surge_failure = _action_surge_failure(current, intent)
        if surge_failure is not None:
            _emit(live, surge_failure)
            return True
    if (
        intent.feature_id
        and feature_invocation is not None
        and (
            _feature_pool_request_failure(live, actor_id, intent, feature_invocation)
            or _feature_uses_exhausted(live, actor_id, intent.feature_id, feature_invocation)
            or _cross_feature_spend_failure(live, current, feature_invocation)
        )
    ):
        return True
    return _item_charge_gate(live, actor_id, intent)


def _record_item_charge_spend(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """Commit the charge spend once every gate has passed. No-op for a non-
    ``use_item`` intent or an uncapped item — only a within-cap invocation
    reaches here past the early exhaustion gate.
    """
    if intent.intent_type != "use_item" or not intent.item_id:
        return
    item = get_lib_loader().get_item(intent.item_id)
    if item is None:
        return
    _record_activity_use_spend(live, actor_id, intent, item)
    if _item_charge_cap(item) is None:
        return
    cost = (
        intent.charges_to_spend
        if intent.charges_to_spend is not None
        else _item_charge_cost(item, intent.activity_id)
    )
    if cost <= 0:
        return
    counters = live.custom_counters_by_entity.setdefault(actor_id, {})
    counter = counters.setdefault(_item_use_counter_key(intent.item_id), {"spent": 0})
    counter["spent"] = counter.get("spent", 0) + cost


def _feature_invocation_refusal(
    live: _LiveCombat, caster: Combatant, intent: PlayerIntent
) -> str:
    """Why `_resolve_feature_invocation` gave up — the same three cases, in the
    same order, so the announced reason matches reality.

    Kept beside the resolver rather than folded into it: the resolver stays
    pure (no `live`, no events), which is what lets availability checks reuse
    it without touching random state (§11).
    """
    feature_id = intent.feature_id or ""
    if feature_id not in _granted_feature_slugs(caster):
        return "not_in_repertoire"
    feature = get_lib_loader().get_feature(feature_id)
    activities = list(feature.activities) if feature else []
    if not activities:
        return "feature_not_executable"
    return "activity_not_selected"

def _resolve_feature_invocation(
    caster: Combatant,
    feature_id: str,
    activity_id: str | None = None,
    *,
    pool_points: int | None = None,
    raging: bool = False,
    leaving_form: bool = False,
) -> _FeatureInvocation | None:
    """Resolve a USE_FEATURE intent to its single concrete activity, or ``None``.

    Applies the REPERTOIRE GATE (class / subclass / species ``granted_features``
    at/below the caster's level) and the SINGLE-ACTIVITY contract. Returns
    ``None`` — after a loud, tracked warning — when the feature is out of
    repertoire, absent from the lib, or has no typed activities.

    A multi-activity feature is a repertoire of ALTERNATIVES (Channel Divinity:
    Divine Spark Heal vs Save vs Turn Undead). When ``activity_id`` names one of
    them, resolve EXACTLY that activity; when it is absent or names none of them,
    keep the safe no-op reject (never guess). Returning ``None`` lets the caller
    reject the invocation BEFORE any action-economy budget is consumed.

    Rage / Second Wind activate as a Bonus Action (``activation.type ==
    "bonus"``); that does NOT end the turn, so the actor may rage then swing on
    the same turn. ``raging``: the caster already has a live Rage (see
    ``_FeatureInvocation.extends_rage``). ``leaving_form``: the caster is in a
    Wild Shape form and names none (see ``_FeatureInvocation.leaves_form``).
    """
    if feature_id not in _granted_feature_slugs(caster):
        _LOGGER.warning(
            "feature_not_in_repertoire feature_id=%s class_slug=%s subclass_slug=%s "
            "species_slug=%s level=%d",
            feature_id,
            caster.class_slug,
            caster.subclass_slug,
            caster.species_slug,
            caster.character_level,
        )
        return None
    feature = get_lib_loader().get_feature(feature_id)
    all_activities = list(feature.activities) if feature else []
    if not all_activities:
        _LOGGER.warning("class_feature_no_typed_activities feature_id=%s", feature_id)
        return None
    selected = all_activities[0]
    if len(all_activities) > 1:
        # Repertoire of ALTERNATIVES — resolve the caller-selected activity, or
        # defer with a loud, tracked no-op when no valid selection is supplied
        # (firing all of them is wrong; guessing one is worse).
        chosen = next((a for a in all_activities if a.id == activity_id), None)
        if chosen is None:
            _LOGGER.warning(
                "feature_multi_activity_selection_deferred feature_id=%s count=%d activity_id=%s",
                feature_id,
                len(all_activities),
                activity_id,
            )
            return None
        selected = chosen
    scaling_value = (pool_points or 1) if _scales_by_amount(selected) else None
    # The per-rest cap resolves against the caster's real ScaleValue map, so a
    # ``@scale.*`` maximum (Second Wind's ``@scale.fighter.second-wind``, 3 at
    # Fighter 5) gives its level-scaled cap rather than a conservative floor.
    scale_values = _scale_values_of(caster)
    activation = getattr(selected.activation, "type", None)
    invocation = _FeatureInvocation(
        activities=[selected],
        # Rage's melee damage bonus and resistances ride a PassiveEffect on the
        # feature; threading it lets the UtilityActivity's effect rider resolve
        # to a runtime ActiveEffect.
        passive_effects=list(feature.passive_effects) if feature else [],
        is_bonus_action=activation == "bonus",
        is_free_action=activation == "special" and selected.kind in _FREE_SPECIAL_ACTIVITY_KINDS,
        use_cap=_feature_use_cap(feature, _uses_roll_data(caster, scale_values)),
        use_cost=_feature_activity_cost(all_activities, selected, scaling_value=scaling_value),
        scaling_value=scaling_value,
    )
    if raging and feature_id == _RAGE_FEATURE:
        # SRD 5.2 Rage: "Take a Bonus Action to extend your Rage." Already
        # raging, the invocation extends it: the Bonus Action, no use, nothing
        # applied.
        return replace(invocation, use_cost=0, extends_rage=True)
    if leaving_form and feature_id == _WILD_SHAPE_FEATURE:
        return replace(invocation, use_cost=0, leaves_form=True)
    return invocation


# SRD 5.2 Bardic Inspiration. The die a creature holds is the "Inspired" effect
# the Inspire activity applies to it (id from its name, origin
# ``cast:inspired:<bard>``).
_BARDIC_INSPIRATION: Final = "bardic-inspiration"
_INSPIRED_EFFECT_ID: Final = "effect:inspired"
_INSPIRED_ORIGIN_PREFIX: Final = "cast:inspired:"
#: Intents that roll a d20 for the ACTING creature and may therefore redeem a
#: held Bardic Inspiration die by asking (``PlayerIntent.redeem_granted_die``):
#: a weapon attack and a spell attack. Saves and checks need no flag — the
#: die belongs to the roller, and the resolver draws it itself.
_DIE_REDEEMABLE_INTENTS: Final = frozenset({"attack", "cast_spell"})


def _live_effect(live: _LiveCombat, entity_id: str, effect_id: str) -> ActiveEffect | None:
    """``entity_id``'s live effect with id ``effect_id``, or ``None``."""
    return next((e for e in live.active_effects.get(entity_id, []) if e.id == effect_id), None)


def _inspiration_effect(live: _LiveCombat, entity_id: str) -> ActiveEffect | None:
    """The Bardic Inspiration die ``entity_id`` holds — SRD 5.2: "A creature can
    have only one Bardic Inspiration die at a time."""
    return _live_effect(live, entity_id, _INSPIRED_EFFECT_ID)


def _granted_dice_by_entity(live: _LiveCombat) -> dict[str, str]:
    """Every creature in this fight holding a Bardic Inspiration die, as
    ``{entity_id: "1d8"}`` — the per-holder projection the resolver reads when
    the roll belongs to somebody other than the acting creature (a saving
    throw forced by an enemy's spell). Empty when nobody holds one, so every
    pre-existing fixture resolves byte-identically.
    """
    dice: dict[str, str] = {}
    for c in live.initiative:
        die = _granted_die(live, c)
        if die is not None:
            dice[c.entity_id] = die
    return dice


def _granted_die(live: _LiveCombat, holder: Combatant) -> str | None:
    """The Bardic Inspiration die ``holder`` can roll, as ``"1d6"`` … ``"1d12"``.

    It is "one of your Bardic Inspiration dice": the granting bard's
    ``@scale.bard.inspiration`` at its Bard level, read now. A die whose bard is
    not in this combat cannot be sized, so it is not redeemable (``None``, like
    no die at all).
    """
    effect = _inspiration_effect(live, holder.entity_id)
    if effect is None:
        return None
    bard = _find_combatant(live, effect.origin.removeprefix(_INSPIRED_ORIGIN_PREFIX))
    if bard is None:
        return None
    die = _scale_values_of(bard).get("bard.inspiration.die")
    return f"1{die}" if isinstance(die, str) else None


def _redeemed_die(live: _LiveCombat, attacker: Combatant, intent: PlayerIntent) -> str | None:
    """The die this ``attack`` asks to roll on a miss (``redeem_granted_die``);
    ``None`` when it asks for none, and for every other intent type."""
    # SRD 5.2 Bardic Inspiration applies to any D20 Test, so a SPELL attack
    # (``cast_spell`` whose activity is an attack — Eldritch Blast, Guiding
    # Bolt) redeems the die exactly as a weapon attack does.
    if intent.intent_type not in _DIE_REDEEMABLE_INTENTS or intent.redeem_granted_die is None:
        return None
    return _granted_die(live, attacker)


def _granted_die_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``AttackFailed(reason="no_granted_die")`` when an ``attack`` asks to
    redeem a die its attacker cannot roll; ``None`` otherwise. One of the
    ``pre_resolution_gates``, so the refusal spends nothing."""
    if intent.intent_type not in _DIE_REDEEMABLE_INTENTS or intent.redeem_granted_die is None:
        return None
    if _granted_die(live, current) is not None:
        return None
    return AttackFailed(
        actor_id=current.entity_id, target_id=intent.target_id, reason="no_granted_die"
    )


def _bardic_inspiration_target_failure(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="target_invalid")`` for a Bardic Inspiration with no
    creature to inspire — SRD 5.2: "you can inspire another creature … A
    creature can have only one Bardic Inspiration die at a time": no target in
    this combat, the bard itself, or a creature already holding a die. Range
    and sight are not modelled. ``None`` otherwise."""
    if intent.feature_id != _BARDIC_INSPIRATION:
        return None
    target = _find_combatant(live, intent.target_id) if intent.target_id else None
    if (
        target is not None
        and target.entity_id != actor_id
        and _inspiration_effect(live, target.entity_id) is None
    ):
        return None
    return CastFailed(actor_id=actor_id, spell_id="", reason="target_invalid")


def _rage_armor_failure(
    current: Combatant, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="heavy_armor")`` for a Rage activated by a barbarian
    already wearing heavy armor — SRD 5.2 Rage: "it ends early if you don Heavy
    armor"; refusing the activation is the same rule stated at the other end.
    One of the ``pre_resolution_gates``, so the refusal spends no Bonus Action
    and no Rage use. ``None`` for every other intent.
    """
    if intent.feature_id != _RAGE_FEATURE or intent.intent_type != "use_feature":
        return None
    if current.worn_armor != "heavy":
        return None
    return CastFailed(actor_id=actor_id, spell_id="", reason="heavy_armor")


def _raging_cast_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="raging")`` for a spell cast by a raging creature —
    SRD 5.2 Rage: "you can't cast spells" and "your Concentration ends" (the
    latter is folded when the Rage effect lands, ``_emit_apply_effect_applied``).
    A ``pre_resolution_gate``: the refusal spends no slot and no action.
    """
    if intent.intent_type != "cast_spell":
        return None
    if _rage_effect(live, current.entity_id) is None:
        return None
    return CastFailed(actor_id=current.entity_id, spell_id=intent.spell_id or "", reason="raging")


def _expend_granted_dice_rolled_by_targets(
    live: _LiveCombat, actx: ActivityResolutionContext
) -> None:
    """End the "Inspired" effect of every creature that rolled its die on
    its OWN failed saving throw during this resolution — SRD 5.2: "A Bardic
    Inspiration die is expended when it's rolled."

    The attack path spends the acting creature's die in
    ``_expend_granted_die``; a save forced by somebody else's spell is rolled
    by the TARGET (``activities/save_primitive.py`` records it in
    ``actx.granted_dice_rolls``), so the same expenditure is applied keyed by
    holder here.
    """
    for holder_id in list(actx.granted_dice_rolls):
        effect = _inspiration_effect(live, holder_id)
        if effect is None:
            continue
        _emit(
            live,
            EffectExpired(
                effect_id=effect.id,
                target_id=holder_id,
                origin=effect.origin,
                reason="expended",
            ),
        )


def _expend_granted_die(
    live: _LiveCombat, holder: Combatant, actx: ActivityResolutionContext
) -> None:
    """SRD 5.2: "A Bardic Inspiration die is expended when it's rolled." Ends the
    holder's "Inspired" effect once ``attack.py`` rolled the die; a hit or a
    natural 1 left it unrolled and banked."""
    effect = _inspiration_effect(live, holder.entity_id)
    if not actx.granted_die_rolls or effect is None:
        return
    _emit(
        live,
        EffectExpired(
            effect_id=effect.id,
            target_id=holder.entity_id,
            origin=effect.origin,
            reason="expended",
        ),
    )


def _begin_turn(live: _LiveCombat, *, new_round: bool) -> None:
    """Open the turn ``live.current_turn_index`` now points at.

    The second half of the turn boundary, shared by ``start_combat`` (which has
    no turn to end) and ``_end_turn_and_advance``. Emits the round-start pair
    when ``new_round``, then ``TurnStarted`` (whose ``_emit`` fold performs the
    action-economy resets), the ``turn_start`` marker + hooks, and finally any
    pending death save.
    """
    if new_round:
        _emit(live, RoundStarted(round_number=live.round_number))
        _emit(
            live,
            TurnPhase(actor_id=None, phase="round_start", round_number=live.round_number),
        )
        run_round_start(live)
    actor_id = _current_actor(live).entity_id
    _emit(live, TurnStarted(actor_id=actor_id))
    _emit(
        live,
        TurnPhase(actor_id=actor_id, phase="turn_start", round_number=live.round_number),
    )
    run_turn_start(live, actor_id)
    _maybe_roll_death_save(live)


def _open_turn_at_current_index(live: _LiveCombat) -> None:
    """Open the turn ``live.current_turn_index`` names, wrapping to a new
    round when the index has run past the last slot.

    Shared by ``_end_turn_and_advance`` (after the ending actor's turn-end
    phase) and ``_hand_off_departed_turn`` (a current actor that left the
    initiative order, whose removal already moved the pointer on, C21), so
    neither path skips the next creature's turn.
    """
    new_round = live.current_turn_index >= len(live.initiative)
    if new_round:
        live.current_turn_index = 0
        live.round_number += 1
    _begin_turn(live, new_round=new_round)


def _end_turn_and_advance(live: _LiveCombat, actor_id: str) -> None:
    """SRD §Action Economy — end ``actor_id``'s turn and start the next.

    The ONE turn-advance implementation in the engine: ``submit_player_intent``
    (both the spell-slot reject paths and the normal post-resolution path) and
    ``advance_monster_turn`` all route through here, ``start_combat`` runs
    the second half via ``_begin_turn``, and ``_hand_off_departed_turn``
    reuses both halves for a creature that left mid-turn (C21). Before F3a
    each of those three sites carried its own copy of the emit-and-wrap block.

    Event order at the boundary is fixed and pinned by
    ``tests/test_turn_lifecycle.py``::

        TurnPhase(turn_end, A) -> [turn_end hooks: repeat-save, duration tick,
                                    timed-effect expiry] -> TurnEnded(A)
        -> (on wrap) RoundStarted -> TurnPhase(round_start) -> [round_start hooks]
        -> TurnStarted(B) -> TurnPhase(turn_start, B) -> [turn_start hooks]
        -> pending death save

    The marker precedes its hooks so a host reading the stream sees the phase
    announced before anything that phase causes. That includes the SRD repeat
    save (Hold Person & co.), which is the ``engine:repeat-save`` ``turn_end``
    hook rather than a call at the intent sites — so it fires exactly once per
    turn end and never on the bonus-action path, which returns before reaching
    here.

    A creature that left the initiative order during its own turn (C21) is
    no longer in it: its removal already moved the pointer to the next
    creature, so ending its turn is the pending hand-off
    (``_hand_off_departed_turn``), done once.
    """
    if _find_combatant(live, actor_id) is None:
        _hand_off_departed_turn(live)
        return
    _close_turn(live, actor_id)
    live.current_turn_index += 1
    _open_turn_at_current_index(live)


def _close_turn(live: _LiveCombat, actor_id: str) -> None:
    """The first half of the turn boundary: ``actor_id``'s turn-end phase and
    hooks, ``TurnEnded``, and the legendary-action window after it."""
    _emit(
        live,
        TurnPhase(actor_id=actor_id, phase="turn_end", round_number=live.round_number),
    )
    run_turn_end(live, actor_id)
    _emit(live, TurnEnded(actor_id=actor_id))
    # C18 §Monster action economy — record the window a legendary action may
    # be taken in, BEFORE the caller's round/turn-index bump moves
    # ``live.round_number`` past the round this turn just ended in.
    live.last_ended_turn = (live.round_number, actor_id)


def _hand_off_departed_turn(live: _LiveCombat, *, turn_began: bool = True) -> None:
    """Open the next creature's turn after the current actor left the
    initiative order (C21), once the intent or legendary action that removed
    it has resolved, so all of that resolution's events precede the next
    ``TurnStarted``. Its removal already moved the pointer to the next
    creature, so the pointer is not bumped. A no-op when nothing is pending.

    A creature that left during its own intent had a turn, and it ends as any
    turn does — the ``turn_end`` phase and hooks, ``TurnEnded``, and the
    window SRD 5.2 Legendary Actions open "immediately after another
    creature's turn". ``turn_began=False`` is a legendary action's removal:
    taken in the window after the previous creature's turn, before this one
    acted, so the departed creature had no turn to end and opens no window.
    """
    departed = live.departed_actor_id
    if departed is None:
        return
    if turn_began:
        _close_turn(live, departed)
    live.departed_actor_id = None
    _open_turn_at_current_index(live)


def _keep_turn(live: _LiveCombat) -> None:
    """The tail of an intent that keeps its actor's turn: roll the actor's
    pending death save — unless the actor left the initiative order during
    the intent, whose turn is then handed on instead (the pointer already
    names the next creature, whose own turn start rolls its death save)."""
    if live.departed_actor_id is not None:
        _hand_off_departed_turn(live)
        return
    _maybe_roll_death_save(live)


def _end_action(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """End ``actor_id``'s turn after an Action-costed intent, unless an Action
    Surge extra action is still unspent — SRD 5.2: "On your turn, you can take
    one additional action" — so the turn stays open for it (an unused one
    lapses at the actor's next turn start). ``pass`` always ends the turn."""
    actor = _find_combatant(live, actor_id)
    if intent.intent_type != "pass" and actor is not None and actor.extra_actions_remaining > 0:
        _maybe_roll_death_save(live)
        return
    _end_turn_and_advance(live, actor_id)


def _insert_into_roster(
    live: _LiveCombat, combatant: Combatant, index: int, *, zone_id: str
) -> None:
    """Seat ``combatant`` at ``index`` of the initiative order (C21).

    A positional insert, never a re-sort: SRD 5.2 Summon Dragon, "the
    creature shares your Initiative count, but it takes its turn immediately
    after yours". An insert at or before the current slot moves the pointer
    on by one, so the creature whose turn it is keeps it. Registers the
    creature's zone and tracked Hit Points; emits nothing (the caller emits
    ``CombatantJoined`` with the seat's own fields).
    """
    live.initiative.insert(index, combatant)
    if index <= live.current_turn_index:
        live.current_turn_index += 1
    live.actor_zone[combatant.entity_id] = zone_id
    live.tracked_hp[combatant.entity_id] = combatant.hp_current


def _purge_entity_state(live: _LiveCombat, entity_id: str) -> None:
    """Drop every trace of a creature that left the initiative order (C21):
    its own per-entity state, the marks and grants it holds or sourced, the
    constructs it owns, and the effect identities that target it.

    Another caster's concentration on it keeps running (its
    ``concentration_chain`` entry is kept): SRD 5.2 is silent on a spell whose
    target vanishes, so when that concentration ends its cascade still runs
    and may name the departed id. Its own chain is only dropped here: its
    concentration must already have ended through ``_drop_concentration``
    (``_leave_roster`` runs it first), or the effects it maintained on other
    creatures would outlive it with nothing left to end them.
    """
    per_entity: tuple[dict[str, Any], ...] = (
        live.actor_zone,
        live.monster_slug_by_entity,
        live.xp_value_by_entity,
        live.tracked_hp,
        live.tracked_temp_hp,
        live.undead_fortitude_holds,
        live.active_conditions,
        live.active_effects,
        live.expended_resources,
        live.spell_slots_by_entity,
        live.pact_slots_by_entity,
        live.spells_known_by_entity,
        live.custom_counters_by_entity,
        live.concentration_chain,
        live.concentration_rounds_remaining,
        live.reaction_effects_pending_expiry,
        live.help_grants,
        live.vex_grants,
        live.sap_marks,
        live.slow_marks,
        live.monster_action_uses_by_entity,
        live.legendary_resistance_armed,
        live.transforms,
        live.summons,
    )
    for state in per_entity:
        state.pop(entity_id, None)
    live.hidden_entities.discard(entity_id)
    live.rage_bonus_extensions.discard(entity_id)
    by_identity: tuple[dict[tuple[str, str, str], Any], ...] = (
        live.conditions_by_effect,
        live.repeat_save_on_turn_end,
    )
    for identities in by_identity:
        for identity in [key for key in identities if key[0] == entity_id]:
            del identities[identity]
    live.pending_reactions[:] = [r for r in live.pending_reactions if r.owner_id != entity_id]
    for construct_id in [k for k, c in live.constructs.items() if c.owner_id == entity_id]:
        del live.constructs[construct_id]
    _purge_references_to(live, entity_id)


def _purge_references_to(live: _LiveCombat, entity_id: str) -> None:
    """Drop ``entity_id`` where it appears inside another creature's entry: a
    Help grant it gave, a Vex grant against it, a Sap mark it sourced, a Slow
    mark it sourced. An entry left empty goes with it."""
    for target in list(live.help_grants):
        helpers = [h for h in live.help_grants[target] if h != entity_id]
        if helpers:
            live.help_grants[target] = helpers
        else:
            del live.help_grants[target]
    for attacker in list(live.vex_grants):
        live.vex_grants[attacker].pop(entity_id, None)
        if not live.vex_grants[attacker]:
            del live.vex_grants[attacker]
    for sapped in [s for s, source in live.sap_marks.items() if source == entity_id]:
        del live.sap_marks[sapped]
    for slowed in list(live.slow_marks):
        live.slow_marks[slowed].discard(entity_id)
        if not live.slow_marks[slowed]:
            del live.slow_marks[slowed]


def _remove_from_roster(live: _LiveCombat, entity_id: str) -> bool:
    """Splice ``entity_id`` out of the initiative order and purge its state
    (C21); return whether it was the current actor.

    A removal before the current slot moves the pointer back by one, so the
    current actor keeps its turn. Removing the current actor leaves the
    pointer where it is: that slot now names the next creature, or runs past
    the last slot (the caller opens that turn). Precondition: ``entity_id``
    is in the roster.
    """
    index = next(i for i, c in enumerate(live.initiative) if c.entity_id == entity_id)
    del live.initiative[index]
    _purge_entity_state(live, entity_id)
    was_current = index == live.current_turn_index
    if index < live.current_turn_index:
        live.current_turn_index -= 1
    return was_current


def _leave_roster(live: _LiveCombat, entity_id: str, reason: CombatantLeftReason) -> None:
    """A creature disappears from the combat (C21). SRD 5.2 Summon Dragon:
    "The creature disappears when it drops to 0 Hit Points or when the spell
    ends."

    Ends whatever it concentrates on through the drop cascade (nothing it
    maintained outlives it), then splices it out and emits
    ``CombatantLeft``. When it was the current
    actor, the turn passes on — nobody skipped — once the resolution in
    progress completes (``_hand_off_departed_turn``), so the rest of that
    resolution never lands inside the next turn. While that hand-off is
    pending no turn is running, so a creature removed from the slot the
    pointer names never had a turn to end. A no-op for an id not in the
    roster, so two expiries in one cascade never remove it twice. Draws
    nothing.
    """
    if _find_combatant(live, entity_id) is None:
        return
    _drop_concentration(live, entity_id)
    was_current = _remove_from_roster(live, entity_id)
    _emit(live, CombatantLeft(entity_id=entity_id, reason=reason))
    if was_current and live.departed_actor_id is None:
        live.departed_actor_id = entity_id


def _validate_intent_preconditions(
    live: _LiveCombat,
    handle: CombatHandle,
    actor_id: str,
    *,
    intent: PlayerIntent | None = None,
) -> Combatant:
    """Validate that combat is live, ``actor_id`` is in initiative, it is
    currently ``actor_id``'s turn, and — when ``intent`` is given — the actor
    is not Incapacitated for an action/bonus/reaction-shaped intent. Raises
    ``IntentRejectedError`` on any failure; returns the current actor's
    ``Combatant`` on success."""
    if live.ended:
        raise IntentRejectedError("combat_ended", f"handle={handle.handle_id}")

    in_initiative = any(c.entity_id == actor_id for c in live.initiative)
    if not in_initiative:
        raise IntentRejectedError(
            "actor_not_in_initiative",
            f"actor_id={actor_id!r} not in initiative order",
        )

    current = _current_actor(live)
    if current.entity_id != actor_id:
        raise IntentRejectedError(
            "not_actor_turn",
            f"current_turn={current.entity_id!r}, submitted={actor_id!r}",
        )

    # SRD 5.2 Incapacitated: "You can't take any action, Bonus Action, or
    # Reaction." (implied by Paralyzed / Petrified / Stunned / Unconscious).
    if (
        intent is not None
        and intent.intent_type not in _INCAPACITATED_ALLOWED_INTENTS
        and conditions_block_actions(_condition_names(current))
    ):
        raise IntentRejectedError(
            "actor_incapacitated",
            f"actor_id={actor_id!r} is Incapacitated and cannot take {intent.intent_type!r}",
        )
    return current


def _handle_move(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> None:
    """SRD 5.2 §Movement and Position — move ``current`` to ``target_zone_id``
    along the fewest-cells legal route (``shortest_path``), paying each leg's
    ``edge_distance`` (difficult terrain doubles) out of ``movement_remaining``.
    Each uninterrupted run of steps emits one ``ActorMoved`` (see below). Movement does
    NOT end the turn.

    Rejections (``MoveFailed``, nothing mutated): ``not_adjacent`` — no
    destination / untracked position / destination is the current cell (the
    legacy reason is retained for hosts); ``occupied`` — "You can't willingly
    end a move in a space occupied by another creature"; ``blocked_path`` —
    the destination is adjacent but the step crosses a wall or cuts a blocked
    corner; ``unreachable`` — no legal route (enemy-occupied cells are
    impassable, allies may be passed through); ``insufficient_movement`` — the
    whole route costs more than the remaining budget, and nothing moves;
    ``frightened`` — SRD 5.2 Frightened: "You can't willingly move closer to
    the source of fear" (C16b) — the mover is Frightened of a known, living,
    tracked, currently-visible source and some step of the route would
    reduce distance to it (``_frightened_approach_blocked``).

    Every opportunity attack a step provokes, from either side, fires before
    the mover leaves its cell (``_fire_opportunity_attacks_on_step``). A step
    that provokes closes the ``ActorMoved`` run walked so far, so the attack
    lands between two events. A mover an attack stops (0 HP, Speed 0, gone)
    stays on the cell it was leaving; a mover an attack pushes stops where
    the push leaves it, keeping its unspent movement.
    """
    actor_id = current.entity_id
    # SRD 5.2 "Speed 0. Your Speed is 0 and can't increase." (Grappled /
    # Restrained / Paralyzed / Petrified / Unconscious) and Exhaustion's
    # ``-5 ft x level``: a creature whose effective Speed is 0 cannot move at
    # all — distinct from ``insufficient_movement`` (budget spent this turn).
    if _effective_speed(current, live) == 0:
        _emit(live, MoveFailed(actor_id=actor_id, reason="speed_zero"))
        return
    destination = intent.target_zone_id
    start_zone = live.actor_zone.get(actor_id)
    if destination is None or start_zone is None or destination == start_zone:
        _emit(live, MoveFailed(actor_id=actor_id, reason="not_adjacent"))
        return
    # SRD §Moving Around Other Creatures — a move may not END in another
    # creature's space, ally or enemy alike.
    if destination in _occupied_cells(live, exclude=(actor_id,)):
        _emit(live, MoveFailed(actor_id=actor_id, reason="occupied"))
        return
    # Adjacency alone doesn't guarantee a legal step — a wall
    # crossing the segment or a diagonal cutting a blocked corner yields None
    # from edge_distance (SRD 5.2 "Corners").
    if (
        live.topology.is_adjacent(start_zone, destination)
        and live.topology.edge_distance(start_zone, destination) is None
    ):
        _emit(live, MoveFailed(actor_id=actor_id, reason="blocked_path"))
        return
    # Enemy spaces are impassable; an ally's space, a summon's included, may
    # be passed through.
    enemy_cells = _occupied_cells(live, exclude=_allied_ids(live, actor_id))
    ignore_terrain = current.ignored_difficult_terrain
    path = live.topology.shortest_path(
        start_zone, destination, avoid=enemy_cells, ignore_terrain=ignore_terrain
    )
    if not path:
        _emit(live, MoveFailed(actor_id=actor_id, reason="unreachable"))
        return
    # "To enter a square, you must have enough movement left to pay for
    # entering" — the whole route is priced up front so a rejection is atomic.
    total_cost = _path_total_distance(live.topology, path, ignore_terrain=ignore_terrain)
    if total_cost is None or current.movement_remaining < total_cost:
        _emit(live, MoveFailed(actor_id=actor_id, reason="insufficient_movement"))
        return
    # SRD 5.2 Frightened: "You can't willingly move closer to the source of
    # fear." (C16b) — checked before any budget is spent or opportunity
    # attack fires, so a rejection here is as atomic as the ones above.
    if _frightened_approach_blocked(live, current, path):
        _emit(live, MoveFailed(actor_id=actor_id, reason="frightened"))
        return
    # SRD 5.2 Opportunity Attacks: "The attack occurs right before the
    # creature leaves your reach." One ``ActorMoved`` per uninterrupted run of
    # steps: a step that provokes first closes the run walked so far, so the
    # attack lands between the two events.
    run_start, run_ft, position = start_zone, 0, start_zone
    for next_cell in path[1:]:
        step_distance = live.topology.edge_distance(
            position, next_cell, ignore_terrain=current.ignored_difficult_terrain
        )
        if step_distance is None:  # pragma: no cover - route is legal by construction
            break
        if run_ft and _opportunity_attackers(
            live, mover_id=actor_id, from_cell=position, to_cell=next_cell
        ):
            _emit(
                live,
                ActorMoved(
                    actor_id=actor_id, from_zone=run_start, to_zone=position, distance_ft=run_ft
                ),
            )
            run_start, run_ft = position, 0
        if _fire_opportunity_attacks_on_step(
            live, mover_id=actor_id, from_cell=position, to_cell=next_cell
        ):
            break
        mover = _find_combatant(live, actor_id)
        if mover is None or mover.movement_remaining < step_distance:
            break
        _spend_movement(live, actor_id, step_distance)
        live.actor_zone[actor_id] = next_cell
        position = next_cell
        run_ft += step_distance
    # A summon dropped to 0 HP on the way has left the order, and its
    # departure already opened the next turn: no ActorMoved.
    if run_ft and _find_combatant(live, actor_id) is not None:
        _emit(
            live,
            ActorMoved(
                actor_id=actor_id, from_zone=run_start, to_zone=position, distance_ft=run_ft
            ),
        )
    # Turn stays live — no TurnEnded, no current_turn_index advance.


# ── C21 conjurations: the shared pre-spend gate and the enchant ─────────────

_ConjurationGate = Callable[[_LiveCombat, Combatant, PlayerIntent], CombatEvent | None]


def _conjuration_gate_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """The first C21 conjuration refusal for ``intent``, or ``None``. The last
    of ``submit_player_intent``'s ``pre_resolution_gates``, so a refused
    conjuration spends no slot, use or action. Each check is a no-op for an
    intent it does not govern."""
    # A shape-shifted actor is refused first: it cannot cast any spell at all,
    # nor make a weapon attack — so a form's weapon attack is refused here,
    # before the stat-block check below ever sees it. A summon's own limits
    # come next, for the same reason: it casts nothing, so a readied
    # conjuration of its reports that, and only its stat block's attacks reach
    # the stat-block check. A readied conjuration is refused before the
    # enchant gate reads its (absent) weapon; the rest govern disjoint intents
    # (a construct's attack names a spell, a stat-block swing names an action,
    # Wild Shape and Polymorph each their own allowlisted source), so their
    # order only fixes which reason a malformed intent reports.
    checks: tuple[_ConjurationGate, ...] = (
        _shape_shifted_failure,
        _summon_command_failure,
        _readied_conjuration_failure,
        _enchant_cast_failure,
        _construct_cast_failure,
        _summon_cast_failure,
        _construct_attack_failure,
        _stat_block_attack_failure,
        _wild_shape_failure,
        _polymorph_form_failure,
    )
    for check in checks:
        failure = check(live, current, intent)
        if failure is not None:
            return failure
    return None


def _readied_conjuration_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="target_invalid")`` for a ``ready`` naming an
    allowlisted conjuration. A readied cast resolves from ``_PendingReaction``,
    which carries only the spell and its slot level, never the weapon, form or
    cell the conjuration needs."""
    if intent.intent_type != "ready" or (intent.spell_id or "") not in CONJURATION_ALLOWLIST:
        return None
    return CastFailed(
        actor_id=current.entity_id, spell_id=intent.spell_id or "", reason="target_invalid"
    )


def _enchant_cast_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="target_invalid")`` for an allowlisted enchant cast
    whose ``weapon_id`` its ``EnchantActivity.restrictions`` refuse.

    SRD 5.2 Magic Weapon: "You touch a nonmagical weapon." The weapon must be
    a corpus ``Weapon`` (``restrictions.type == "weapon"``) and, under
    ``allow_magical: false``, neither magical nor a +N weapon. An Unarmed
    Strike is not a weapon ("Instead of using a weapon to make a melee attack,
    you can use a punch, kick, headbutt, or similar forceful blow"). Whether
    the target carries the weapon is not checked: equipment is optional on a
    hand-built spec.
    """
    spell_id = intent.spell_id or ""
    if intent.intent_type != "cast_spell" or CONJURATION_ALLOWLIST.get(spell_id) != "enchant":
        return None
    loader = get_lib_loader()
    weapon = loader.get_weapon(intent.weapon_id) if intent.weapon_id else None
    spell = loader.get_spell(spell_id)
    enchant = next(
        (a for a in (spell.activities if spell else ()) if isinstance(a, EnchantActivity)), None
    )
    if (
        weapon is not None
        and weapon.slug != _UNARMED_STRIKE
        and enchant is not None
        and enchant.restrictions.type == "weapon"
        and (enchant.restrictions.allow_magical or not (weapon.magical or weapon.magical_bonus))
    ):
        return None
    return CastFailed(actor_id=current.entity_id, spell_id=spell_id, reason="target_invalid")


def _conjuration_carrier(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> ConjurationCarrier | None:
    """The pre-validated inputs an allowlisted conjuration resolves with, or
    ``None`` for every other intent (its activities stay narrative). Echoes
    what ``_conjuration_gate_failure`` already accepted."""
    source = (
        intent.spell_id
        if intent.intent_type == "cast_spell"
        else intent.feature_id
        if intent.intent_type == "use_feature"
        else intent.item_id
        if intent.intent_type == "use_item"
        else None
    ) or ""
    kind = CONJURATION_ALLOWLIST.get(source)
    if kind == "enchant":
        return ConjurationCarrier(source_slug=source, weapon_slug=intent.weapon_id)
    if kind == "construct":
        return ConjurationCarrier(source_slug=source, cell=_construct_cell(live, current, intent))
    if kind == "summon":
        # SRD 5.2 Summon Dragon: the space ``_summon_cast_failure`` accepted.
        return ConjurationCarrier(
            source_slug=source,
            cell=_summon_placement(live, current, intent)[0],
            form_slug=intent.form_id,
        )
    if kind == "transform":
        # SRD 5.2 Wild Shape: the Beast form ``_wild_shape_failure`` accepted;
        # a leave names none and resolves nothing.
        return (
            ConjurationCarrier(source_slug=source, form_slug=intent.form_id)
            if intent.form_id
            else None
        )
    if kind == "transform_rider":
        # SRD 5.2 Polymorph: the Beast form ``_polymorph_form_failure`` accepted;
        # fold step 2 applies it on a failed save.
        return ConjurationCarrier(source_slug=source, form_slug=intent.form_id)
    return None


def _end_superseded_enchantments(
    live: _LiveCombat,
    caster: Combatant,
    actx: ActivityResolutionContext | None,
    pre_event_count: int,
) -> None:
    """SRD 5.2 Magic Weapon: "The spell ends early if you cast it again." After
    an enchant cast, every other live enchantment this caster made ends
    (``remove_ieffect``), whichever weapon or creature it is on. A recast at
    the same tier on the same creature has the same identity as the old
    effect; the expiry fold pops the first match, which is the older one."""
    if actx is None or actx.conjuration is None or actx.conjuration.weapon_slug is None:
        return
    applied_now = {
        id(ev.effect) for ev in live.event_log[pre_event_count:] if isinstance(ev, EffectApplied)
    }
    superseded = [
        effect
        for effects in live.active_effects.values()
        for effect in effects
        if ENCHANTED_WEAPON_FLAG in effect.flags
        and effect.origin.startswith("cast:")
        and effect.origin.split(":", 2)[2:] == [caster.entity_id]
        and id(effect) not in applied_now
    ]
    for effect in superseded:
        _emit(
            live,
            EffectExpired(
                effect_id=effect.id,
                target_id=effect.target_id,
                origin=effect.origin,
                reason="remove_ieffect",
            ),
        )


def _enchanted_weapon(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon | None
) -> Weapon | None:
    """``weapon`` with the attacker's live enchantments applied (the weapon
    itself when none names it); ``None`` for a weapon-less intent."""
    if weapon is None:
        return None
    return enchant_weapon(weapon, live.active_effects.get(attacker.entity_id, ()))


def _enchanting_spell(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon
) -> str | None:
    """The spell whose enchantment names ``weapon``, if any.

    ``ActiveEffect.origin`` carries the spell slug that applied it; the
    flag names the weapon. The origin is the spell, not the effect name, so
    the ability lookup uses the caster's entry for that spell.
    """
    for effect in live.active_effects.get(attacker.entity_id, ()):
        if effect.flags.get(ENCHANTED_WEAPON_FLAG) == weapon.slug and effect.origin:
            return effect.origin
    return None


def _weapon_enchantment_ability(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon | None
) -> str | None:
    """The ability an enchantment makes ``weapon`` fight with, or ``None``.

    A player character has no ``Combatant.spellcasting_ability`` (that field
    serves stat blocks and constructs), so the enchantment's own source
    supplies it: SRD 5.2 Shillelagh is a Druid cantrip, and the spell that
    enchanted the weapon names the ability.
    """
    if weapon is None:
        return None
    override = enchant_ability_override(
        live.active_effects.get(attacker.entity_id, ()), weapon.slug
    )
    if override is None:
        return None
    source = _enchanting_spell(live, attacker, weapon)
    return _spellcasting_ability_for(attacker, source) if source else None


def _weapon_enchantment_spellcasting(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon | None
) -> bool:
    """Whether a live enchantment turns ``weapon`` to the caster's
    spellcasting ability.

    SRD 5.2 Shillelagh: "you can use your spellcasting ability instead of
    Strength for the attack and damage rolls of melee attacks using that
    weapon". The enchantment names the weapon it applies to, so a second
    weapon in the same hand stays on its own default.
    """
    if weapon is None:
        return False
    return enchant_ability_override(
        live.active_effects.get(attacker.entity_id, ()), weapon.slug
    ) is not None


def _weapon_enchantment_to_hit(
    attacker: Combatant, base: Weapon | None, enchanted: Weapon | None
) -> int:
    """The enchantment's to-hit a host-pinned ``attack_bonus`` would drop:
    ``_attack_bonus`` returns a pinned value verbatim, before the weapon's
    ``magical_bonus``, so the delta rides on top of the pin (as Archery does).
    0 for an unpinned attacker, whose ``magical_bonus`` already counts."""
    if attacker.attack_bonus is None or base is None or enchanted is None:
        return 0
    return enchanted.magical_bonus - base.magical_bonus


# ── C21 constructs (SRD 5.2 Spiritual Weapon) ─────────────────────────────


def _construct_id(owner_id: str, spell_id: str) -> str:
    """One construct per owner and spell: a recast replaces it."""
    return f"construct:{owner_id}:{spell_id}"


def _repeated_construct(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> _Construct | None:
    """The live construct an ``attack`` repeats: the one its ``spell_id`` names
    that ``current`` owns. The field names a force only to that force's owner;
    on any other attack it is not read."""
    if intent.intent_type != "attack" or not intent.spell_id:
        return None
    return live.constructs.get(_construct_id(current.entity_id, intent.spell_id))


def _construct_cell(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> str | None:
    """Where a construct cast places its force. SRD 5.2 Spiritual Weapon: "The
    force appears within range in a space of your choice" — the intent's
    ``target_zone_id``; else the named target's space (the immediate attack is
    against "one creature within 5 feet of the force"); else the caster's."""
    if intent.target_zone_id is not None:
        return intent.target_zone_id
    target_cell = live.actor_zone.get(intent.target_id) if intent.target_id else None
    return target_cell or live.actor_zone.get(current.entity_id)


def _construct_cast_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="out_of_range")`` for a construct cast whose force
    cannot be placed: an explicit ``target_zone_id`` the caster cannot reach
    with the spell (off the map, beyond its range, out of sight or behind total
    cover), or a named target farther from the force's space than its reach.
    With no ``target_zone_id`` the force appears in the named target's space,
    which ``_spell_out_of_range_failure`` has already range-checked."""
    spell_id = intent.spell_id or ""
    spec = CONSTRUCTS.get(spell_id)
    if intent.intent_type != "cast_spell" or spec is None:
        return None
    caster_cell = live.actor_zone.get(current.entity_id)
    spell = get_lib_loader().get_spell(spell_id)
    range_ft = (
        spell.range.value
        if spell is not None and spell.range.units == SpellRangeUnits.FEET
        else None
    )
    in_range = intent.target_zone_id is None or (
        caster_cell is not None
        and range_ft is not None
        and _in_range_with_los(live.topology, caster_cell, intent.target_zone_id, range_ft)
    )
    cell = _construct_cell(live, current, intent)
    target_cell = live.actor_zone.get(intent.target_id) if intent.target_id else None
    reach = live.topology.distance_ft(cell, target_cell) if cell and target_cell else None
    in_reach = target_cell is None or (reach is not None and reach <= spec.reach_ft)
    if in_range and in_reach:
        return None
    return CastFailed(actor_id=current.entity_id, spell_id=spell_id, reason="out_of_range")


def _construct_attack_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """The refusal for a construct's Bonus-Action repeat, or ``None`` (also for
    an attack that repeats no construct: ``_repeated_construct``).

    SRD 5.2 Spiritual Weapon: "As a Bonus Action on your later turns, you can
    move the force up to 20 feet and repeat the attack against a creature
    within 5 feet of it." ``action_unavailable``: the construct was cast this
    round (the cast made its attack), or the repeat also names a weapon — the
    force's attack is the whole of it, and a weapon swing riding the Bonus
    Action would be a free attack. ``target_invalid``: the target is unknown or
    dead. ``out_of_range``: the new ``target_zone_id`` cannot be measured or is
    more than the construct's move away (the force floats: distance, not a
    path), or the target is beyond its reach from the force's (moved) space.
    """
    construct = _repeated_construct(live, current, intent)
    if construct is None:
        return None
    spec = CONSTRUCTS[construct.spell_id]
    if intent.weapon_id or construct.cast_round == live.round_number:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="action_unavailable"
        )
    target = _find_combatant(live, intent.target_id) if intent.target_id else None
    if target is None or target.entity_id in live.dead_ids:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="target_invalid"
        )
    cell = intent.target_zone_id or construct.cell
    moved = live.topology.distance_ft(construct.cell, cell)
    target_cell = live.actor_zone.get(target.entity_id)
    reach = live.topology.distance_ft(cell, target_cell) if target_cell else None
    if moved is None or moved > spec.move_ft or reach is None or reach > spec.reach_ft:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="out_of_range"
        )
    return None


def _stat_block_attack_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """Stat-block commands (C21): an ``attack`` naming ``stat_block_action_id`` is one
    swing of an attack action on the actor's current stat block (its form's
    while transformed). ``AttackFailed(reason="action_unavailable")`` when
    there is nothing to swing — no stat block, no such action, an action with
    no attack roll (the Multiattack itself, since each swing is its own
    intent, and save actions such as a Breath Weapon, which can't be
    commanded yet), or a command that also names a weapon or a spell;
    ``"out_of_range"`` when the target is beyond the action's reach or range.
    ``None`` for every other intent. A refusal spends nothing."""
    if intent.intent_type != "attack" or not intent.stat_block_action_id:
        return None
    found = _stat_block_action(
        _current_stat_block_slug(live, current.entity_id), intent.stat_block_action_id
    )
    activities = list(found[1].activities) if found is not None else []
    # A Multiattack as a NAMED command is still refused: each swing is its own
    # intent, and a command resolves ONE swing. Its parts reach this gate when
    # the summon's turn carries it out instead (``_run_summon_commanded_turn``
    # resolves the multiattack as a whole), which is where the parts belong.
    if found is not None and found[1].slug == _MULTIATTACK_ACTION_SLUG and _commanded_by_owner(
        live, current, intent
    ):
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="action_unavailable"
        )
    # A stat-block action that makes no ATTACK ROLL is still a legal command
    # when it is a save action with a resolving activity (the Air Elemental's
    # Whirlwind: "Strength Saving Throw ... each Medium or smaller creature"):
    # it resolves like a breath weapon does rather than being refused as
    # though it did not exist (§5). Multiattack and pure utility actions keep
    # their refusal — each swing is its own intent, and a utility action has
    # no resolution the command could honour.
    resolvable = any(
        isinstance(a, AttackActivity)
        or (getattr(a, "save", None) is not None and getattr(a, "damage", None) is not None)
        for a in activities
    )
    if intent.weapon_id or intent.spell_id or not resolvable:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="action_unavailable"
        )
    if _stat_block_target_out_of_range(live, current, intent, activities):
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="out_of_range"
        )
    return None


def _wild_shape_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """SRD 5.2 Wild Shape (C21): ``use_feature`` of the allowlisted
    ``"transform"`` feature names its Beast form in ``form_id``, checked
    against the Beast Shapes table before anything is spent.
    ``CastFailed(spell_id="", reason="invalid_form")`` for no ``form_id``
    outside a Wild Shape form; a slug that is no usable Beast
    (``_validated_beast_form``); a form above the Druid level's Max CR, or with
    a Fly Speed below Druid level 8; or a creature under Polymorph, whose game
    statistics — class features included — are its Beast's. No ``form_id``
    inside a Wild Shape form is the Bonus-Action leave. ``None`` for every
    other intent."""
    if (
        intent.intent_type != "use_feature"
        or CONJURATION_ALLOWLIST.get(intent.feature_id or "") != "transform"
        or (intent.form_id is None and _is_wild_shaped(live, current.entity_id))
    ):
        return None
    form = _validated_beast_form(intent.form_id)
    tier = wild_shape_tier(_class_levels(current).get("druid", 0))
    transform = _transform_of(live, current.entity_id)
    if (
        form is None
        or tier is None
        or form.cr > tier.max_cr
        or (bool(form.movement.fly) and not tier.fly_allowed)
        or (transform is not None and transform.source == "polymorph")
    ):
        return CastFailed(actor_id=current.entity_id, spell_id="", reason="invalid_form")
    return None


def _polymorph_form_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """SRD 5.2 Polymorph (C21): the cast names its Beast form in ``form_id``,
    checked before the slot is spent. ``CastFailed(reason="invalid_form")``
    for a slug that is no usable Beast (``_validated_beast_form``), a target
    with neither a Challenge Rating nor a level (``_challenge_rating_of``),
    or a form whose Challenge Rating is above it. ``None`` for every other
    intent."""
    spell_id = intent.spell_id or ""
    if (
        intent.intent_type != "cast_spell"
        or CONJURATION_ALLOWLIST.get(spell_id) != "transform_rider"
    ):
        return None
    form = _validated_beast_form(intent.form_id)
    ceiling = _challenge_rating_of(live, intent.target_id) if intent.target_id else None
    if form is None or ceiling is None or form.cr > ceiling:
        return CastFailed(actor_id=current.entity_id, spell_id=spell_id, reason="invalid_form")
    return None


def _apply_construct_requests(
    live: _LiveCombat, caster: Combatant, actx: ActivityResolutionContext | None
) -> None:
    """Fold step 5: seat each construct this resolution requested and make its
    immediate attack (SRD 5.2 Spiritual Weapon: "you can immediately make one
    melee spell attack against one creature within 5 feet of the force").

    Runs after the concentration anchor, so the construct records a live
    identity, and a same-spell recast's drop has already ended the old
    construct. A living named target is attacked; with none, the force waits."""
    if actx is None:
        return
    for request in actx.construct_requests:
        construct = _Construct(
            construct_id=_construct_id(request.owner_id, request.spell_id),
            owner_id=request.owner_id,
            spell_id=request.spell_id,
            cell=request.cell,
            slot_level=request.slot_level,
            anchor=_anchor_identity(request.spell_id, request.owner_id),
            cast_round=live.round_number,
        )
        live.constructs[construct.construct_id] = construct
        for target_id in request.target_ids:
            target = _find_combatant(live, target_id)
            if target is not None and target_id not in live.dead_ids:
                _resolve_construct_attack(live, caster, construct, target)


def _construct_spellcasting_ability(owner: Combatant, spell_id: str) -> str | None:
    """The ability a construct's or summon's attack uses: the owner's ability
    for ``spell_id`` (``_spellcasting_ability_for``: its own entry in
    ``spell_abilities``, else its class's), else its stat block's (a monster),
    else ``None`` (the legacy fallbacks of a classless caster)."""
    return _spellcasting_ability_for(owner, spell_id) or owner.spellcasting_ability


def _resolve_construct_attack(
    live: _LiveCombat, owner: Combatant, construct: _Construct, target: Combatant
) -> None:
    """One melee spell attack by ``owner`` from ``construct``'s space against
    ``target``: the owner's spell attack bonus and spellcasting modifier
    (``spell_attack_magnitudes``), its hydration payload and conditions, with
    cover and distance measured from the force. The slot level scales the dice
    and the spell's level makes the hit magical. Draws the d20, then the damage
    dice on a hit, through the ordinary attack path; the one-use Help / Vex /
    Sap grants pop after. The target's readied reactions (Shield) have already
    fired, before the resolution's event slice, as for every attack: inside
    it, C13's fold would record a readied concentration spell as the
    owner's."""
    spec = CONSTRUCTS[construct.spell_id]
    target_list = [target]
    ability = _construct_spellcasting_ability(owner, construct.spell_id)
    attack_bonus, modifier = spell_attack_magnitudes(owner, ability)
    payload = _build_hydration_payload(live, caster=owner)
    pre_event_count = len(live.event_log)
    target_cell = live.actor_zone.get(target.entity_id)
    distance = live.topology.distance_ft(construct.cell, target_cell) if target_cell else None
    geometry = {
        "target_cover": _target_cover_map(
            live, owner.entity_id, target_list, origin_cell=construct.cell
        ),
        "target_distance_ft": {} if distance is None else {target.entity_id: distance},
    }
    actx = build_activity_context(
        owner,
        target_list,
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=construct.slot_level,
        base_spell_level=spec.base_level,
        spellcasting_ability=ability,
        concentration=False,
        source_passive_effects=[],
        spell_book={},
        active_effects=tuple(live.active_effects.get(owner.entity_id, [])),
        **(_monster_context_kwargs(live, owner, target_list, payload) | geometry),
    )
    resolve_activity(
        construct_attack_activity(
            spec,
            slot_level=construct.slot_level,
            attack_bonus=attack_bonus,
            damage_bonus=modifier,
        ),
        actx,
    )
    _consume_attack_roll_grants(live, owner, target_list, pre_event_count)


def _resolve_construct_attack_intent(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> None:
    """A construct's Bonus-Action repeat: move the force to ``target_zone_id``
    (when given), attack, and end the owner's Hide (SRD 5.2 Hide: the
    condition ends "immediately after ... you make an attack roll"). A no-op
    for every other intent; ``_construct_attack_failure`` validated this one
    before anything was spent."""
    construct = _repeated_construct(live, current, intent)
    target = _find_combatant(live, intent.target_id) if intent.target_id else None
    if construct is None or target is None:
        return
    if intent.target_zone_id is not None:
        construct.cell = intent.target_zone_id
    _resolve_construct_attack(live, current, construct, target)
    _break_hide_on_attack_or_verbal_cast(live, current, intent, None)


def _end_anchor_dependents(live: _LiveCombat, event: EffectExpired) -> None:
    """Remove every construct and summon whose concentration anchor just
    expired: the force "lasts for the duration" of its concentration spell,
    and a summoned creature "disappears ... when the spell ends", so a broken
    or dropped concentration, the spell's end, its owner's death or
    Incapacitated condition, and a recast all end them here. A summon leaves
    with ``spell_ended`` at the spell's maximum duration and with
    ``concentration_drop`` on every other path."""
    identity = (event.target_id, event.effect_id, event.origin)
    for construct_id in [cid for cid, c in live.constructs.items() if c.anchor == identity]:
        del live.constructs[construct_id]
    reason: CombatantLeftReason = (
        "spell_ended" if event.reason == "duration" else "concentration_drop"
    )
    for summon_id in [sid for sid, s in live.summons.items() if s.anchor == identity]:
        _leave_roster(live, summon_id, reason)


# ── C21 roster summons (SRD 5.2 Summon Dragon) ─────────────────────────────


def _grid_cells_by_distance(topology: GridTopology, origin: str, radius_ft: int) -> list[str]:
    """Every valid cell within ``radius_ft`` of ``origin`` (Chebyshev), nearest
    first, ties broken by row, then column: a fixed scan order, so the same
    combat always places a summon in the same space."""
    col, row = parse_cell(origin)
    reach = radius_ft // topology.cell_size_ft
    ranked = sorted(
        (max(abs(dc), abs(dr)), row + dr, col + dc)
        for dr in range(-reach, reach + 1)
        for dc in range(-reach, reach + 1)
    )
    return [cid for _, r, c in ranked if topology.is_valid_cell(cid := cell_id(c, r))]


def _summon_placement(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> tuple[str | None, CastFailedReason | None]:
    """Where a summon cast seats its creature, ``(cell, None)``, or why it
    cannot, ``(None, reason)``.

    SRD 5.2 Summon Dragon: "It manifests in an unoccupied space that you can
    see within range". A space is legal when the caster can measure it, it is
    within the spell's range with line of sight and not behind total cover,
    the caster is not Blinded (SRD 5.2: "You can't see") unless its Blindsight
    reaches the space (``_blindsight_reaches_zone``), and it is one of the
    grid's own cell ids (``col,row``) that no living creature occupies. An
    explicit ``target_zone_id`` must be legal: an invalid, non-canonical or
    occupied cell is ``"target_invalid"``, one beyond range or out of sight
    ``"out_of_range"``. Without one, the first legal cell of the fixed scan
    outward from the caster. A creature ``target_id`` plays no part: the
    spell targets a space.
    """
    caster_cell = live.actor_zone.get(current.entity_id)
    spell = get_lib_loader().get_spell(intent.spell_id or "")
    range_ft = (
        spell.range.value
        if spell is not None and spell.range.units == SpellRangeUnits.FEET
        else None
    )
    if caster_cell is None or range_ft is None:
        return None, "out_of_range"
    grid = live.topology
    occupied = _occupied_cells(live, exclude=())
    blinded = is_condition_active(Condition.BLINDED, _condition_names(current))

    def _free(cell: str) -> bool:
        # ``is_valid_cell`` parses with ``int()``, which also reads "1, 1": a
        # creature seated there would match no other position check.
        return (
            grid.is_valid_cell(cell) and cell == cell_id(*parse_cell(cell)) and cell not in occupied
        )

    def _seen(cell: str) -> bool:
        return not blinded or _blindsight_reaches_zone(live, current, cell)

    def _in_sight(cell: str) -> bool:
        return (
            live.topology.distance_ft(caster_cell, cell) is not None
            and _in_range_with_los(live.topology, caster_cell, cell, range_ft)
            and _seen(cell)
        )

    if intent.target_zone_id is not None:
        if not _free(intent.target_zone_id):
            return None, "target_invalid"
        if not _in_sight(intent.target_zone_id):
            return None, "out_of_range"
        return intent.target_zone_id, None
    cell = next(
        (
            c
            for c in _grid_cells_by_distance(grid, caster_cell, range_ft)
            if _free(c) and _in_sight(c)
        ),
        None,
    )
    return (cell, None) if cell is not None else (None, "out_of_range")


#: The Foundry key an effect uses to add typed damage parts to an attack
#: (SRD 5.2 True Strike).
_EFFECT_DAMAGE_PARTS_KEY: Final = "system.damage.parts"
#: The same extra damage written as a typed bonus string (``"2d6[radiant]"``).
_EFFECT_DAMAGE_TYPED_BONUS_KEY: Final = "system.damage.base.bonus"


def _damage_type_offer(activities: list[Any]) -> set[str]:
    """Every damage type the activities ALREADY list for a player to pick.

    Only multi-typed parts count: a single-typed part is not a choice, and
    naming its type explicitly must not be mistaken for an invalid pick.
    """
    offered: set[str] = set()
    for activity in activities:
        damage = getattr(activity, "damage", None)
        for part in (getattr(damage, "parts", None) or ()):
            types = list(getattr(part, "types", None) or ())
            if len(types) > 1:
                offered.update(types)
    return offered


def _damage_type_choices(intent: PlayerIntent, activities: list[Any]) -> dict[str, str]:
    """The caster's damage-type choice, keyed by activity id.

    The book leaves the type to the player for a part that lists several
    ("choose the damage type"); without a choice the resolvers fall back to
    the first listed, so the intent carries one and it must name a type the
    activity actually offers.
    """
    chosen = intent.damage_type
    if not chosen:
        return {}
    return {activity.id: chosen for activity in activities if getattr(activity, "id", None)}


def _effect_damage_parts(
    live: _LiveCombat, attacker: Combatant, weapon: Weapon | None
) -> tuple[tuple[str, str], ...]:
    """Typed damage parts the attacker's live effects add to THIS swing.

    SRD 5.2 True Strike writes its extra damage as
    ``system.damage.parts = [[formula, type]]`` on the effect rider. Effects
    naming a specific weapon (an enchantment) only count for that weapon.
    """
    out: list[tuple[str, str]] = []
    for effect in live.active_effects.get(attacker.entity_id, ()):
        if effect.disabled:
            continue
        named = effect.flags.get(ENCHANTED_WEAPON_FLAG)
        if named is not None and (weapon is None or named != weapon.slug):
            continue
        for change in effect.changes:
            if change.key == _EFFECT_DAMAGE_PARTS_KEY:
                out.extend(_parse_damage_parts(change.value))
            elif change.key == _EFFECT_DAMAGE_TYPED_BONUS_KEY:
                # The ranged rider states the same extra damage as a TYPED
                # bonus (``"2d6[radiant]"``) instead of a parts list, so it
                # is read here rather than dropped.
                out.extend(_parse_typed_bonus(change.value))
    return tuple(out)


def _parse_typed_bonus(value: object) -> list[tuple[str, str]]:
    """``"2d6[radiant]"`` as one ``(formula, type)`` pair; a bare formula
    (no ``[type]``) carries no damage type and is skipped rather than
    guessed."""
    text = str(value).strip()
    match = re.fullmatch(r"(.+)\[([a-z]+)\]", text)
    if match is None:
        return []
    formula, damage_type = match.group(1).strip(), match.group(2)
    return [(formula, damage_type)] if formula else []


def _parse_damage_parts(value: object) -> list[tuple[str, str]]:
    """``[[formula, type], ...]`` from an effect change, as pairs."""
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in parsed:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            formula, damage_type = entry
            if isinstance(formula, str) and isinstance(damage_type, str):
                pairs.append((formula, damage_type))
    return pairs


def _apply_rider_effects_on_attack(
    live: _LiveCombat,
    caster: Combatant,
    spell: Any,
    activities: list[Any],
    actx: Any,
) -> None:
    """Apply the effect riders that name the activities this cast resolves.

    SRD 5.2 True Strike ships one ``EnchantActivity`` carrying two effects,
    each with ``riders.activity`` pointing at the attack it belongs to (the
    melee one and the ranged one). A cast names ONE attack activity, so the
    rider that names it is the one that fires: without this the spell's
    extra damage — "the attack's extra damage increases when you reach
    character levels 5, 11, and 17" — is never applied, and without the
    scope a cast without an explicit choice would fire both.
    """
    chosen = {getattr(activity, "id", None) for activity in activities}
    chosen.discard(None)
    if not chosen:
        return
    cast_level = (actx.slot_level or actx.base_spell_level or 0) if actx else 0
    for carrier in getattr(spell, "activities", ()) or ():
        for ref in getattr(carrier, "effects", ()) or ():
            riders = getattr(ref, "riders", None)
            if riders is None or not chosen.intersection(riders.activity or ()):
                continue
            _apply_one_effect_ref(live, caster, spell, ref)




def _apply_one_effect_ref(
    live: _LiveCombat,
    caster: Combatant,
    spell: Any,
    ref: Any,
) -> None:
    """Build one ``ActiveEffect`` from ``ref`` and seat it on ``caster``.

    This runs BEFORE the attack context exists (the attack has to see the
    effect), so it builds the effect directly from the spell's passive
    definitions instead of borrowing the activity-effects helper, which
    needs that context. Change values that carry an ``@``-token are
    resolved here: True Strike's extra damage is
    ``(floor((@details.level + 1) / 6))d6``, and leaving the token
    unresolved made the damage roll raise at play time.
    """
    source = None
    for passive in getattr(spell, "passive_effects", ()) or ():
        if passive.id == ref.id:
            source = passive
            break
    if source is None or source.disabled:
        return
    resolved = source.model_copy(
        update={
            "changes": [
                change.model_copy(update={"value": _resolve_caster_tokens(change.value, caster)})
                for change in source.changes
            ]
        }
    )
    applied = passive_effect_to_active_effect(
        resolved,
        target_id=caster.entity_id,
        caster_id=caster.entity_id,
        concentration=bool(getattr(spell, "concentration", False)),
    )
    # ``_emit`` routes EffectApplied through ``_emit_apply_effect_applied``,
    # which is what appends to ``live.active_effects``: appending here too
    # seated the same effect twice and doubled True Strike's extra damage.
    _emit(live, EffectApplied(actor_id=caster.entity_id, effect=applied))


def _resolve_caster_tokens(value: object, caster: Combatant) -> object:
    """The character level behind ``@details.level`` in an effect value."""
    if not isinstance(value, str) or "@details.level" not in value:
        return value
    return value.replace("@details.level", str(caster.character_level))


def _cast_activity_selection_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed`` for a spell whose activities are ALTERNATIVES and which
    the cast did not choose between: ``None`` for every other intent.

    SRD 5.2 Find Steed ships two activities, "Summon Steed" and "Summon
    Flying Steed". Firing both spent one slot and seated two horses;
    guessing one would silently override the player (§5). The cast is
    therefore refused before the slot is spent, naming the choice. A
    spell with one activity needs no ``activity_id``.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id or intent.activity_id:
        return None
    spell = get_lib_loader().get_spell(intent.spell_id)
    if spell is None or len(spell.activities) <= 1:
        return None
    # "target_invalid" named the wrong cause: the target may be perfectly
    # legal, what is missing is which of the spell's activities was meant.
    return CastFailed(
        actor_id=current.entity_id, spell_id=intent.spell_id, reason="activity_ambiguous"
    )


#: The DM-adjudicated circumstance each summoning spell needs when the player
#: names a form the engine has no stat block for. SRD 5.2 Find Familiar:
#: "... or another Beast that has a Challenge Rating of 0" — the book names
#: the set of legal forms and leaves which one it is to the table, exactly as
#: it leaves "is it night" to the DM. The engine therefore ASKS instead of
#: refusing: the answer travels back under ``input_key`` in
#: ``PlayerIntent.adjudications`` and the cast then seats the DM's pick
#: through ``PlayerIntent.form_id``. The form named in ``question`` is filled
#: from the intent, so the DM sees which creature the player proposes.
#:
#: A form the book does NOT allow must not ask the DM — a refusal is the
#: honest answer there, since there is nothing to adjudicate.
_ADJUDICATED_SUMMONS: Final[dict[str, tuple[str, str]]] = {
    "find-familiar": (
        "Find Familiar seats only a Beast of Challenge Rating 0 outside the",
        "spell's printed list. May the familiar take the form '{form}'?",
    ),
}

#: The forms SRD 5.2 Find Familiar allows outside its printed list. The rule
#: allows ANY Beast with Challenge Rating 0; the engine can seat only the
#: stat blocks it has, and these are the CR 0 Beasts in the shipped corpus.
#: A CR 0 Beast outside this set is still legal at the table, so it must reach
#: the DM rather than be turned away by a list the book never wrote.
#: The stat block a DM-approved form seats when the corpus has no entry of
#: its own. SRD 5.2 Find Familiar: "a spirit that takes an animal form you
#: choose" — and a familiar's stat block, not its name, decides its numbers.
#: The corpus ships every CR 0 Beast the printed list names, so a form that
#: reaches the DM is seated on the nearest legal stat block, which is the
#: one the printed list gives for a tiny Beast. The DM approved the FORM;
#: naming it is the DM's, and inventing hit points from a name would be a
#: guess the engine does not make.
_CR0_BEAST_BASE: Final[str] = "weasel"

_CR0_BEAST_FORMS: Final[frozenset[str]] = frozenset(
    {
        "badger",
        "crab",
        "deer",
        "goat",
        "jackal",
        "piranha",
        "seahorse",
        "venomous-snake",
    }
)

#: The DM-adjudicated circumstances a spell needs before it can resolve, by
#: ``PlayerIntent.adjudications`` key. The engine asks for what the book
#: leaves to the DM and changes nothing until the answer arrives (§8); it
#: never assumes the circumstance.
_ADJUDICATED_CASTS: Final[dict[str, tuple[str, str]]] = {
        # SRD 5.2 Create Undead: "You can cast this spell only at night."
        # "Night" is a fact about the world, not about the caster.
        "create-undead": (
            "create-undead:night",
            "Create Undead can be cast only at night. Is it night here?",
        ),
}


def _adjudication_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """Ask the DM for the circumstance an adjudicated cast depends on, and
    refuse the cast until the answer is here.

    The engine emits ``AdjudicationRequested`` naming the key and the
    question, then ``CastFailed(reason="target_invalid")`` — both BEFORE
    the slot or the Action is spent, so a refused cast leaves the turn
    untouched. ``None`` when the cast needs no such answer, or when the
    answer it carries is ``True``.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id:
        return None
    spec = _ADJUDICATED_CASTS.get(intent.spell_id)
    if spec is None:
        return None
    key, question = spec
    answers = intent.adjudications or {}
    if answers.get(key) is True:
        return None
    _emit(
        live,
        AdjudicationRequested(
            actor_id=current.entity_id,
            question=question,
            input_key=key,
            source_id=intent.spell_id,
        ),
    )
    return CastFailed(
        actor_id=current.entity_id, spell_id=intent.spell_id, reason="target_invalid"
    )


def _summon_form_question(question: tuple[str, str], form: str | None) -> str:
    """The DM question, with the form the player named filled in."""
    return " ".join(question).replace("{form}", str(form or "").strip())


def _summon_form_is_adjudicable(form: str | None) -> bool:
    """Whether a form outside a spell's printed list is a fact about the
    world the DM can settle, rather than something the book forbids.

    SRD 5.2 Find Familiar: "... or another Beast that has a Challenge Rating
    of 0." The rule allows the set; which Beast it is is not in the corpus.
    The engine therefore asks. A CR 0 Beast from beyond the shipped stat
    blocks is still allowed here — refusing it would claim a list the book
    never wrote — and a name that is not a Beast of Challenge Rating 0 at
    all ("imp", a Fiend; "dragon") is not.
    """
    key = (form or "").strip().lower()
    return key in _CR0_BEAST_FORMS


def _summon_cast_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed`` for a summon cast whose creature has no legal space
    (``_summon_placement``'s reason), so the refusal spends no slot and no
    Action; ``None`` for every other intent."""
    spell_id = intent.spell_id or ""
    if intent.intent_type != "cast_spell" or CONJURATION_ALLOWLIST.get(spell_id) != "summon":
        return None
    # SRD 5.2 Find Steed: "a loyal, Medium Celestial, Fey, or Fiend" — the
    # player names the form. The spell states no default, so a cast that
    # names none (or an unknown one) is refused BEFORE the slot is spent,
    # rather than seated as an arbitrary steed (§8: no silent choice).
    spec = SUMMONS.get(spell_id)
    if spec is not None and spec.resolve(intent.form_id) is None:
        # A form outside the spell's printed list is not automatically illegal:
        # SRD 5.2 Find Familiar allows "another Beast that has a Challenge
        # Rating of 0", and which one it is is a fact about the world the
        # engine cannot know. Ask the DM and change nothing until the answer
        # arrives (§8), rather than turning a legal form away with a reason
        # that names the wrong cause. A form the book forbids (a Celestial
        # familiar that is not a CR 0 Beast) is still refused, with no
        # round trip that could only end in the same refusal.
        question = _ADJUDICATED_SUMMONS.get(spell_id)
        answer_key = f"{spell_id}:form"
        if _summon_form_is_adjudicable(intent.form_id):
            if (intent.adjudications or {}).get(answer_key) is True:
                return None
            _emit(
                live,
                AdjudicationRequested(
                    actor_id=current.entity_id,
                    question=_summon_form_question(question, intent.form_id),
                    input_key=answer_key,
                    source_id=spell_id,
                ),
            )
        return CastFailed(
            actor_id=current.entity_id, spell_id=spell_id, reason="target_invalid"
        )
    _, reason = _summon_placement(live, current, intent)
    if reason is None:
        return None
    return CastFailed(actor_id=current.entity_id, spell_id=spell_id, reason=reason)


def _summon_command_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """What a summoned creature cannot be commanded to do (C21): anything off
    its stat block. ``None`` for every other creature.

    ``CastFailed(reason="no_spellcasting")`` for a cast or a readied spell:
    the Draconic Spirit's stat block has no Spellcasting, and SRD 5.2 Ready
    says "When you Ready a spell, you cast it as normal".
    ``AttackFailed(reason="action_unavailable")`` for an attack that names no
    stat-block action — a plain attack, a weapon, or an Unarmed Strike ("a
    melee attack that involves you using your body to damage, grapple, or
    shove a target"), so a Grapple or a Shove too: its attacks are its stat
    block's (``_stat_block_attack_failure`` vets the one a command names).
    Every other action stands — SRD 5.2 Monsters: "A monster can take the
    actions in this section or take one of the actions available to all
    creatures", Dash, Disengage, Dodge, Help and Hide among them. A refusal
    spends nothing.
    """
    if current.entity_id not in live.summons:
        return None
    if intent.intent_type == "cast_spell" or (intent.intent_type == "ready" and intent.spell_id):
        return CastFailed(
            actor_id=current.entity_id, spell_id=intent.spell_id or "", reason="no_spellcasting"
        )
    if intent.intent_type in ("grapple", "shove") or (
        intent.intent_type == "attack" and not intent.stat_block_action_id
    ):
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="action_unavailable"
        )
    return None


def _summon_under_command_from_caster(
    live: _LiveCombat, caster: Combatant, actor_id: str
) -> str | None:
    """The summon of ``caster`` a command given on this turn belongs to.

    SRD 5.2 Summon Dragon: "It obeys your verbal commands (no action
    required by you)." The command costs the summoner no action, so it is
    given on the SUMMON's turn: the summon is the acting entity, the caster
    is its owner. A command the owner submits instead goes to its first
    summon still standing. ``None`` when neither reading finds one.
    """
    _own = _summon_owner(live, actor_id)
    if _own is not None and _own == caster.entity_id:
        return actor_id
    living = [
        c.entity_id
        for c in live.initiative
        if live.summons.get(c.entity_id) is not None
        and live.summons[c.entity_id].owner_id == caster.entity_id
        and c.is_alive
        and c.entity_id not in live.dead_ids
    ]
    return living[0] if living else None


def _summon_stat_block_slug(live: _LiveCombat, entity_id: str) -> str | None:
    """The stat block a summon acts from.

    ``_current_stat_block_slug`` answers for a creature the roster knows by
    ``monster_template_slug``; a summon instead records the block it was
    seated on (``_Summon.stat_block_slug``) — the Draconic Spirit's Rend and
    Multiattack live there.
    """
    summon = live.summons.get(entity_id)
    return summon.stat_block_slug if summon is not None else None


def _summon_owner(live: _LiveCombat, entity_id: str) -> str | None:
    """The caster a summon obeys, ``None`` for every other creature."""
    summon = live.summons.get(entity_id)
    return summon.owner_id if summon is not None else None


#: The stat-block action whose resolution is a SEQUENCE of other actions
#: ("The spirit makes a number of Rend attacks ..."). Naming it as one
#: command stays a refusal; its parts resolve on the summon's own turn.
_MULTIATTACK_ACTION_SLUG: Final = "multiattack"

def _commanded_by_owner(live: _LiveCombat, current: Combatant, intent: PlayerIntent) -> bool:
    """True when ``current`` was handed ``intent`` from outside its own turn.

    A command a table issues for a stat block names ONE swing; a summon
    spending its own turn on the command it carries resolves the whole
    action, Multiattack included (``_run_summon_commanded_turn``).
    """
    return intent.target_id is not None

def _stat_block_resolved_activities(
    found: tuple[Monster, MonsterAction],
) -> list[Any]:
    """The activities a stat-block action actually resolves.

    A Multiattack carries no attack of its own: its resolution is the parts
    its description lists (the Draconic Spirit's "two Rend attacks").
    Anything else is its own activities, as a monster's turn resolves them.
    """
    monster, action = found
    if len(action.activities) == 1 and isinstance(action.activities[0], UtilityActivity):
        parts = expand_action_to_parts(monster, action)
        if parts:
            return [activity for _, activity in parts]
    return list(action.activities)

def _resolve_monster_attacks_on(
    live: _LiveCombat,
    current: Combatant,
    target: Combatant | None,
    monster: Monster,
    action: MonsterAction,
    activities: Sequence[Any],
) -> None:
    """Resolve ``activities`` of ``action`` against ``target``.

    A Multiattack keeps the shape a monster's own turn gives it: its listed
    parts each swing, the auxiliary actions it uses included; a single
    activity resolves once. Shared by a monster's turn and by the command a
    summon carries out, so the two paths cannot drift apart.
    """
    targets = [] if target is None else [target]
    parts = expand_action_to_parts(monster, action)
    if not parts:
        _resolve_monster_attack_activities(live, current, targets, list(activities))
        return
    for _, activity in parts:
        _resolve_monster_attack_activities(live, current, targets, [activity])

def _run_summon_commanded_turn(live: _LiveCombat, current: Combatant) -> bool:
    """The summon's turn spent on its owner's command (C21).

    SRD 5.2 Summon Dragon: "It obeys your verbal commands (no action
    required by you)." A summon has no AI of its own, so a command the owner
    gave it — an ``attack`` naming a ``stat_block_action_id``, vetted by
    ``_stat_block_attack_failure`` exactly as a monster's own swing is — is
    what its turn resolves. ``True`` when a command was carried out (and the
    turn ended); ``False`` when there is none, leaving the uncommanded Dodge
    to ``_run_uncommanded_summon_turn``.
    """
    action_id = live.summon_commands.pop(current.entity_id, None)
    slug = _summon_stat_block_slug(live, current.entity_id) or live.monster_slug_by_entity.get(
        current.entity_id
    )
    if action_id is None or slug is None:
        return False
    found = _stat_block_action(slug, action_id)
    if found is None:
        # The action the command named is gone from the stat block (a shape
        # change, a stale command): fall back to the uncommanded Dodge rather
        # than inventing an attack.
        return False
    candidates = _select_monster_targets(live, current)
    chosen_target = _lowest_hp_target(candidates)
    _emit(
        live,
        IntentSubmitted(
            actor_id=current.entity_id,
            intent_type="attack",
            target_id=chosen_target.entity_id if chosen_target is not None else None,
        ),
    )
    activities = _stat_block_resolved_activities(found)
    monster = get_lib_loader().get_monster(slug)
    if monster is not None:
        live.monster_action_uses_by_entity.setdefault(
            current.entity_id, _hydrate_monster_action_uses(monster)
        )
    # The command IS the action: it resolves whole (a Multiattack fans out to
    # its listed parts), then the summon's action is spent and its turn ends.
    _resolve_monster_attacks_on(live, current, chosen_target, found[0], found[1], activities)
    _mark_monster_action_used(live, current, found[1])
    current.action_available = False
    _end_turn_and_advance(live, current.entity_id)
    return True


def _summon_id(live: _LiveCombat, owner_id: str, stat_block_slug: str) -> str:
    """``summon:<owner>:<stat block>:<n>``, ``n`` counting the owner's summons
    from 1: a recast or a re-summon never reuses an id."""
    count = live.summon_counts.get(owner_id, 0) + 1
    live.summon_counts[owner_id] = count
    return f"summon:{owner_id}:{stat_block_slug}:{count}"


def _summon_insert_index(live: _LiveCombat, owner_id: str) -> int:
    """The roster slot a new summon of ``owner_id`` takes: right after its
    owner and after the owner's summons already acting there. SRD 5.2 Summon
    Dragon: "the creature shares your Initiative count, but it takes its turn
    immediately after yours" — a slot, never a re-sort (the tie-break by
    Dexterity could put the creature ahead of its caster)."""
    ids = [c.entity_id for c in live.initiative]
    index = ids.index(owner_id) + 1
    while (
        index < len(ids)
        and (summon := live.summons.get(ids[index])) is not None
        and summon.owner_id == owner_id
    ):
        index += 1
    return index


def _summon_display_name(request: SummonRequest) -> str:
    """The name the seated creature goes by: the form the player chose where
    the spell offers one, else the stat block's own name."""
    form = (request.form or "").strip()
    return form.replace("-", " ").title() if form else ""


def _summon_combatant(
    owner: Combatant,
    monster: Monster,
    *,
    entity_id: str,
    ac: int,
    hp: int,
    magnitudes: StatBlockMagnitudes,
    display_name: str | None = None,
) -> Combatant:
    """The summoned creature as a ``Monster`` combatant acting from
    ``monster``'s stat block at its summoner's numbers: ``ac`` and ``hp`` as
    the spell's bonuses made them, the magnitudes' Proficiency Bonus, and the
    flat to-hit on the legacy ``attack_bonus`` too. It shares its owner's
    Initiative count and enters with its full walking Speed.

    ``display_name`` names a form the player chose where the spell offers one
    (Find Familiar's "another Beast of CR 0"): the numbers are the stat
    block's, the name is the table's. It defaults to the block's own name.
    """
    return Combatant(
        entity_id=entity_id,
        entity_type="Monster",
        name=display_name or monster.name,
        initiative=owner.initiative,
        hp_current=hp,
        hp_max=hp,
        creature_type=monster.creature_type.value,
        attack_bonus=magnitudes.attack_bonus,
        movement_remaining=monster.movement.walk or 0,
        **(
            _stat_block_fields(monster)
            | {"ac": ac, "proficiency_bonus_override": magnitudes.proficiency_bonus}
        ),
    )


def _seat_summon(live: _LiveCombat, caster: Combatant, request: SummonRequest) -> None:
    """Seat every creature ``request`` raises, one call per body.

    SRD 5.2 Animate Dead: "Choose a pile of bones or a corpse ... The target
    becomes an Undead creature", one body at the spell's base level and
    "1 + @scaling.increase * 2" of them at a higher slot. ``request.count``
    is that formula already evaluated; a spell that raises one carries 1.
    """
    for _ in range(max(1, request.count)):
        _seat_one_summon(live, caster, request)


def _seat_one_summon(live: _LiveCombat, caster: Combatant, request: SummonRequest) -> None:
    """Seat the creature ``request`` summons and announce it with
    ``CombatantJoined``. Draws nothing.

    SRD 5.2 Summon Dragon: "Use the spell slot's level for the spell's level in
    the stat block." The spell's ``bonuses`` give the Draconic Spirit's "AC 14
    + the spell's level" and "HP 50 + 10 for each spell level above 5", and its
    ``match`` flags the summoner's spell attack and Proficiency Bonus; the
    Multiattack count reads the slot level. The creature records its caster's
    concentration anchor, so it leaves when that concentration ends."""
    _slug = SUMMONS[request.spell_id].resolve(request.form)
    if _slug is None and request.form:
        # A form the spell's printed list does not carry was approved by the DM
        # (_ADJUDICATED_SUMMONS): the corpus has no stat block under that name,
        # so the creature is seated on the CR 0 Beast block instead of the
        # spell failing after its slot was already spent. Reaching here without
        # that approval is impossible — _summon_cast_failure refuses first.
        if _summon_form_is_adjudicable(request.form):
            _slug = _CR0_BEAST_BASE
    _skip = _slug is None
    monster = None if _skip else get_lib_loader().get_monster(_slug)
    # The registry test pins that every ``SUMMONS`` stat block loads with an AC.
    assert monster is not None
    assert monster.ac is not None
    ability = _construct_spellcasting_ability(caster, request.spell_id)
    spell_attack, modifier = spell_attack_magnitudes(caster, ability)
    roll_data = SummonRollData(level=request.slot_level, mod=modifier)
    ac = monster.ac + evaluate_summon_formula(request.bonuses.ac, roll_data)
    hp = monster.hp + evaluate_summon_formula(request.bonuses.hp, roll_data)
    magnitudes = summon_magnitudes(
        monster,
        request.match,
        spell_attack_bonus=spell_attack,
        proficiency_bonus=proficiency_bonus_of(caster),
        attack_damage_bonus=evaluate_summon_formula(request.bonuses.attack_damage, roll_data),
    )
    count = summon_attack_count(monster, roll_data)
    entity_id = _summon_id(live, caster.entity_id, monster.slug)
    index = _summon_insert_index(live, caster.entity_id)
    after = live.initiative[index - 1].entity_id
    _insert_into_roster(
        live,
        _summon_combatant(
            caster,
            monster,
            entity_id=entity_id,
            ac=ac,
            hp=hp,
            magnitudes=magnitudes,
            display_name=_summon_display_name(request),
        ),
        index,
        zone_id=request.cell,
    )
    live.monster_slug_by_entity[entity_id] = monster.slug
    live.monster_action_uses_by_entity[entity_id] = _hydrate_monster_action_uses(monster)
    live.summons[entity_id] = _Summon(
        entity_id=entity_id,
        owner_id=caster.entity_id,
        spell_id=request.spell_id,
        stat_block_slug=monster.slug,
        slot_level=request.slot_level,
        anchor=_anchor_identity(request.spell_id, caster.entity_id),
        magnitudes=magnitudes,
        attacks_per_action=count if count is not None else multiattack_count(monster),
    )
    _emit(
        live,
        CombatantJoined(
            entity_id=entity_id,
            name=_summon_display_name(request),
            stat_block_slug=monster.slug,
            origin_caster_id=caster.entity_id,
            spell_id=request.spell_id,
            initiative_count=caster.initiative,
            after_entity_id=after,
            zone_id=request.cell,
            hp_max=hp,
            ac=ac,
        ),
    )


def _run_uncommanded_summon_turn(live: _LiveCombat, current: Combatant) -> None:
    """A summon's turn with no command. SRD 5.2 Summon Dragon: "It obeys your
    verbal commands (no action required by you). If you don't issue any, it
    takes the Dodge action and uses its movement to avoid danger." A summon
    that cannot take the Action (Incapacitated, or its Action already spent on
    a command) passes instead. It moves nowhere and draws nothing."""
    actor_id = current.entity_id
    if current.action_available and not conditions_block_actions(_condition_names(current)):
        _emit(live, IntentSubmitted(actor_id=actor_id, intent_type="dodge"))
        _set_dodging(live, actor_id)
    else:
        _emit(live, IntentSubmitted(actor_id=actor_id, intent_type="pass"))
    _end_turn_and_advance(live, actor_id)


def _report_save_without_consequence(
    live: _LiveCombat,
    caster: Combatant,
    spell: Spell | None,
    actx: ActivityResolutionContext | None,
    pre_event_count: int,
) -> None:
    """Ask the DM about a spell whose failed save changes NOTHING.

    SRD 5.2 spells state their failure branch in prose. The corpus turns most
    of them into machine-readable ``effects`` / ``riders``; a handful carry
    only the save, while the spell's own text names the consequence — Grease:
    "each creature ... must succeed on a Dexterity saving throw or have the
    Prone condition", Sleet Storm: "... or have the Prone condition",
    Telekinesis: "... is Restrained", Earthquake: "... knocked Prone". When the
    data does not say and the text does, the engine must not report a save and
    stop (§7) nor invent the condition from prose (§2): it asks the DM, which
    is what §8 requires of a decision the rule leaves to the table.

    Emitted ONCE per cast, only when a save actually failed, and emitting
    does not roll back the resolution: the slot, the Action and the roll are
    spent as the book says. ``input_key`` is ``"<spell>:fail_effect"``.
    """
    from dnd5e_engine.activities.conjuration import SAVE_CONSEQUENCES

    spell_id = getattr(spell, "slug", None) if spell is not None else None
    if not spell_id or actx is None:
        return
    named = SAVE_CONSEQUENCES.get(spell_id)
    if not named:
        return
    failed = [
        ev
        for ev in live.event_log[pre_event_count:]
        if isinstance(ev, SaveRolled) and not ev.succeeded
    ]
    if not failed:
        return
    targets = sorted({ev.target_id for ev in failed})
    _emit(
        live,
        AdjudicationRequested(
            actor_id=caster.entity_id,
            question=
                f"{spell_id} states that a failed save imposes "
                f"{', '.join(named)}, but the shipped data carries no effect "
                f"for it. Apply {', '.join(named)} to {', '.join(targets)}?",
            input_key=f"{spell_id}:fail_effect",
            source_id=spell_id,
        ),
    )


def _apply_summon_requests(
    live: _LiveCombat, caster: Combatant, actx: ActivityResolutionContext | None
) -> None:
    """Fold step 6: seat each creature this resolution summoned. Runs after the
    concentration anchor, so the creature records a live identity, and a
    same-spell recast's drop has already dismissed the old creature."""
    if actx is None:
        return
    for request in actx.summon_requests:
        _seat_summon(live, caster, request)


# ── C21 transformations (SRD 5.2 Wild Shape, Polymorph) ────────────────────


def _transform_of(live: _LiveCombat, entity_id: str) -> _Transform | None:
    return live.transforms.get(entity_id)


def _stat_block_fields(monster: Monster) -> dict[str, Any]:
    """The ``Combatant`` fields of a creature acting wholly from ``monster``'s
    stat block: its physical statistics (``_physical_stat_fields``) and its
    INT / WIS / CHA, Proficiency Bonus, proficiencies, spellcasting ability and
    legendary pools. A Polymorph form (SRD 5.2: "The target's game statistics
    are replaced by the stat block of the chosen Beast") and a summoned
    creature both take all of them."""
    scores = monster.ability_scores
    legendary_actions = _legendary_action_uses_max(monster)
    legendary_resistances = _legendary_resistance_max(monster)
    return _physical_stat_fields(monster) | {
        "intelligence": scores.int,
        "wisdom": scores.wis,
        "charisma": scores.cha,
        "proficiency_bonus_override": monster.proficiency_bonus,
        "save_proficiencies": [
            a for a in ABILITY_CODES if getattr(monster.saving_throws, a) is not None
        ],
        "skill_proficiencies": [k for k, v in monster.skills.model_dump().items() if v is not None],
        "skill_expertise": [],
        "spellcasting_ability": monster.spellcasting_ability,
        "legendary_actions_max": legendary_actions,
        "legendary_actions_remaining": legendary_actions,
        "legendary_resistances_max": legendary_resistances,
        "legendary_resistances_remaining": legendary_resistances,
    }


def _monster_senses(monster: Monster) -> CombatantSenses:
    """A stat block's special senses (SRD 5.2 §Senses: Blindsight, Darkvision,
    Tremorsense, Truesight), without its Passive Perception."""
    return CombatantSenses(
        darkvision=monster.senses.darkvision,
        blindsight=monster.senses.blindsight,
        tremorsense=monster.senses.tremorsense,
        truesight=monster.senses.truesight,
    )


def _physical_stat_fields(form: Monster) -> dict[str, Any]:
    """AC, STR / DEX / CON, Speed and movement modes, senses, damage and
    condition traits, trait mechanics, and a 5-ft reach (the corpus carries no
    melee reach) — the stat block every transformation swaps in."""
    scores = form.ability_scores
    return {
        "ac": form.ac,
        "strength": scores.str,
        "dexterity": scores.dex,
        "constitution": scores.con,
        "base_speed": form.movement.walk or 0,
        "movement_modes": CombatantMovementModes(
            climb=form.movement.climb,
            swim=form.movement.swim,
            fly=form.movement.fly,
            burrow=form.movement.burrow,
        ),
        "senses": _monster_senses(form),
        "melee_reach_ft": 5,
        "damage_resistances": list(form.damage_resistances),
        "damage_immunities": list(form.damage_immunities),
        "damage_vulnerabilities": list(form.damage_vulnerabilities),
        "condition_immunities": list(form.condition_immunities),
        "physical_resistances_nonmagical_only": False,
        "trait_mechanics": [a.mechanic for a in form.special_abilities if a.mechanic is not None],
    }


def _form_stat_fields(target: Combatant, form: Monster, source: TransformSource) -> dict[str, Any]:
    """The ``Combatant`` fields a transformation into ``form`` replaces, with
    the form's values.

    Both sources swap the physical stat block: AC, STR / DEX / CON, Speed and
    movement modes, senses, damage and condition traits, trait mechanics, and a
    5-ft reach (the corpus carries no melee reach). SRD 5.2 Polymorph: "The
    target's game statistics are replaced by the stat block of the chosen
    Beast, but the target retains its alignment, personality, creature type,
    Hit Points, and Hit Point Dice" — so it also takes the form's INT / WIS /
    CHA, Proficiency Bonus, proficiencies, spellcasting ability and legendary
    pools. SRD 5.2 Wild Shape: "you retain your creature type; Hit Points; Hit
    Point Dice; Intelligence, Wisdom, and Charisma scores; class features;
    languages; and feats. You also retain your skill and saving throw
    proficiencies and use your Proficiency Bonus for them, in addition to
    gaining the proficiencies of the creature." A druid's Proficiency Bonus is
    never below a CR 1 or lower Beast's, so the union already gives "the one in
    the stat block" whenever that is higher.
    """
    if source == "polymorph":
        return _stat_block_fields(form)
    saves = [a for a in ABILITY_CODES if getattr(form.saving_throws, a) is not None]
    skills = [k for k, v in form.skills.model_dump().items() if v is not None]
    return _physical_stat_fields(form) | {
        "save_proficiencies": [
            *target.save_proficiencies,
            *(a for a in saves if a not in target.save_proficiencies),
        ],
        "skill_proficiencies": [
            *target.skill_proficiencies,
            *(k for k in skills if k not in target.skill_proficiencies),
        ],
    }


def _apply_transform(
    live: _LiveCombat,
    target_id: str,
    form: Monster,
    *,
    source: TransformSource,
    effect: ActiveEffect,
    temp_hp: int,
) -> None:
    """Shape-shift ``target_id`` into ``form`` (which has an AC: the form gates
    check it), carried by ``effect``.

    An earlier transformation of the target ends first (Wild Shape used again;
    a second Polymorph). Then the effect lands, the replaced fields are stashed
    and swapped, a monster target acts from the form's actions, and the
    creature gains ``temp_hp`` Temporary Hit Points in its one bucket.
    Polymorph's grant vanishes when the spell ends ("These Temporary Hit Points
    vanish if any remain when the spell ends"), but only a bucket the grant
    raised is the grant's. The form's Multiattack sets the Attack action's
    count; Wild Shape keeps the creature's own Extra Attack when that is more,
    since it retains its class features.
    """
    _end_transform(live, target_id, "remove_ieffect")
    target = _find_combatant(live, target_id)
    if target is None:
        return
    _emit(live, EffectApplied(effect=effect))
    fields = _form_stat_fields(target, form, source)
    is_monster = target.entity_type != "Character"
    attacks = multiattack_count(form)
    live.transforms[target_id] = _Transform(
        entity_id=target_id,
        form_slug=form.slug,
        source=source,
        effect_id=effect.id,
        origin=effect.origin,
        stash={name: getattr(target, name) for name in fields},
        original_monster_slug=live.monster_slug_by_entity.get(target_id) if is_monster else None,
        original_action_uses=(
            live.monster_action_uses_by_entity.get(target_id) if is_monster else None
        ),
        form_proficiency_bonus=form.proficiency_bonus,
        attacks_per_action=(
            max(attacks, _own_attacks_per_action(target)) if source == "wild-shape" else attacks
        ),
        clears_temp_hp_on_end=(
            source == "polymorph" and temp_hp > live.tracked_temp_hp.get(target_id, 0)
        ),
    )
    _update_combatant(live, target_id, **fields)
    if is_monster:
        live.monster_slug_by_entity[target_id] = form.slug
        live.monster_action_uses_by_entity[target_id] = _hydrate_monster_action_uses(form)
    _clamp_movement_budget(live, target_id)
    _emit(live, TempHpApplied(target_id=target_id, amount=temp_hp))


def _end_transform(live: _LiveCombat, entity_id: str, reason: EffectExpiryReason) -> None:
    """End ``entity_id``'s transformation, if any, through its effect: a
    transformation its caster concentrates on (Polymorph) ends that
    concentration, the one path C13 cascades; any other (Wild Shape) expires
    its effect directly. Either way ``_revert_transform_on_expiry`` reverts it."""
    transform = live.transforms.get(entity_id)
    if transform is None:
        return
    identity = (entity_id, transform.effect_id, transform.origin)
    caster_id = transform.origin.split(":", 2)[-1]
    if identity in live.concentration_chain.get(caster_id, []):
        _drop_concentration(live, caster_id, reason=reason)
        return
    _emit(
        live,
        EffectExpired(
            effect_id=transform.effect_id,
            target_id=entity_id,
            origin=transform.origin,
            reason=reason,
        ),
    )


def _revert_transform_on_expiry(live: _LiveCombat, event: EffectExpired) -> None:
    """Revert the creature whose transformation effect just expired: restore
    every stashed field and the monster's own actions, re-clamp its movement,
    and — for Polymorph's own grant — empty its Temporary Hit Points ("These
    Temporary Hit Points vanish if any remain when the spell ends"). Wild
    Shape's Temporary Hit Points stay."""
    transform = live.transforms.get(event.target_id)
    if transform is None or (transform.effect_id, transform.origin) != (
        event.effect_id,
        event.origin,
    ):
        return
    del live.transforms[event.target_id]
    _update_combatant(live, event.target_id, **transform.stash)
    if transform.original_monster_slug is None:
        live.monster_slug_by_entity.pop(event.target_id, None)
    else:
        live.monster_slug_by_entity[event.target_id] = transform.original_monster_slug
    if transform.original_action_uses is None:
        live.monster_action_uses_by_entity.pop(event.target_id, None)
    else:
        live.monster_action_uses_by_entity[event.target_id] = transform.original_action_uses
    _clamp_movement_budget(live, event.target_id)
    if transform.clears_temp_hp_on_end:
        live.tracked_temp_hp[event.target_id] = 0
        _update_combatant(live, event.target_id, temp_hp=0)


def _validated_beast_form(form_id: str | None) -> Monster | None:
    """The corpus Beast ``form_id`` names when a shape-shift can take it (C21:
    Wild Shape and Polymorph turn a creature into "a Beast form"): a Beast
    stat block with an Armor Class that is not a summon stat block. ``None``
    for an unknown slug, a non-Beast or an unusable stat block."""
    form = get_lib_loader().get_monster(form_id) if form_id else None
    if (
        form is None
        or form.creature_type != "beast"
        or form.ac is None
        or uses_summon_roll_data(form)
    ):
        return None
    return form


def _is_wild_shaped(live: _LiveCombat, entity_id: str) -> bool:
    """``entity_id`` is in a Wild Shape form; a polymorphed creature has no
    form of its own to leave."""
    transform = _transform_of(live, entity_id)
    return transform is not None and transform.source == "wild-shape"


def _end_wild_shape(live: _LiveCombat, entity_id: str, reason: EffectExpiryReason) -> None:
    """End ``entity_id``'s Wild Shape form, if it is in one. SRD 5.2: "You stay
    in that form ... until you use Wild Shape again, have the Incapacitated
    condition, or die. You can also leave the form early as a Bonus Action."
    A Polymorph form is its spell's to end."""
    if _is_wild_shaped(live, entity_id):
        _end_transform(live, entity_id, reason)


def _apply_transform_requests(
    live: _LiveCombat, caster: Combatant, actx: ActivityResolutionContext | None
) -> None:
    """Fold step 1 (C21): shape-shift every creature this resolution's
    ``transform`` activity asked for — Wild Shape, into the form
    ``_wild_shape_failure`` validated. SRD 5.2: "When you assume a Wild Shape
    form, you gain a number of Temporary Hit Points equal to your Druid
    level." The form is not concentration; ``_end_wild_shape`` ends it."""
    for request in actx.transform_requests if actx is not None else ():
        form = _validated_beast_form(request.form_slug)
        if form is None:  # validated before anything was spent
            continue
        _apply_transform(
            live,
            request.target_id,
            form,
            source=request.source,
            effect=ActiveEffect(
                id=_WILD_SHAPE_EFFECT_ID,
                name="Wild Shape",
                origin=f"cast:{_WILD_SHAPE_FEATURE}:{request.target_id}",
                target_id=request.target_id,
                flags={TRANSFORM_FORM_FLAG: request.form_slug},
            ),
            temp_hp=_class_levels(caster).get("druid", 0),
        )


def _challenge_rating_of(live: _LiveCombat, entity_id: str) -> float | None:
    """The Challenge Rating a Polymorph form is measured against. SRD 5.2: "a
    Challenge Rating equal to or less than the target's (or the target's level
    if it doesn't have a Challenge Rating)" — a Character's level; a monster's
    template CR (its own, not its form's, while it is transformed); ``None``
    for a template-less creature, which is refused rather than guessed, and
    for a summoned creature, whose stat block reads "CR None" (the Draconic
    Spirit's dataset ``cr`` of 0 cannot say so)."""
    target = _find_combatant(live, entity_id)
    if target is None or entity_id in live.summons:
        return None
    if target.entity_type == "Character":
        return float(target.character_level)
    transform = _transform_of(live, entity_id)
    slug = (
        transform.original_monster_slug
        if transform is not None
        else live.monster_slug_by_entity.get(entity_id)
    )
    monster = get_lib_loader().get_monster(slug) if slug else None
    return monster.cr if monster is not None else None


def _apply_transform_riders(
    live: _LiveCombat,
    caster: Combatant,
    actx: ActivityResolutionContext | None,
    pre_event_count: int,
) -> None:
    """Fold step 2 (C21): a ``TRANSFORM_RIDERS`` spell's failed save
    shape-shifts its target into the form ``_polymorph_form_failure``
    accepted. SRD 5.2 Polymorph: "The target must succeed on a Wisdom saving
    throw or shape-shift into a Beast form for the duration" and "gains a
    number of Temporary Hit Points equal to the Hit Points of the Beast form".
    A target's FIRST ``SaveRolled`` in the resolution is the spell's own; a
    later one is a roll the damage triggered (the
    ``_apply_forced_movement_riders`` rule). The form rides the spell's
    concentration effect (id and origin by the spell-effect convention), so
    C13 governs it; a successful save applies nothing and the concentration
    anchor follows."""
    carrier = actx.conjuration if actx is not None else None
    rider = TRANSFORM_RIDERS.get(carrier.source_slug) if carrier is not None else None
    if carrier is None or rider is None:
        return
    form = _validated_beast_form(carrier.form_slug)
    if form is None:  # validated before the slot was spent
        return
    judged: set[str] = set()
    for event in live.event_log[pre_event_count:]:
        if not isinstance(event, SaveRolled) or event.target_id in judged:
            continue
        judged.add(event.target_id)
        if event.succeeded:
            continue
        _apply_transform(
            live,
            event.target_id,
            form,
            source=rider.source,
            effect=ActiveEffect(
                id=f"effect:{carrier.source_slug}",
                name=carrier.source_slug.title(),
                origin=f"cast:{carrier.source_slug}:{caster.entity_id}",
                target_id=event.target_id,
                flags={"concentration": True, TRANSFORM_FORM_FLAG: form.slug},
            ),
            temp_hp=form.hp,
        )


def _end_polymorph_on_depletion(live: _LiveCombat, target_id: str) -> None:
    """SRD 5.2 Polymorph: "The spell ends early on the target if it has no
    Temporary Hit Points left." The damage fold calls this after Temporary Hit
    Points absorb a hit and before it writes Hit Points, so the rest of that
    hit lands on the creature's own Hit Points. A Wild Shape form is kept: it
    does not end with its Temporary Hit Points."""
    transform = _transform_of(live, target_id)
    if (
        transform is not None
        and transform.source == "polymorph"
        and live.tracked_temp_hp.get(target_id, 0) == 0
    ):
        _end_transform(live, target_id, "temp_hp_depleted")


#: Proficiency Bonus of a stat block whose template the loader cannot read:
#: the SRD's own base value, never a guess at a higher one.
_STAT_BLOCK_PB_BASE: Final = 2


def _template_proficiency_bonus(live: _LiveCombat, entity_id: str) -> int | None:
    """The Proficiency Bonus written on ``entity_id``'s monster template.

    Read once per resolution rather than per formula: the stat block is the
    authority for a monster's own attacks and save actions (SRD 5.2 the
    template's ``proficiency_bonus``), so the number the context carries is
    the one the printed block carries. ``None`` when no template resolves.
    """
    slug = _current_stat_block_slug(live, entity_id)
    monster = get_lib_loader().get_monster(slug) if slug else None
    return monster.proficiency_bonus if monster is not None else None

def _stat_block_magnitudes_of(live: _LiveCombat, current: Combatant) -> StatBlockMagnitudes | None:
    """A transformed actor's stat-block numbers: its current six scores (the
    form's physical ones; Wild Shape keeps its own INT / WIS / CHA) and the
    form's Proficiency Bonus, which its stat-block attacks use. A summon's are
    the ones fixed when it was seated, its summoner's to-hit and damage bonus
    included. ``None`` for any other creature."""
    transform = live.transforms.get(current.entity_id)
    if transform is None:
        summon = live.summons.get(current.entity_id)
        if summon is not None:
            return summon.magnitudes
        # A Monster acting from its own template (``advance_monster_turn``)
        # rolls at THAT stat block's scores and Proficiency Bonus: its
        # save actions (the Air Elemental's Whirlwind) state their own DC and
        # read ``@mod`` from the same scores as its attacks. Without them the
        # context fell back to a spellcasting ability the monster never has
        # and raised ``KeyError: 'charisma'``.
        if current.entity_type == "Monster":
            return StatBlockMagnitudes(
                ability_scores={
                    "str": current.strength,
                    "dex": current.dexterity,
                    "con": current.constitution,
                    "int": current.intelligence,
                    "wis": current.wisdom,
                    "cha": current.charisma,
                },
                proficiency_bonus=(
                    _template_proficiency_bonus(live, current.entity_id)
                    or _STAT_BLOCK_PB_BASE
                ),
            )
        return None
    return StatBlockMagnitudes(
        ability_scores={
            "str": current.strength,
            "dex": current.dexterity,
            "con": current.constitution,
            "int": current.intelligence,
            "wis": current.wisdom,
            "cha": current.charisma,
        },
        proficiency_bonus=transform.form_proficiency_bonus,
    )


def _current_stat_block_slug(live: _LiveCombat, entity_id: str) -> str | None:
    """The stat block ``entity_id`` acts from: its form's while it is
    transformed (C21), else its monster template's; ``None`` with neither (a
    PC, a template-less foe)."""
    transform = _transform_of(live, entity_id)
    if transform is not None:
        return transform.form_slug
    return live.monster_slug_by_entity.get(entity_id)


def _stat_block_action(
    stat_block_slug: str | None, action_id: str | None
) -> tuple[Monster, MonsterAction] | None:
    """Stat block ``stat_block_slug`` and its action ``action_id``, or ``None``
    when either is missing."""
    if not stat_block_slug or not action_id:
        return None
    monster = get_lib_loader().get_monster(stat_block_slug)
    if monster is None:
        return None
    action = next((a for a in monster.actions if a.slug == action_id), None)
    return None if action is None else (monster, action)


_ON_TURN_ACTIVATIONS: Final = frozenset({"action", "bonus", "reaction"})


def _stat_block_action_is_bonus(stat_block_slug: str | None, action_id: str) -> bool:
    """True iff every ON-TURN activity of the named stat-block action is a typed
    Bonus Action (``activation.type == "bonus"``).

    Only ``action``/``bonus``/``reaction`` activations are priced: a
    ``turnEnd`` repeat save (the Basilisk's Petrifying Gaze) rides the action
    rather than costing one, and pricing it would have made a typed Bonus
    Action read as an Action. At least one Bonus Action is required, and any
    Action among them makes the whole command an Action (the price the stat
    block prints for the package). Measured r204: 11 corpus actions carried a
    bonus activation next to a non-cost one, so the basilisk's gaze still spent
    the Action."""
    found = _stat_block_action(stat_block_slug, action_id)
    if found is None:
        return False
    kinds = {
        getattr(getattr(a, "activation", None), "type", None) for a in found[1].activities
    }
    priced = kinds & _ON_TURN_ACTIVATIONS
    return "bonus" in priced and "action" not in priced


def _stat_block_target_out_of_range(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent, activities: Sequence[Any]
) -> bool:
    """True iff the intent's named target is beyond the stat-block action's own
    reach, range or area from ``current``, out of its line of sight or behind
    total cover. ``False`` with no target or an untracked position, as the
    weapon-reach gate does.

    A templated save action (a breath weapon's cone, a fear aura's radius) is
    read twice: its typed ``range`` in feet is a placement distance (a Djinni's
    Create Whirlwind cylinder is 120 ft away, the Water Elemental's Whelm
    sphere 10 ft), and its ``target.template`` is the shape the target must
    stand in. The aim is the SAME one resolution takes
    (``_aoe_direction``: the intent's own ``direction`` first, else the step
    toward the named target), so the gate cannot pass an aim that then affects
    nobody, nor refuse one that would have hit. Before r204 this branch did not
    exist: ``_monster_attack_range_ft`` returns ``None`` for a self-centred
    area, so a commanded breath hit a named target 60 ft away."""
    target_id = intent.target_id
    attacker_zone = live.actor_zone.get(current.entity_id)
    target_zone = live.actor_zone.get(target_id) if target_id is not None else None
    activity = area_activity(activities)
    template = area_template(activity) if activity is not None else None
    if template is not None:
        if attacker_zone is None or target_zone is None:
            return False
        # A COUNTED area ("up to N creatures") names whom it affects, so its
        # typed ``range`` is the whole constraint: the target need only be
        # within it. The template is where the effect happens, not where the
        # victim must stand — the Chain Devil's Conjure Infernal Chain reaches
        # 60 ft and pulls 30 ft along its line, so a creature at 45 ft is a
        # legal target. ``creature_count == 1`` areas are also the ones
        # ``_monster_area`` skips, which is how they escaped every gate
        # before r204 (measured: the 30-ft line reached 100 ft).
        range_ft = _range_ft(activity.range)
        if creature_count(activity) is not None:
            return range_ft is not None and not _in_range_with_los(
                live.topology, attacker_zone, target_zone, range_ft
            )
        if range_ft is not None and not _in_range_with_los(
            live.topology, attacker_zone, target_zone, range_ft
        ):
            return True
        origin = target_zone if template.anchor == "target" else attacker_zone
        direction = (
            _aoe_direction(live, current.entity_id, intent) if template.directional else None
        )
        if template.directional and direction is None:
            return True
        return target_zone not in area_cells(live.topology, template, origin, direction)
    range_ft = _monster_attack_range_ft(activities, current.melee_reach_ft)
    if range_ft is None or attacker_zone is None or target_zone is None:
        return False
    return not _in_range_with_los(live.topology, attacker_zone, target_zone, range_ft)


def _shape_shifted_failure(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> CombatEvent | None:
    """The refusal for what a shape-shifted creature cannot do, or ``None``.

    ``CastFailed(reason="no_spellcasting")`` for a cast or a readied spell —
    SRD 5.2 Wild Shape: "You can't cast spells, but shapeshifting doesn't
    break your Concentration"; Polymorph: "it can't speak or cast spells";
    Ready: "When you Ready a spell, you cast it as normal". A running
    concentration is untouched. ``AttackFailed(reason="action_unavailable")``
    for a weapon attack — Wild Shape: "Your ability to handle objects is
    determined by the form's limbs rather than your own"; Polymorph: "The
    target's gear melds into the new form"."""
    if current.entity_id not in live.transforms:
        return None
    if intent.intent_type == "cast_spell" or (intent.intent_type == "ready" and intent.spell_id):
        return CastFailed(
            actor_id=current.entity_id, spell_id=intent.spell_id or "", reason="no_spellcasting"
        )
    if intent.intent_type == "attack" and intent.weapon_id:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="action_unavailable"
        )
    return None


@dataclass
class _ActionCost:
    """Action-economy classification for an intent: which budget it consumes
    and whether it is a reaction cast (which additionally emits
    ``ReactionTriggered``). ``cast_spell_for_timing`` is the timing-only spell
    fetch reused by the slot gate downstream."""

    is_bonus_action: bool
    is_reaction_cast: bool
    cast_spell_for_timing: Spell | None
    # A free ``special``-activation feature (``_FeatureInvocation.is_free_action``):
    # spends nothing and keeps the turn.
    is_free_action: bool = False


def _classify_action_cost(
    intent: PlayerIntent,
    feature_invocation: _FeatureInvocation | None,
    stat_block_slug: str | None = None,
) -> _ActionCost:
    """SRD §Action Economy — classify an intent's action cost BEFORE emitting
    IntentSubmitted. Cast spells consult their asset's typed
    ``casting_time.unit``; a feature invocation reads its resolved cost; a
    stat-block command reads the ``activation.type`` of the action it names;
    all other intents are Actions."""
    cast_spell_for_timing = (
        get_lib_loader().get_spell(intent.spell_id)
        if intent.intent_type == "cast_spell" and intent.spell_id
        else None
    )
    casting_unit = (
        cast_spell_for_timing.casting_time.unit if cast_spell_for_timing is not None else None
    )
    is_bonus_action = casting_unit == CastingTimeUnit.BONUS
    # SRD §Action Economy — a class feature is a Bonus Action when its (single)
    # activity's ``activation.type`` is ``"bonus"`` (Rage, Second Wind). A bonus
    # action does NOT end the turn, so the actor may rage then swing on the same
    # turn — the very flow exercises. ``feature_invocation`` is already
    # resolved (gate + single-activity validation) above; read its cost here.
    if feature_invocation is not None and feature_invocation.is_bonus_action:
        is_bonus_action = True
    # SRD 5.2 §Action Economy — a stat-block action's own typed
    # ``activation.type`` prices the command. A Bonus Action activity (the
    # pirate captain's Captain's Charm, the elephant's Trample) is a Bonus
    # Action, not the monster's Action: before this every command was an
    # Action, so it spent the wrong budget, left the Bonus Action unspent and
    # blocked the very Action the monster still had (measured r204).
    if (
        intent.intent_type == "attack"
        and intent.stat_block_action_id
        and _stat_block_action_is_bonus(stat_block_slug, intent.stat_block_action_id)
    ):
        is_bonus_action = True
    # SRD 5.2 magic items -- an item activation costs whatever its own typed
    # ``activation.type`` says. 36 corpus activities say "bonus" (drinking a
    # potion, lighting a tinderbox). The Boots of Speed pack entry says
    # "action" although the SRD text says Bonus Action: the engine follows the
    # corpus, and that mismatch is recorded as an open data question.
    item_is_reaction = False
    if intent.intent_type == "use_item" and intent.item_id:
        item = get_lib_loader().get_item(intent.item_id)
        if item is not None:
            # The charge-priced activity is the usual answer, but an
            # activation that spends no charges ("Click Heels" is free and
            # only starts a 600-second effect) is NOT the one
            # _item_charge_activity picks -- it prices consuming activities
            # only. Fall back to the named activity, then to the item's sole
            # activity, so a free activation still costs what its
            # ``activation.type`` says (36 corpus activations say "bonus").
            activity = _item_charge_activity(item, intent.activity_id) or _named_activity(
                item, intent.activity_id
            )
            activation = getattr(getattr(activity, "activation", None), "type", None)
            if activation == "bonus":
                is_bonus_action = True
            elif activation == "reaction":
                item_is_reaction = True
    is_reaction_cast = casting_unit == CastingTimeUnit.REACTION or item_is_reaction
    return _ActionCost(
        is_bonus_action=is_bonus_action,
        is_reaction_cast=is_reaction_cast,
        cast_spell_for_timing=cast_spell_for_timing,
        is_free_action=feature_invocation is not None and feature_invocation.is_free_action,
    )


def _spell_out_of_range(
    live: _LiveCombat,
    actor_id: str,
    intent: PlayerIntent,
    cast_spell_for_timing: Spell | None,
) -> bool:
    """SRD §Spell Range — return ``True`` if this is a targeted cast whose
    target lies beyond the spell's metric range. ``self``/``special`` ranges
    carry no metric distance and never gate. A construct placed in a named
    space is measured there instead (``_construct_cast_failure``): SRD 5.2
    Spiritual Weapon's force "appears within range", and its target need only
    be "within 5 feet of the force"."""
    if not (
        intent.intent_type == "cast_spell"
        and cast_spell_for_timing is not None
        and intent.target_id is not None
        and not (intent.target_zone_id is not None and cast_spell_for_timing.slug in CONSTRUCTS)
    ):
        return False
    spell_range = cast_spell_for_timing.range
    if spell_range.units == SpellRangeUnits.FEET:
        range_ft: int | None = spell_range.value
    elif spell_range.units == SpellRangeUnits.TOUCH:
        range_ft = 5
    else:
        range_ft = None
    if isinstance(range_ft, int) and range_ft > 0:
        caster_zone = live.actor_zone.get(actor_id)
        target_zone = live.actor_zone.get(intent.target_id)
        if (
            caster_zone is not None
            and target_zone is not None
            and not _in_range_with_los(live.topology, caster_zone, target_zone, range_ft)
        ):
            return True
    return False


def _spell_out_of_range_failure(
    live: _LiveCombat,
    actor_id: str,
    intent: PlayerIntent,
    cast_spell_for_timing: Spell | None,
) -> CombatEvent | None:
    """``CastFailed(reason="out_of_range")`` when ``intent`` is a spell-range-
    gated cast; ``None`` otherwise. One of the ``pre_resolution_gates``
    failure-builders consumed by ``submit_player_intent``."""
    if not _spell_out_of_range(live, actor_id, intent, cast_spell_for_timing):
        return None
    return CastFailed(actor_id=actor_id, spell_id=intent.spell_id or "", reason="out_of_range")


def _hellish_rebuke_target_invalid(current: Combatant, intent: PlayerIntent) -> bool:
    """SRD §Hellish Rebuke — return ``True`` if this is a Hellish Rebuke cast
    whose target is not the most-recent damager tracked on the caster."""
    return (
        intent.intent_type == "cast_spell"
        and intent.spell_id == "hellish-rebuke"
        and (current.last_damaged_by is None or intent.target_id != current.last_damaged_by)
    )


def _cast_target_invalid_failure(
    current: Combatant, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """``CastFailed(reason="target_invalid")`` for every pre-slot illegal
    cast target (Hellish Rebuke's fixed target); ``None`` otherwise. An
    area's own refusals (an unaimed Cone/Line/Cube, exclusions) are
    ``_area_target_failure``'s, which covers every intent kind. One of the
    ``pre_resolution_gates`` failure-builders consumed by
    ``submit_player_intent``."""
    if not _hellish_rebuke_target_invalid(current, intent):
        return None
    return CastFailed(actor_id=actor_id, spell_id=intent.spell_id or "", reason="target_invalid")


# SRD 5.2 Magic action: "When you take the Magic action, you cast a spell that
# has a casting time of an action or use a feature or magic item that requires
# a Magic action to be activated." The corpus does not mark which features or
# items need it, so every Action-costed cast, item use and feature use counts.
_MAGIC_ACTION_INTENTS: Final[frozenset[IntentType]] = frozenset(
    {"cast_spell", "use_item", "use_feature"}
)


def _extra_action_funds(c: Combatant, intent_type: IntentType) -> bool:
    """True when an Action Surge extra action can pay for ``intent_type`` —
    SRD 5.2: "you can take one additional action, except the Magic action".
    ``pass`` needs no action."""
    return (
        c.extra_actions_remaining > 0
        and intent_type != "pass"
        and intent_type not in _MAGIC_ACTION_INTENTS
    )


def _action_payment(c: Combatant, intent_type: IntentType) -> dict[str, Any]:
    """The initiative-slot update that pays one Action for ``intent_type``: the
    base Action while unspent, then an Action Surge extra action; empty when
    neither can pay (``pass`` once its Action is gone)."""
    if c.action_available:
        return {"action_available": False}
    if _extra_action_funds(c, intent_type):
        return {"extra_actions_remaining": c.extra_actions_remaining - 1}
    return {}


def _consume_action_budget(
    live: _LiveCombat, actor_id: str, cost: _ActionCost, intent_type: IntentType
) -> Combatant:
    """Consume the classified action-economy budget on ``actor_id``'s
    initiative slot and return the refreshed current actor. ``current`` is a
    stale snapshot; mutate via slot model_copy so subsequent reads see the
    updated state. An Action is paid by ``_action_payment`` (the base Action,
    else an Action Surge extra action); a free ``special`` activation pays nothing."""
    if cost.is_free_action:
        return _current_actor(live)
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            if cost.is_bonus_action:
                update: dict[str, Any] = {"bonus_action_available": False}
            elif cost.is_reaction_cast:
                update = {"reaction_available": False}
            else:
                update = _action_payment(c, intent_type)
            live.initiative[idx] = c.model_copy(update=update)
            break
    return _current_actor(live)


def _action_economy_gate_failure(
    current: Combatant,
    intent: PlayerIntent,
    *,
    is_bonus_action: bool,
    is_reaction_cast: bool,
) -> CombatEvent | None:
    """The action-economy budget gate for ``submit_player_intent``: returns
    the rejection event to emit (turn-keeping), or ``None`` when the intent
    may proceed to budget consumption. Raises ``IntentRejectedError`` for
    the cases with no typed event surface today: a non-cast, non-attack
    Action-costed intent with no Action left, and the FIRST swing of an
    Attack action with no Action left (this restores the pre-C14
    hard Action gate so a turn-keeping Action intent, e.g. Dash or
    Disengage, cannot be chained into a free attack sequence).

    ``attack`` gets its own branch (SRD §Extra Attack): an exhausted
    ``attacks_remaining`` is always a turn-KEEPING ``AttackFailed`` —
    unlike every other Action-costed intent, a later same-Action swing
    owes no further Action spend, so its rejection must not look like a
    fresh "no Action" failure. But the FIRST swing (``attack_action_engaged``
    False) still owes the Action itself, exactly like every other
    Action-costed intent.

    ``"pass"`` is exempt from every branch below —
    it was never an Action ("I'm done" needs no budget) and it must ALWAYS
    be accepted and end the turn. Without this exemption, every turn-
    keeping intent (attack/move/cast_spell/drop_concentration/dash/...)
    that leaves ``action_available`` False with nothing left to spend it on
    (e.g. after a multi-attack actor's swings, or after a plain Dash) has
    no way to end the turn: the generic ``not current.action_available``
    branch below would hard-reject ``pass`` itself, deadlocking the turn.

    SRD 5.2 Action Surge (C20): with the base Action spent, an unspent extra
    action admits any Action-costed intent but a Magic action, and an attack
    whose Attack action's swings are spent takes another Attack action on it
    (``_consume_attack_budget``).
    """
    if intent.intent_type == "pass":
        return None
    if is_bonus_action:
        if not current.bonus_action_available:
            # An ``attack`` intent (a stat-block Bonus Action command) reports
            # its refusal on the attack surface, like every other spent budget
            # that stops a swing; a cast keeps ``CastFailed``.
            if intent.intent_type == "attack":
                return AttackFailed(
                    actor_id=current.entity_id,
                    target_id=intent.target_id,
                    reason="no_action_economy",
                )
            return CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id or "",
                reason="no_action_economy",
            )
        return None
    if is_reaction_cast:
        if not current.reaction_available:
            return CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id or "",
                reason="no_action_economy",
            )
        return None
    extra_action = _extra_action_funds(current, intent.intent_type)
    if intent.intent_type == "attack":
        if current.attacks_remaining <= 0 and not extra_action:
            return AttackFailed(
                actor_id=current.entity_id,
                target_id=intent.target_id,
                reason="no_action_economy",
            )
        # SRD §Action Economy — the FIRST swing of the Attack action still
        # owes the hard Action requirement (a turn-keeping
        # Action intent — Dash, Disengage — must not let a same-turn attack
        # sequence resolve for free). Subsequent swings this Action
        # (``attack_action_engaged`` True) skip this: the Action was
        # already paid for, or soft-consumed, by the first swing.
        if not current.attack_action_engaged and not current.action_available and not extra_action:
            raise IntentRejectedError(
                "no_action_economy",
                f"actor_id={current.entity_id!r} has no Action remaining this turn",
            )
        return None
    if not current.action_available and not extra_action:
        if intent.intent_type == "cast_spell":
            return CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id or "",
                reason="no_action_economy",
            )
        raise IntentRejectedError(
            "no_action_economy",
            f"actor_id={current.entity_id!r} has no Action remaining this turn",
        )
    return None


def _consume_attack_budget(live: _LiveCombat, actor_id: str, current: Combatant) -> Combatant:
    """SRD §Extra Attack — soft-consume the Action for a main-hand attack:
    the Action is spent only on the FIRST swing of a multi-attack
    sequence (``attack_action_engaged`` False); a later swing this Action
    never re-pays it, and never rejects even if the Action was somehow
    already gone. Every resolved swing decrements ``attacks_remaining`` by
    one and sets ``attack_action_engaged`` True. SRD 5.2 Action Surge: once
    this Attack action's swings are spent, the next attack takes another
    Attack action on an extra action, with fresh swings. The first swing
    takes the Action's count as it stands (C21: a form adopted earlier this
    turn)."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            if not c.attack_action_engaged:
                update: dict[str, Any] = {
                    **_action_payment(c, "attack"),
                    "attack_action_engaged": True,
                    # The count is read as the Action is taken: a form adopted
                    # earlier this turn (Wild Shape is a Bonus Action) swings
                    # with the form's count.
                    "attacks_remaining": _attacks_per_action(live, c) - 1,
                }
            elif c.attacks_remaining > 0:
                update = {"attacks_remaining": c.attacks_remaining - 1}
            else:
                update = {
                    **_action_payment(c, "attack"),
                    "attacks_remaining": _attacks_per_action(live, c) - 1,
                }
            live.initiative[idx] = c.model_copy(update=update)
            break
    return _current_actor(live)


def _nick_offhand_earned(current: Combatant, weapon: Weapon | None) -> bool:
    """SRD 5.2 §Weapon Mastery — True when THIS off-hand swing carries a live
    Nick. Nick is a mastery the wielder CHOSE ("choose two" for a fighter),
    not a property of the weapon: `activities/mastery.py` refuses a printed
    mastery the combatant never chose (`_earns_mastery`), and the off-hand
    economy must read it the same way. Without this gate a character with an
    empty `weapon_masteries` list swung a printed-Nick dagger and kept its
    Bonus Action — measured r197: `bonus_action_available` stayed True while
    the same swing with a Vex/Slow light weapon correctly spent it. The
    `_earns_mastery` sentinel keeps a monster's stat block authoritative
    (`weapon_masteries is None`)."""
    return (
        weapon is not None
        and weapon.mastery == "nick"
        and _earns_mastery(current, weapon)
    )


def _is_offhand_attack_swing(
    current: Combatant, intent: PlayerIntent, weapon: Weapon | None
) -> bool:
    """SRD 5.2 §Two-Weapon Fighting — True when THIS attack intent is the
    off-hand Bonus Action swing that follows a Light main-hand weapon
    attack this same turn: the actor has already engaged its Attack action,
    swung a Light weapon (``light_weapon_swing_slug``), the off-hand swing
    hasn't already fired, the Bonus Action is still available, the weapon
    now being swung is Light, and it is a DIFFERENT weapon slug than the
    main-hand swing (SRD: "you can make an attack with a different light
    melee weapon"). ``weapon`` is the intent's resolved ``Weapon`` (``None``
    for a missing/non-weapon slug -> never Light -> never off-hand).

    MAIN-ACTION SWINGS TAKE PRIORITY: an
    Extra-Attack actor with budget left (``attacks_remaining > 0``) swinging
    a second, different Light weapon is still an ordinary Attack-action
    swing, not an automatic off-hand Bonus Action — the SRD off-hand option
    is something the wielder chooses to spend a Bonus Action on, it does
    not preempt remaining Attack-action swings. So the off-hand
    classification additionally requires EITHER the Attack-action budget is
    already exhausted (``attacks_remaining <= 0``, the common case: a
    1-attack actor, or an Extra-Attack actor who has used up its swings) OR
    the host explicitly asked for the Bonus Action swing now
    (``intent.use_bonus_action``, e.g. interleaving the off-hand attack
    before a multiattack sequence is finished).

    SRD 5.2 §Weapon Mastery — Nick: *"When
    you make the extra attack of the Light property, you can make it as
    part of the Attack action instead of as a Bonus Action."* A Nick
    off-hand weapon therefore does NOT require ``bonus_action_available``;
    the different-Light-weapon gate and the ``offhand_attack_spent``
    once-per-turn cap still apply unchanged."""
    if weapon is None or WeaponProperty.LIGHT not in weapon.properties:
        return False
    window_open = _twf_window_open(current) or (
        _nick_offhand_earned(current, weapon) and _offhand_window_open(current)
    )
    return (
        intent.intent_type == "attack"
        and current.attack_action_engaged
        and window_open
        and intent.weapon_id != current.light_weapon_swing_slug
        and (current.attacks_remaining <= 0 or intent.use_bonus_action)
    )


def _consume_offhand_attack_budget(
    live: _LiveCombat, actor_id: str, current: Combatant, weapon: Weapon | None
) -> Combatant:
    """SRD 5.2 §Two-Weapon Fighting — the off-hand swing spends the Bonus
    Action, NOT the per-Action attack budget: ``attacks_remaining`` is left
    untouched (the main-hand swing already decremented it) and
    ``attack_action_engaged`` stays as-is. Marks ``offhand_attack_spent``
    so ``_twf_window_open`` closes and a second off-hand swing this turn
    is rejected.

    SRD 5.2 §Weapon Mastery — Nick: *"When you make the extra
    attack of the Light property, you can make it as part of the Attack
    action instead of as a Bonus Action. You can make this extra attack only
    once per turn."* When the OFF-HAND ``weapon`` (the one making the extra
    attack) carries ``nick``, the Bonus Action is left UNSPENT — the swing is
    part of the Attack action. Everything else is unchanged: the
    different-Light-weapon gate and ``bonus_action_available`` pre-condition
    in ``_is_offhand_attack_swing``, the ``offhand_attack_spent``
    once-per-turn cap (the SRD's own "only once per turn"), and the
    positive-ability-mod suppression on the swing's damage."""
    is_nick = _nick_offhand_earned(current, weapon)
    update: dict[str, bool] = {"offhand_attack_spent": True}
    if not is_nick:
        update["bonus_action_available"] = False
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update=update)
            break
    return _current_actor(live)


# What pays for an ``attack`` intent's swing: the Attack action, or one of SRD
# 5.2's swings outside it — the Light property's extra attack, a Flurry of
# Blows strike, Martial Arts' Bonus Unarmed Strike, a construct's repeat
# (Spiritual Weapon: "As a Bonus Action on your later turns").
AttackFunding = Literal[
    "action", "light_offhand", "flurry", "martial_arts_bonus", "construct_bonus"
]

_UNARMED_STRIKE: Final = "unarmed-strike"
# Monk's Focus: the Flurry of Blows activity (Foundry id), and the Monk 10
# feature that adds a third strike to it.
_FLURRY_OF_BLOWS: Final = ("monks-focus", "2ghJTBhilLrFn9xT")
_HEIGHTENED_FOCUS: Final = "heightened-focus"


def _update_combatant(live: _LiveCombat, entity_id: str, **fields: Any) -> None:
    """Write ``fields`` onto ``entity_id``'s initiative slot (``model_copy``), so
    later reads of ``live.initiative`` see them."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == entity_id:
            live.initiative[idx] = c.model_copy(update=fields)
            return


def _classify_attack_funding(
    current: Combatant,
    intent: PlayerIntent,
    weapon: Weapon | None,
    *,
    repeats_construct: bool = False,
) -> AttackFunding:
    """What pays for this ``attack`` intent's swing (``"action"`` for any other
    intent), in priority order:

    * ``"construct_bonus"`` — an attack repeating a live construct its actor
      owns (``repeats_construct``, from ``_repeated_construct``; the Bonus
      Action; ``_construct_attack_failure`` validates the repeat);
    * ``"light_offhand"`` — the Light property's extra attack with a different
      Light weapon (``_is_offhand_attack_swing``: the Bonus Action, or nothing
      with Nick);
    * ``"flurry"`` — an Unarmed Strike while a committed Flurry of Blows still
      owes strikes ("You can expend 1 Focus Point to make two Unarmed Strikes as
      a Bonus Action": the Focus Point and the Bonus Action already paid);
    * ``"martial_arts_bonus"`` — an Unarmed Strike with ``use_bonus_action`` by
      an attacker whose Martial Arts is active ("Bonus Unarmed Strike. You can
      make an Unarmed Strike as a Bonus Action.");
    * ``"action"`` — the Attack action.
    """
    if repeats_construct:
        return "construct_bonus"
    if _is_offhand_attack_swing(current, intent, weapon):
        return "light_offhand"
    if intent.intent_type != "attack" or intent.weapon_id != _UNARMED_STRIKE:
        return "action"
    if current.flurry_strikes_remaining > 0:
        return "flurry"
    if intent.use_bonus_action and _martial_arts_active(current):
        return "martial_arts_bonus"
    return "action"


def _activity_needs_scaling_amount(activity: Any) -> bool:
    """True when the activity's DC formula names ``@scaling`` (Foundry fills
    it from the chosen spell level/amount). Without a chosen amount the
    formula cannot resolve, and the engine does not substitute a number.

    SRD 5.2 Hat of Many Spells: DC 10 + the spell's level.
    """
    formulas: list[str] = []
    uses = getattr(activity, "uses", None)
    formulas.append(getattr(uses, "max", "") or "")
    check = getattr(activity, "check", None)
    dc = getattr(check, "dc", None) if check is not None else None
    formulas.append(getattr(dc, "formula", "") or "")
    damage = getattr(activity, "damage", None)
    for part in getattr(damage, "parts", ()) or ():
        formulas.append(getattr(part, "bonus", "") or "")
        custom = getattr(part, "custom", None)
        formulas.append(getattr(custom, "formula", "") or "")
        scaling = getattr(part, "scaling", None)
        formulas.append(getattr(scaling, "formula", "") or "")
    heal = getattr(activity, "heal", None)
    formulas.append(getattr(heal, "formula", "") or "")
    return any("@scaling" in str(f) for f in formulas)

def _scaling_amount_named(intent: "PlayerIntent") -> int | None:
    """Названное игроком количество для `@scaling`, или None.

    Источники различаются по виду предмета: Hat of Many Spells называет
    УРОВЕНЬ заклинания (`@scaling` = «DC 10 plus the spell's level»),
    Helm of Brilliance — число камней в шлеме («taking Radiant damage equal
    to the number of gems in the helm»), что приходит как `pool_points`.
    Движок не выбирает между ними сам: берётся то, что назвал игрок.
    """
    if intent.pool_points is not None:
        return intent.pool_points
    return intent.slot_level


def _attack_weapon_failure(
    current: Combatant, actor_id: str, intent: PlayerIntent
) -> CombatEvent | None:
    """``AttackFailed`` for a swing that names no weapon the loader can
    resolve, so the refusal spends no Action; ``None`` for every other
    intent.

    Two shapes used to reach the resolution tail and produce nothing but
    ``intent_submitted``: a weapon slug outside the release (a typo, a homebrew
    item) and no slug at all. Both are silent in the same way a legal swing
    whose rules resolved nothing would be (§5: every offered choice must work
    or say why), and neither is a DM call — the engine does not guess which
    weapon the hero swings (§8). A shape-shifted creature's refusal belongs to
    ``_stat_block_attack_failure``, which runs on its own path.
    """
    if intent.intent_type != "attack":
        return None
    if intent.stat_block_action_id:
        return None
    if intent.weapon_id and get_lib_loader().get_weapon(intent.weapon_id) is not None:
        return None
    return AttackFailed(
        actor_id=actor_id,
        target_id=intent.target_id,
        reason="unknown_weapon" if intent.weapon_id else "weapon_required",
    )


def _unavailable_spell_failure(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent
) -> CastFailed | None:
    """Требования 5 и 7: заклинание вне списка персонажа не исполняется.

    `live.spells_known_by_entity` — список, который персонаж действительно
    имеет (подготовленные/известные). Раньше он читался ТОЛЬКО для
    наратива: `cast_spell` разрешался по корпусу, и персонаж без Bless в
    списке всё равно его кастовал (`spell_cast`, `effect_applied`). Совет
    хоста при этом отчитывался о дефекте, но движок его не закрывал.

    Отказ `spell_not_available` не тратит ни слот, ни действие. Для
    персонажа с ПУСТЫМ списком список считается неизвестным (не
    «ничего нельзя»): движок не отнимает возможность, о которой его не
    проинформировали.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id:
        return None
    known = live.spells_known_by_entity.get(actor_id)
    if not known:
        return None
    if intent.spell_id in known:
        return None
    return CastFailed(
        actor_id=actor_id,
        spell_id=intent.spell_id,
        reason="spell_not_available",
    )

def _narrative_item_failure(
    live: _LiveCombat,
    actor_id: str,
    current: Combatant,
    intent: PlayerIntent,
    feature_invocation: object | None,
) -> CastFailed | None:
    """Требования 7 и 8: активность без механического исхода в источнике.

    `activities/resolver.py` исполняет `utility` без `effects[]` и
    `summon`/`enchant`/`transform` вне allowlist как нарративный
    passthrough — пишет `activity_kind_narrative` и возвращает None, не
    выдавая ни одного события. Для `use_item` это было молчаливым
    расходом действия; для заклинания — молчаливым расходом ЯЧЕЙКИ
    (Dispel Magic, Alarm, Augury: `intent_submitted` + `spell_cast` и
    больше ничего, неотличимо от «сработало»). Оба случая — названный
    отказ до расхода.

    Стоит в `pre_resolution_gates`, то есть ДО `_consume_intent_budget`:
    отказ не стоит игроку ни действия, ни ячейки (§9).

    Возвращает событие отказа; `None`, если у активностей есть механика.
    """
    if intent.intent_type not in ("use_item", "cast_spell"):
        return None
    if intent.intent_type == "cast_spell" and not intent.spell_id:
        return None

    # SRD 5.2 Hat of Many Spells: "expend a spell slot of the spell's level.
    # ... make an Intelligence (Arcana) check (DC 10 plus the spell's level)".
    # The DC formula carries `@scaling`, which the resolver fills only from a
    # chosen amount — so a request that names no spell level has no DC to
    # roll. Refused by name BEFORE any budget (формула не решается догадкой).
    activities_for_scaling = _resolve_intent_activities(
        intent,
        feature_invocation,
        current,
        stat_block_slug=_current_stat_block_slug(live, actor_id),
    ).activities
    if any(_activity_needs_scaling_amount(a) for a in activities_for_scaling) and (
        _scaling_amount_named(intent) is None
    ):
        return CastFailed(
            actor_id=actor_id,
            spell_id=intent.spell_id or intent.item_id or "",
            reason="scaling_amount_required",
        )
    # ``_resolve_intent_activities`` only reads the corpus; it spends nothing.
    activities = _resolve_intent_activities(
        intent, feature_invocation, current,
        stat_block_slug=_current_stat_block_slug(live, actor_id),
    ).activities
    if not activities:
        return None
    # A conjuration the resolver can really perform is mechanics (see
    # `_conjures_a_combatant`); the narrative gate must leave it alone.
    if all(_conjures_a_combatant(a, intent) for a in activities):
        return None
    if not all(_activity_is_narrative_or_delegated(a) for a in activities):
        return None
    return CastFailed(
        actor_id=actor_id,
        spell_id=intent.spell_id or intent.item_id or "",
        reason=(
            "narrative_spell_without_mechanics"
            if intent.intent_type == "cast_spell"
            else "narrative_activity"
        ),
    )

def _intent_economy_failure(
    current: Combatant, intent: PlayerIntent, cost: _ActionCost, funding: AttackFunding
) -> CombatEvent | None:
    """The action-economy gate for ``intent`` paid as ``funding``: the
    turn-keeping rejection to emit, or ``None``. The Light extra attack and a
    Flurry strike were admitted by ``_classify_attack_funding``; the Bonus
    Unarmed Strike and a construct's repeat need the Bonus Action; every
    other intent goes through ``_action_economy_gate_failure`` (which may
    raise)."""
    if funding in ("martial_arts_bonus", "construct_bonus") and not current.bonus_action_available:
        return AttackFailed(
            actor_id=current.entity_id, target_id=intent.target_id, reason="no_action_economy"
        )
    if cost.is_free_action or funding != "action":
        return None
    return _action_economy_gate_failure(
        current,
        intent,
        is_bonus_action=cost.is_bonus_action,
        is_reaction_cast=cost.is_reaction_cast,
    )


def _consume_intent_budget(
    live: _LiveCombat,
    actor_id: str,
    current: Combatant,
    intent: PlayerIntent,
    cost: _ActionCost,
    funding: AttackFunding,
    weapon: Weapon | None,
) -> Combatant:
    """Spend what pays for ``intent`` and return the refreshed current actor
    (``current`` is a stale snapshot). A non-attack intent spends its classified
    Action / Bonus Action / Reaction; an attack spends what ``funding`` names:
    the Attack action's soft-consume, the Light extra attack's Bonus Action
    (none with Nick), one owed Flurry strike, or the Bonus Action."""
    if intent.intent_type != "attack":
        return _consume_action_budget(live, actor_id, cost, intent.intent_type)
    if cost.is_bonus_action:
        # A stat-block Bonus Action command pays the Bonus Action, never the
        # Attack action's budget: the monster keeps its Action that turn.
        _update_combatant(live, actor_id, bonus_action_available=False)
        return _current_actor(live)
    if funding == "action":
        return _consume_attack_budget(live, actor_id, current)
    if funding == "light_offhand":
        return _consume_offhand_attack_budget(live, actor_id, current, weapon)
    if funding == "flurry":
        _update_combatant(
            live, actor_id, flurry_strikes_remaining=current.flurry_strikes_remaining - 1
        )
    else:
        _update_combatant(live, actor_id, bonus_action_available=False)
    return _current_actor(live)


def _grant_flurry_strikes(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """SRD 5.2 Monk's Focus: "Flurry of Blows. You can expend 1 Focus Point to
    make two Unarmed Strikes as a Bonus Action." Heightened Focus: "make three
    Unarmed Strikes with it instead of two." Called once the invocation is
    committed; the owed strikes are this turn's next Unarmed Strike attacks
    (``_classify_attack_funding``) and lapse at the monk's next turn start."""
    if (intent.feature_id, intent.activity_id) != _FLURRY_OF_BLOWS:
        return
    strikes = 3 if _HEIGHTENED_FOCUS in _granted_feature_slugs(_current_actor(live)) else 2
    _update_combatant(live, actor_id, flurry_strikes_remaining=strikes)


_ACTION_SURGE: Final = "action-surge"


def _action_surge_failure(current: Combatant, intent: PlayerIntent) -> CombatEvent | None:
    """``CastFailed(reason="no_action_economy")`` for a second Action Surge on
    one turn — SRD 5.2: "Starting at level 17, you can use it twice before a
    rest but only once on a turn." — ``None`` otherwise. One of the
    ``pre_resolution_gates``, so nothing is spent."""
    if intent.feature_id != _ACTION_SURGE or not current.action_surge_used_this_turn:
        return None
    return CastFailed(actor_id=current.entity_id, spell_id="", reason="no_action_economy")


def _grant_action_surge(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """SRD 5.2 Action Surge: "On your turn, you can take one additional action,
    except the Magic action." Called once the invocation is committed; the
    extra action lapses at the fighter's next turn start."""
    if intent.feature_id != _ACTION_SURGE:
        return
    fighter = _current_actor(live)
    _update_combatant(
        live,
        actor_id,
        extra_actions_remaining=fighter.extra_actions_remaining + 1,
        action_surge_used_this_turn=True,
    )


def _record_light_weapon_swing(
    live: _LiveCombat, actor_id: str, current: Combatant, weapon_slug: str
) -> Combatant:
    """Record ``weapon_slug`` as this turn's main-hand Light-weapon swing so
    ``_twf_window_open`` opens for a same-turn off-hand Bonus Action swing
    (SRD 5.2 §Two-Weapon Fighting)."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={"light_weapon_swing_slug": weapon_slug})
            break
    return _current_actor(live)


def _record_loading_weapon_fired(live: _LiveCombat, actor_id: str, current: Combatant) -> Combatant:
    """Record that this turn's one permitted Loading-weapon shot has been
    fired (SRD 5.2 Loading), closing the window for any further same-turn
    attack attempt with a Loading weapon (checked pre-budget in
    ``submit_player_intent``). Reset to False at the actor's own
    TurnStarted."""
    for idx, c in enumerate(live.initiative):
        if c.entity_id == actor_id:
            live.initiative[idx] = c.model_copy(update={"loading_weapon_fired_this_turn": True})
            break
    return _current_actor(live)


def _slot_available(live: _LiveCombat, entity_id: str, slot_level: int) -> bool:
    """SRD §Spell Slots — does ``entity_id`` hold an unexpended slot at ``slot_level``
    in EITHER pool (Spellcasting feature or Pact Magic)? SRD §Multiclassing lets
    either pool cast either prepared spell; the engine has no spell-list gate."""
    spell = live.spell_slots_by_entity.get(entity_id, {})
    pact = live.pact_slots_by_entity.get(entity_id, {})
    return int(spell.get(slot_level, 0)) > 0 or int(pact.get(slot_level, 0)) > 0


def _slotless_cast_available(
    live: _LiveCombat, caster: Combatant, spell: Spell, slot_level: int
) -> bool:
    """SRD 5.2 Magic Initiate: "You can cast it once without a spell slot, and
    you regain the ability to cast it in that way when you finish a Long
    Rest." True when ``spell`` is one of ``caster``'s slotless casts, cast at
    its own level, and its ``custom_counters`` tally is unspent."""
    if spell.slug not in caster.slotless_casts or slot_level != spell.level:
        return False
    counters = live.custom_counters_by_entity.get(caster.entity_id, {})
    return counters.get(f"{SLOTLESS_CAST_COUNTER_PREFIX}{spell.slug}", {}).get("spent", 0) < 1


def _cast_payable(live: _LiveCombat, caster: Combatant, spell: Spell, slot_level: int) -> bool:
    """Can ``caster`` pay for a leveled ``spell`` at ``slot_level``: its
    slotless cast, else a slot (``_slot_available``)?"""
    return _slotless_cast_available(live, caster, spell, slot_level) or _slot_available(
        live, caster.entity_id, slot_level
    )


def _pay_for_cast(live: _LiveCombat, caster: Combatant, spell: Spell, slot_level: int) -> bool:
    """Pay for a leveled cast: the slotless cast when one is available, else a
    slot (``_take_spell_slot``). Magic Initiate: "You can also cast the spell
    using any spell slots you have." The slotless cast goes first because a
    slot can pay for any spell and the slotless cast only for this one.
    Returns ``False`` and spends nothing when neither is available."""
    if _slotless_cast_available(live, caster, spell, slot_level):
        counters = live.custom_counters_by_entity.setdefault(caster.entity_id, {})
        counter = counters.setdefault(f"{SLOTLESS_CAST_COUNTER_PREFIX}{spell.slug}", {"spent": 0})
        counter["spent"] = counter.get("spent", 0) + 1
        return True
    return _take_spell_slot(live, caster.entity_id, slot_level)


def _spend_countered_slotless_cast(
    live: _LiveCombat, caster: Combatant, intent: PlayerIntent
) -> None:
    """SRD 5.2 Counterspell: "If that spell was cast with a spell slot, the slot
    isn't expended." Only a slot is spared: a countered spell its slotless cast
    pays for (``_pay_for_cast`` spends that before a slot) still spends it."""
    spell = get_lib_loader().get_spell(intent.spell_id or "")
    if spell is not None and spell.level > 0:
        slot_level = intent.slot_level if intent.slot_level is not None else spell.level
        if _slotless_cast_available(live, caster, spell, slot_level):
            _pay_for_cast(live, caster, spell, slot_level)


def _take_spell_slot(live: _LiveCombat, entity_id: str, slot_level: int) -> bool:
    """Expend one slot at ``slot_level`` — Spellcasting pool first, then Pact.
    Returns ``False`` and mutates nothing when neither pool has one."""
    for pool in (
        live.spell_slots_by_entity.get(entity_id),
        live.pact_slots_by_entity.get(entity_id),
    ):
        if pool is not None and int(pool.get(slot_level, 0)) > 0:
            pool[slot_level] = int(pool[slot_level]) - 1
            return True
    return False


def _reject_over_count_targets(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """Validation-only pre-check for a cast whose resolving activity carries
    ``target.affects.count`` (Magic Missile darts, Hold Person's extra Humanoids).
    Runs BEFORE ``_consume_spell_slot`` so a rejected upcast never spends a slot —
    ``_resolve_intent_activities`` (which fetches ``activities``) does not run
    until AFTER the slot gate, so this loads the spell itself via the cached lib
    loader and inspects ``spell.activities`` directly.

    Returns ``True`` iff the cast was REJECTED (``CastFailed(reason=
    "target_invalid")`` emitted and the turn already advanced — the caller must
    return). ``False`` means not applicable (no count-bearing activity) or the
    named targets are all live and within the resolved count N — either way the
    cast proceeds unchanged; ``_count_scaled_targets`` later assumes validity and
    never rejects.
    """
    if not (intent.intent_type == "cast_spell" and intent.spell_id):
        return False
    spell = get_lib_loader().get_spell(intent.spell_id)
    if spell is None:
        return False
    activity = _find_count_activity(list(spell.activities))
    if activity is None:
        return False
    cast_level = intent.slot_level if intent.slot_level is not None else spell.level
    n = resolve_target_count(activity.target.affects.count, cast_level=cast_level)
    if n is None:
        return False
    ids = (
        list(intent.target_ids)
        if intent.target_ids
        else ([intent.target_id] if intent.target_id else [])
    )
    live_ids = {c.entity_id for c in live.initiative}
    if len(ids) > n or any(i not in live_ids for i in ids):
        _emit(
            live,
            CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id,
                reason="target_invalid",
            ),
        )
        _end_turn_and_advance(live, actor_id)
        return True
    return False


def _apply_pre_slot_cast_gates(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """Run every gate that MUST fire before ``_consume_spell_slot`` — the
    in-combat-ritual rejection, a Counterspell interrupt
    (``_drain_counterspell_reaction``) and the target-count validation
    (``_reject_over_count_targets``) all need to reject (when applicable)
    before the slot is ever touched, so neither an illegal ritual cast, a
    countered cast, nor a rejected-target cast spends the caster's slot.
    Merged into one early-return call site (rather than separate ``if``s in
    ``submit_player_intent``) to keep that function's branch count down.

    The ritual check runs FIRST and unconditionally: an in-combat
    ``as_ritual=True`` cast is illegal regardless of anything else about the
    intent, so it must never reach ``_drain_counterspell_reaction`` — an
    armed Counterspell must not be triggered (or its slot spent) by a cast
    that was never going to resolve. The remaining two checks are
    independent and order-insensitive (each returns ``False`` fast when not
    applicable to the intent).

    Returns ``True`` iff any gate emitted ``CastFailed`` and already
    advanced the turn — the caller must return.
    """
    if (
        intent.intent_type == "cast_spell"
        and intent.spell_id
        and intent.as_ritual
        and get_lib_loader().get_spell(intent.spell_id) is not None
    ):
        _emit(
            live,
            CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id,
                reason="ritual_in_combat",
            ),
        )
        _end_turn_and_advance(live, actor_id)
        return True
    if _drain_counterspell_reaction(live, current, actor_id, intent):
        return True
    if _reject_untargeted_cast(live, current, actor_id, intent):
        return True
    return _reject_over_count_targets(live, current, actor_id, intent)



def _reject_untargeted_cast(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """§9 — a cast naming creatures only through ``target_ids`` resolves against nobody.

    ``target_id`` and ``target_ids`` are independent fields on ``PlayerIntent``: the
    validator links neither, and only ``target_id`` reaches ``_resolve_targets``.
    A caller passing ``target_ids=('m1',)`` therefore got ``SpellCast`` and
    ``EffectApplied`` with the slot SPENT while no target existed and no Hit
    Point moved — a mechanically applied action with no result, and a resource
    gone. The same held for a ``target_id`` naming nobody in this fight.

    Refused with ``target_invalid`` before the slot is touched, the way an
    attack names its target. Only for casts that actually need a target: a
    self/targetless buff (Shield, Mirror Image) legitimately names nobody, so
    the check asks whether a target was named at all, not whether one exists.
    """
    if intent.intent_type != "cast_spell":
        return False
    # ``_resolve_targets`` читает ТОЛЬКО ``target_id``: ветка
    # ``[c for c in live.initiative if c.entity_id == intent.target_id]``.
    # ``target_ids`` там не читается (валидатор модели поля не связывает),
    # поэтому интент с одним ``target_ids`` разрешается в ПУСТОЙ список:
    # ``SpellCast`` и ``EffectApplied`` выданы, слот потрачен, а хиты цели не
    # сдвинулись. Отказ — до списания, как и для атаки без цели.
    if not intent.target_id:
        if not intent.target_ids:
            return False
        # r121: заклинание с настоящим числом целей (``target.affects.count``,
        # Magic Missile ``2 + @item.level``) адресуется ИМЕННО ``target_ids``:
        # ``target_id`` у него пуст по контракту, а цели раскрывает
        # ``_count_scaled_targets``. Отказ здесь гасил такой каст всегда
        # (``cast_failed / target_invalid`` при любом наборе целей). Существование
        # и количество названных id проверяет ``_reject_over_count_targets`` —
        # оно же вызывается сразу после этого гейта в ``_apply_pre_slot_cast_gates``.
        spell = get_lib_loader().get_spell(intent.spell_id) if intent.spell_id else None
        if spell is not None and _find_count_activity(list(spell.activities)) is not None:
            return False
        _emit(
            live,
            CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id or "",
                reason="target_invalid",
            ),
        )
        return True
    if any(c.entity_id == intent.target_id for c in live.initiative):
        return False
    _emit(
        live,
        CastFailed(
            actor_id=current.entity_id,
            spell_id=intent.spell_id or "",
            reason="target_invalid",
        ),
    )
    return True

def _consume_spell_slot(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """SRD §Spellcasting — Spell Slots: "Whenever a character casts a
    spell, they expend a slot of that spell's level or higher." The
    slot gate lives on the orchestrator: the typed resolver walks the
    spell's own activities directly (no wrapping ``CastActivity``), so it
    never reaches a slot-consuming handler — the orchestrator owns the
    gate + decrement for this PC seam. The decrement is final here; the
    typed resolver does not mutate any per-evaluation slot sidecar, so there
    is no post-resolution slot writeback to reconcile with. Two independent
    pools may hold a slot at the same level (Spellcasting + Pact Magic); a cast
    draws from Spellcasting first, then Pact — see ``_take_spell_slot``.

    Returns ``True`` if the cast was REJECTED (a ``CastFailed`` was emitted
    and the turn advanced — the caller must return); ``False`` otherwise.
    """
    if not (intent.intent_type == "cast_spell" and intent.spell_id):
        return False
    slot_gate_spell = get_lib_loader().get_spell(intent.spell_id)
    if slot_gate_spell is None:
        return False
    base_level = slot_gate_spell.level
    slot_level = intent.slot_level if intent.slot_level is not None else base_level
    # SRD §Cantrips — "A cantrip is a spell that can be cast at
    # will, without using a spell slot." Cantrips cannot be cast
    # at higher slot levels; the engine's only correct response
    # to an intent that requests a slot on a base_level=0 spell
    # is to reject the cast (caller bug, not a silent demotion to
    # base level — silent demotion would let a "buggy" intent
    # surface as a successful cast with unintended scaling).
    if base_level == 0 and intent.slot_level not in (None, 0):
        _emit(
            live,
            CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id,
                reason="no_slot",
            ),
        )
        _end_turn_and_advance(live, actor_id)
        return True
    # Consume the slot — or the spell's slotless cast (``_pay_for_cast``). The
    # typed PC resolver does not touch ``_counter_state``, so this subtract is
    # the authoritative decrement — no post-evaluation writeback overwrites it.
    if base_level > 0 and not _pay_for_cast(live, current, slot_gate_spell, slot_level):
        _emit(
            live,
            CastFailed(
                actor_id=current.entity_id,
                spell_id=intent.spell_id,
                reason="no_slot",
            ),
        )
        _end_turn_and_advance(live, actor_id)
        return True
    return False


def _emit_spell_cast(
    live: _LiveCombat, caster_id: str, spell: Spell, slot_level: int | None
) -> None:
    """C17 — ``SpellCast`` with component metadata (SRD §Components; never enforced).

    Normalises ``slot_level`` to ``None`` for a cantrip (``spell.level == 0``)
    regardless of what the caller computed — ``events.py`` declares
    ``SpellCast.slot_level: int | None  # None for a cantrip`` and every call
    site (on-turn cast, readied-reaction resolve, Counterspell's own cast)
    shares this single normalisation point rather than each re-deriving it.
    """
    normalized_slot_level = None if spell.level == 0 else slot_level
    comps, material, consumed, cost = spell_component_metadata(spell)
    _emit(
        live,
        SpellCast(
            actor_id=caster_id,
            spell_id=spell.slug,
            slot_level=normalized_slot_level,
            ritual=bool(spell.ritual),
            components=list(comps),
            material=material,
            material_consumed=consumed,
            material_cost_gp=cost,
        ),
    )


def _emit_spell_cast_for_on_turn_intent(
    live: _LiveCombat, actor_id: str, intent: PlayerIntent
) -> None:
    """C17 — the on-turn ``SpellCast`` call site: fetch the spell the intent just
    successfully cast (the slot gate has already passed by the time the caller
    reaches this) and emit its component metadata. No-op for non-cast intents
    or an unknown ``spell_id`` (mirrors every other lib-loader miss in this
    module — silently skip rather than raise)."""
    if not (intent.intent_type == "cast_spell" and intent.spell_id):
        return
    spell = get_lib_loader().get_spell(intent.spell_id)
    if spell is None:
        return
    slot_level = intent.slot_level if intent.slot_level is not None else spell.level
    # SRD 5.2 Delayed Blast Fireball: a cast whose consumption names the
    # spell own charge pool creates one charge of it. Credited only once the
    # cast actually happened, so a refused cast leaves no bead behind (§9),
    # and keyed to the activity that consumed it.
    _spell_pool_hooks(live, actor_id, intent, spell)
    _emit_spell_cast(live, actor_id, spell, slot_level)

@dataclass
class _ResolvedActivities:
    """The typed-entity fetch result for an intent: the activities the resolver
    will walk, plus the ancillary carriers (cast spell, weapon, spellcasting
    ability, feature passive effects) the context builder needs."""

    activities: list[Any]
    cast_spell: Spell | None
    fetched_weapon: Weapon | None
    spellcasting_ability: str | None
    feature_passive_effects: list[Any]
    # SRD 5.2 magic items: the passive effects a use_item item itself
    # carries. An activity.s effects[] refs point at these by id, and
    # the activation needs them as the context-s effect source -- the
    # Boots of Speed Click Heels rider is one such effect.
    item_passive_effects: list[Any] = field(default_factory=list)


def _resolve_caster_spellcasting_ability(caster: Combatant) -> str | None:
    """SRD 5.2 §Spellcasting — the ability that governs a caster's spell
    attacks and save DCs is a per-CLASS mapping (cleric -> wis, wizard -> int,
    druid -> wis, ...), read from the caster's class doc's
    ``spellcasting.ability``. Returns ``None`` when the caster has no
    ``class_slug``, the class is unknown to the lib, or the class carries no
    spellcasting ability (a non-caster class) — callers fall back to the
    legacy flat approximation in that case."""
    if not caster.class_slug:
        return None
    cls = get_lib_loader().get_class(caster.class_slug)
    if cls is None or not cls.spellcasting.ability:
        return None
    return cls.spellcasting.ability


def _spellcasting_ability_for(caster: Combatant, spell_slug: str) -> str | None:
    """The ability ``caster`` casts ``spell_slug`` with: its own entry in
    ``Combatant.spell_abilities`` (SRD 5.2 Magic Initiate: "Intelligence,
    Wisdom, or Charisma is your spellcasting ability for this feat's spells"),
    else its class's (``_resolve_caster_spellcasting_ability``)."""
    return caster.spell_abilities.get(spell_slug) or _resolve_caster_spellcasting_ability(caster)


def _resolve_intent_activities(
    intent: PlayerIntent,
    feature_invocation: _FeatureInvocation | None,
    caster: Combatant,
    *,
    stat_block_slug: str | None = None,
) -> _ResolvedActivities:
    """Fetch the typed entity for the intent's kind from the lib loader and
    collect the activities the resolver will walk. This is the sole PC
    resolution path; the legacy evaluator's IR path is retired.
    ``stat_block_slug`` is the stat block an ``attack`` naming
    ``stat_block_action_id`` swings from (``_current_stat_block_slug``)."""
    cast_spell: Spell | None = None
    fetched_weapon: Weapon | None = None
    activities: list[Any] = []
    spellcasting_ability: str | None = None
    # The owner entity's PassiveEffect definitions, threaded into the context so
    # an activity's effect riders (``activity.effects[].id``) resolve to a runtime
    # ActiveEffect. A spell carries them on ``Spell.passive_effects``; a feature
    # (Rage) on ``Feature.passive_effects``. Empty for kinds with no rider source.
    feature_passive_effects: list[Any] = []
    # SRD 5.2 magic items -- the passive effects of a use_item item, which
    # its own activities.effects[] refs resolve against. Empty for every
    # non-item intent.
    item_passive_effects: list[Any] = []
    if intent.intent_type == "attack" and intent.weapon_id:
        fetched_weapon = get_lib_loader().get_weapon(intent.weapon_id)
        if fetched_weapon is not None:
            activities = list(fetched_weapon.activities)
            # SRD §Weapon Attacks — most mundane weapons ship the AttackActivity
            # on ``Weapon.activities``; a handful of magic weapons whose attack
            # rides their base weapon ship empty activities. Synthesize one from
            # the weapon's ``damage_parts`` so a swing still resolves (parity
            # with the OLD ``_synthesize_weapon_attack``).
            if not activities:
                activities = [_synthesize_attack_from_weapon(fetched_weapon)]
    elif intent.intent_type == "attack" and intent.stat_block_action_id:
        # A stat-block command (C21): one swing of an action on the actor's current
        # stat block, riders included, as a monster's own turn resolves it
        # (``_stat_block_attack_failure`` has already vetted the action).
        found = _stat_block_action(stat_block_slug, intent.stat_block_action_id)
        activities = list(expand_action_to_activities(*found)) if found is not None else []
        # SRD 5.2 Summon Dragon: "It obeys your verbal commands (no action
        # required by you)." A command given to a summon is resolved below
        # (on the summon's own turn); what it did is recorded so that a host
        # driving the summon through ``advance_monster_turn`` instead of
        # submitting for it spends the command rather than ignoring it (§5:
        # the action the stat block offers has to work either way).

    elif intent.intent_type == "cast_spell" and intent.spell_id:
        cast_spell = get_lib_loader().get_spell(intent.spell_id)
        if cast_spell is not None:
            # Same C2 rule the ``use_item`` branch below states: a spell
            # whose activities are ALTERNATIVES resolves the one the player
            # named, never all of them. Find Steed carries "Summon Steed"
            # and "Summon Flying Steed"; running both seated two horses for
            # one slot. ``activity_id`` picks one, an unambiguous spell
            # needs no id, and a multi-activity spell without an id defers
            # (``_cast_activity_selection_failure``) rather than guessing.
            if intent.activity_id:
                activities = [a for a in cast_spell.activities if a.id == intent.activity_id]
                # SRD 5.2 True Strike: "you make one attack with the weapon
                # used in the spell's casting", and its attack activities carry
                # ``include_base`` — the damage IS the weapon's. The weapon
                # therefore has to reach the resolver, which is the same
                # ``weapon_id`` an ``attack`` intent would name.
                if intent.weapon_id and any(
                    getattr(a, "damage", None) is not None
                    and getattr(a.damage, "include_base", False)
                    for a in activities
                ):
                    fetched_weapon = get_lib_loader().get_weapon(intent.weapon_id)
            elif len(cast_spell.activities) > 1:
                activities = []
            else:
                activities = list(cast_spell.activities)
            # SRD 5.2 §Spellcasting — the real class->ability mapping
            # (cleric -> wis, wizard -> int, ...), read off the caster's own
            # class doc, unless the spell has its own (Magic Initiate). ``None``
            # (unknown class / non-caster class) falls back to the legacy flat
            # approximation in ``build_context.py``.
            spellcasting_ability = _spellcasting_ability_for(caster, intent.spell_id)
    elif intent.intent_type == "use_item" and intent.item_id:
        # Parity with the OLD resolver's ``use_item`` branch: an item (potion,
        # scroll, wand) may carry its own activities — most often a
        # ``CastActivity`` that delegates to a referenced spell. Fetch the
        # typed item and resolve its activities directly. ``get_item`` spans
        # Item/Weapon/Armor/MagicItem, all of which inherit ``activities``.
        fetched_item = get_lib_loader().get_item(intent.item_id)
        if fetched_item is not None:
            # The item.s own passive effects are what its activities.effects[]
            # refs resolve against: the Boots of Speed Click Heels rider is a
            # PassiveEffect on the ITEM, referenced by the activity by id.
            item_passive_effects = list(getattr(fetched_item, "passive_effects", ()) or ())
            # SRD 5.2 §Attunement: a magic item is activated with ITS OWN
            # spellcasting ability, or the wielder's when the item states none
            # (Foundry's "" sentinel). `activities/cast.py` inherits this into
            # a delegated cast, but nothing ever set it for `use_item`, so a
            # cast activity carrying `@mod` (Dragon Orb -> Cure Wounds,
            # "Casting this spell restores ... + @mod Hit Points") raised
            # `ValueError: @mod requires a governing ability` out of the public
            # entry point.
            spellcasting_ability = _resolve_caster_spellcasting_ability(caster)
            if intent.activity_id:
                activities = [a for a in fetched_item.activities if a.id == intent.activity_id]
            else:
                # C2: an unselected use_item must resolve the SAME single
                # activity ``_item_charge_activity`` will charge — resolving
                # every activity on the item (the old behavior) emits events
                # for activities that were never paid for (wand-of-binding:
                # both Hold Monster AND Hold Person fired while only Hold
                # Monster's 5 charges were spent). An item with no consuming
                # activity (nothing prices > 0 itemUses) is resolved by the
                # SAME helper the price came from: one sole activity, or an
                # explicit activity_id. Running every activity here while
                # _classify_action_cost priced a single one let a reaction
                # activity execute as a free Action and two activities run
                # for one price.
                charged = _item_charge_activity(fetched_item, None) or _named_activity(
                    fetched_item, None
                )
                activities = [charged] if charged is not None else []
    elif intent.feature_id:
        # USE_FEATURE — the feature was already resolved to its single concrete
        # activity (repertoire gate + single-activity validation) above, BEFORE
        # any action-economy budget was consumed. A rejected / no-op feature
        # returned early there; reaching here means ``feature_invocation`` holds
        # the resolved activity + its PassiveEffect riders.
        assert feature_invocation is not None
        # A Rage extension or a Wild Shape leave resolves nothing: the Rage it
        # extends is already on the caster, and the form's end is committed.
        activities = (
            []
            if feature_invocation.extends_rage or feature_invocation.leaves_form
            else feature_invocation.activities
        )
        feature_passive_effects = feature_invocation.passive_effects
        # SRD 5.2 "Saving Throws" — a class feature's own save DC is
        # 8 + Proficiency Bonus + the class's spellcasting modifier
        # (Channel Divinity's "your Channel Divinity save DC"; Cleric's
        # Sear Undead, Paladin's Abjure Foes). The engine refuses to guess
        # (check.py:_dc_value), so the caster's real class ability must be
        # supplied here; leaving it None made every such feature raise
        # ValueError out of use_feature instead of resolving.
        # A feature is not a spell: no per-spell Magic Initiate override
        # applies, so the class ability alone is correct.
        spellcasting_ability = _resolve_caster_spellcasting_ability(caster)
    return _ResolvedActivities(
        activities=activities,
        cast_spell=cast_spell,
        fetched_weapon=fetched_weapon,
        spellcasting_ability=spellcasting_ability,
        feature_passive_effects=feature_passive_effects,
        item_passive_effects=item_passive_effects,
    )


def _build_cast_spell_book(activities: Sequence[Any]) -> dict[str, Spell]:
    """uuid -> Spell map for the ``cast`` activities among the resolved
    intent's activities — the delegation seam a scroll/wand's ``CastActivity``
    (``activities/cast.py``) looks its referenced spell up in. A miss (an
    absent uuid, or one the lib can't resolve) stays out of the book; it is
    never silent — ``resolve_cast`` logs ``cast_spell_unresolved`` on a
    lookup failure."""
    book: dict[str, Spell] = {}
    loader = get_lib_loader()
    for activity in activities:
        uuid = getattr(getattr(activity, "spell", None), "uuid", "")
        if not uuid or uuid in book:
            continue
        spell = loader.get_spell_by_uuid(uuid)
        if spell is not None:
            book[uuid] = spell
    return book


def _item_cast_level_override(intent: PlayerIntent) -> int | None:
    """The forced cast level a ``use_item`` charges_to_spend request implies
    for the ``cast`` activity being charged, or ``None`` when no override
    applies (no charges_to_spend, or the charged activity doesn't delegate a
    cast).

    SRD §Casting a Spell at a Higher Level — the extra charges spent above
    the wrapper's own base cost fund a level bump: ``base_level + (requested
    - base_cost)``. ``base_level`` is the wrapper's own ``spell.level``
    override (never the referenced spell's base level — a scroll/wand casts
    AT its own printed level by default); ``base_cost`` is the SAME
    itemUses cost ``_item_charge_gate`` priced this invocation against.

    C2/M2: reuses ``_item_charge_activity`` directly (via a fresh
    ``get_lib_loader().get_item`` lookup) instead of a private
    re-implementation of its selection rule, so the level override and the
    charge gate can never disagree on which activity is being charged.
    """
    if intent.intent_type != "use_item" or intent.charges_to_spend is None or not intent.item_id:
        return None
    item = get_lib_loader().get_item(intent.item_id)
    if item is None:
        return None
    activity = _item_charge_activity(item, intent.activity_id)
    if activity is None or getattr(activity, "kind", "") != "cast":
        return None
    base_level = activity.spell.level
    if base_level is None:
        return None
    # M2: no ``or 1`` fallback — after I1/I3 this path only ever reaches a
    # "cast" kind activity with a positive itemUses cost, so
    # ``_activity_item_use_cost``'s value is used as-is and can never
    # disagree with what ``_resolve_charge_request`` priced.
    base_cost = _activity_item_use_cost(item.slug, activity)
    return int(base_level) + (intent.charges_to_spend - base_cost)


def _find_count_activity(activities: list[Any]) -> Any | None:
    """The first activity whose ``target.affects.count`` GENUINELY encodes
    the upcast mechanic (``count_scales_with_cast_level`` — references
    ``@item.level``; Magic Missile's dart count, Hold Person's extra Humanoids).
    A fixed marker like ``"1"`` (the schema default a plain single-target
    damage/save/utility activity carries — Hex, Hunter's Mark, Revivify, ...) is
    NOT a count mechanic and must not engage this machinery at all. ``None``
    when no activity in the list carries a genuine count formula."""
    for activity in activities:
        target = getattr(activity, "target", None)
        affects = getattr(target, "affects", None) if target is not None else None
        if (
            affects is not None
            and affects.type == "creature"
            and count_scales_with_cast_level(affects.count)
        ):
            return activity
    return None


def _count_scaled_targets(
    live: _LiveCombat,
    intent: PlayerIntent,
    activities: list[Any],
    cast_spell: Spell | None,
) -> list[Combatant] | None:
    """Expand the target list for a cast whose resolving activity carries a
    ``target.affects.count`` (Foundry roll-data, ``@item.level`` = cast level;
    SRD 5.2 Magic Missile: "The spell creates one more dart for each spell slot
    level above 1.").

    The validation half (named ids exist, count doesn't exceed the resolved N)
    already ran in ``_reject_over_count_targets`` BEFORE the slot gate — this is a
    pure expander with NO rejection branch. Returns ``None`` when not applicable
    (not a cast / no count-bearing activity / not a creature count / the formula
    resolves to no count) so the caller falls through to the plain ``target_id``
    lookup.
    """
    if intent.intent_type != "cast_spell" or cast_spell is None:
        return None
    activity = _find_count_activity(activities)
    if activity is None:
        return None
    cast_level = intent.slot_level if intent.slot_level is not None else cast_spell.level
    n = resolve_target_count(activity.target.affects.count, cast_level=cast_level)
    if n is None:
        return None
    ids = (
        list(intent.target_ids)
        if intent.target_ids
        else ([intent.target_id] if intent.target_id else [])
    )
    by_id = {c.entity_id: c for c in live.initiative}
    targets = [by_id[i] for i in ids if i in by_id]
    # A single implicit target on a damage-kind count activity (Magic Missile
    # with a bare ``target_id``, no explicit ``target_ids``) fans every dart at
    # that one creature: one shared damage roll applied N times.
    if (
        intent.target_ids is None
        and len(targets) == 1
        and getattr(activity, "kind", None) == "damage"
    ):
        targets = targets * n
    return targets


def _resolve_targets(
    live: _LiveCombat,
    current: Combatant,
    intent: PlayerIntent,
    activities: list[Any],
    cast_spell: Spell | None,
) -> list[Combatant]:
    """SRD §Areas of Effect / §Range: Self — resolve the target list. An
    intent whose activities carry an area resolves through ``_area_targets``
    (the creatures in the template's cells with line of effect from its point
    of origin that the area affects; the named target for a template it
    cannot place). Otherwise the named target is used, defaulting to the
    caster for an effect-bearing self/targetless buff or a self-targeting
    feature. A count-bearing activity (Magic Missile darts) expands via
    ``_count_scaled_targets`` in place of the plain ``target_id`` lookup."""
    targets: list[Combatant]
    plan = _area_plan(intent, activities)
    if plan is not None:
        targets = _area_targets(live, current, intent, plan)
    else:
        expanded = _count_scaled_targets(live, intent, activities, cast_spell)
        targets = (
            expanded
            if expanded is not None
            else [c for c in live.initiative if c.entity_id == intent.target_id]
        )
        # SRD §Range: Self — an effect-bearing self/targetless buff (Shield,
        # Mirror Image, Disguise Self) names no foe, so the named-target filter
        # above yields []. Its riders would then apply to nobody and the buff
        # would silently do nothing. Default the target to the caster. AoE
        # (handled above) and single-target casts (target_id present) are
        # untouched.
        if (
            (
                not targets
                and intent.intent_type == "cast_spell"
                and _activities_bear_effects(activities)
                and _spell_is_self_or_targetless(cast_spell, intent.target_id)
            )
            or (
                not targets
                and intent.feature_id
                and activities
                and _activities_target_self(activities)
            )
            or (
                # SRD 5.2 magic items -- a self-targeting activation
                # (Boots of Speed "Click Heels") acts on the wearer, not on
                # a foe. Its rider would otherwise apply to nobody and the
                # item would silently do nothing.
                not targets
                # An activation whose typed target is SELF acts on the user.
                # An EMPTY target type is NOT self: it means the target was
                # not stated (a wand that prompts for a foe). Treating
                # emptiness as self made a fireball land on its own caster.
                and intent.intent_type == "use_item"
                and intent.target_id is None
                and activities
                and (
                    _activities_target_self(activities)
                    # A self-buff item states no target type at all: Boots of
                    # Speed's "Click Heels" has target.affects.type == "" and
                    # its payload is effect riders that can only land on the
                    # wearer. A damage/attack/save activation names no rider
                    # effect, so it never reaches this branch -- that is what
                    # keeps an unaimed wand from hurting its own holder.
                    or (
                        _activities_bear_effects(activities)
                        and not any(
                            a.kind in ("attack", "damage", "save", "check") for a in activities
                        )
                        and _activity_effects_target_self(activities)
                    )
                )
            )
            or (
                # SRD §Channel Divinity, Divine Spark (Heal) — a feature heal that
                # names no target defaults to the caster (you use the healing option
                # on yourself). Scoped to feature invocations with no named target
                # and only heal-kind activities, so an offensive feature activity
                # (Divine Spark: Save) with no target still resolves to nobody.
                not targets
                and intent.feature_id
                and intent.target_id is None
                and bool(activities)
                and all(getattr(a, "kind", None) == "heal" for a in activities)
            )
        ):
            targets = [current]
    return targets


# ── pre-armed reaction queue ─────────────────────────────────────
#
# See docs/dev/reaction-queue.md for the full design. This section holds the
# shared drain/fire machinery; the per-reaction call sites live inline in
# submit_player_intent / advance_monster_turn / _handle_move below.


def _register_pending_reaction(live: _LiveCombat, actor_id: str, intent: PlayerIntent) -> None:
    """SRD §Ready — register a ``"ready"`` intent's pre-armed reaction.

    Replaces any prior pending entry for the same owner (a combatant has one
    Action per turn, so at most one freshly-armed reaction at a time — see
    docs/dev/reaction-queue.md, "Queue data structure"). No-op for any other
    intent type or a ``"ready"`` without a ``reaction_trigger``.
    """
    if intent.intent_type != "ready" or intent.reaction_trigger is None:
        return
    live.pending_reactions = [pr for pr in live.pending_reactions if pr.owner_id != actor_id]
    live.pending_reactions.append(
        _PendingReaction(
            owner_id=actor_id,
            trigger=intent.reaction_trigger,
            spell_id=intent.spell_id,
            slot_level=intent.slot_level,
        )
    )


def _drain_pre_resolution_reactions(
    live: _LiveCombat,
    current: Combatant,
    intent: PlayerIntent,
    targets: Sequence[Combatant],
) -> set[str]:
    """Drain target-owned pending reactions for a resolving PC intent and a
    construct cast (Spiritual Weapon's immediate attack).

    ``"attack"`` intents fire ``hit_by_attack`` reactions (Shield's +5 AC
    lands before the hit/miss comparison); a ``magic-missile`` cast fires
    ``targeted_by_magic_missile`` reactions, returning the target ids whose
    reaction fired so the caller can inject the force carve-out. Every
    other intent drains nothing (the overwhelmingly common case).
    """
    if intent.intent_type == "attack" or (
        intent.intent_type == "cast_spell"
        and CONJURATION_ALLOWLIST.get(intent.spell_id or "") == "construct"
    ):
        _drain_targeted_reactions(
            live,
            trigger="hit_by_attack",
            triggering_actor_id=current.entity_id,
            targets=targets,
        )
        return set()
    if intent.intent_type == "cast_spell" and intent.spell_id == "magic-missile":
        return _drain_targeted_reactions(
            live,
            trigger="targeted_by_magic_missile",
            triggering_actor_id=current.entity_id,
            targets=targets,
        )
    return set()


def _apply_magic_missile_shield_carveout(
    payload: dict[str, Any], shielded_target_ids: set[str]
) -> None:
    """SRD Shield — *"...and you take no damage from Magic Missile."*

    Inject a transient ``"force"`` immunity entry into the (per-resolution,
    never persisted) hydration payload for each target whose Shield reaction
    just fired against a Magic Missile trigger.
    ``activities/apply.py::apply_damage`` already merges the sidecar's
    ``immunities`` list unconditionally, flooring the rolled force damage to
    ``0`` (still emitting ``DamageApplied(amount=0)`` per that module's
    "never a suppressed event" contract). Deliberately spell-slug-scoped —
    NOT a general force-immunity mechanic (docs/dev/reaction-queue.md,
    "Magic Missile carve-out").
    """
    for target_id in shielded_target_ids:
        entry = payload["passive_damage_modifiers"].setdefault(target_id, {})
        immunities = list(entry.get("immunities", ()))
        if "force" not in immunities:
            immunities.append("force")
        entry["immunities"] = immunities


def _pop_pending_reaction(
    live: _LiveCombat,
    trigger: ReactionTrigger,
    *,
    triggering_actor_id: str,
    only_owner_id: str | None = None,
    eligible: Callable[[Combatant, _PendingReaction], bool] | None = None,
) -> _PendingReaction | None:
    """SRD §Reactions — pop the first pending reaction matching ``trigger``,
    scanning ``live.initiative`` in INITIATIVE ORDER (the documented firing
    order when multiple reactions could match one trigger). A candidate
    reactor must not be the triggering actor themselves, must match
    ``only_owner_id`` when given (the ``hit_by_attack`` /
    ``targeted_by_magic_missile`` triggers are owned by the creature actually
    under attack/targeted, not any bystander), must be alive, must be neither
    Incapacitated nor shape-shifted, and must have ``reaction_available``.
    Removes + returns the match (a reaction fires — and is spent — at most
    once); ``None`` when nothing qualifies.

    An armed reaction whose owner fails ``eligible`` is SKIPPED (left queued,
    no Reaction spent). The scan continues in initiative order to the
    next candidate rather than stopping.
    """
    for reactor in live.initiative:
        if reactor.entity_id == triggering_actor_id:
            continue
        if only_owner_id is not None and reactor.entity_id != only_owner_id:
            continue
        if not reactor.is_alive or reactor.hp_current <= 0:
            continue
        # SRD 5.2 Incapacitated — no Reaction (so no opportunity attack either).
        if conditions_block_actions(_condition_names(reactor)):
            continue
        # Every armed reaction releases a spell, which this engine casts at the
        # release, and a shape-shifted creature "can't cast spells" (SRD 5.2
        # Wild Shape, Polymorph). The spell stays armed: shapeshifting "doesn't
        # break your Concentration", which holds a readied spell (SRD 5.2 Ready).
        if reactor.entity_id in live.transforms:
            continue
        if not reactor.reaction_available:
            continue
        match = next(
            (
                pr
                for pr in live.pending_reactions
                if pr.owner_id == reactor.entity_id and pr.trigger == trigger
            ),
            None,
        )
        if match is not None:
            if eligible is not None and not eligible(reactor, match):
                continue
            live.pending_reactions.remove(match)
            return match
    return None


def _resolve_readied_spell_cast(
    live: _LiveCombat, reactor: Combatant, popped: _PendingReaction
) -> None:
    """Auto-fire a pre-armed reaction spell (Shield) as a full self-cast.

    Consumes the reactor's Reaction and pays for the spell (``_pay_for_cast``:
    its slotless cast, else a slot), emits ``ReactionTriggered``,
    then resolves the spell's own activities against the reactor as sole
    target through the SAME typed resolver every on-turn cast uses — no
    bespoke Shield-only mechanics. Any ``EffectApplied`` this produces on the
    reactor with a round-scoped duration is registered for the off-turn
    expiry fix (``reaction_effects_pending_expiry`` — see
    ``docs/dev/reaction-queue.md``), since the reactor is by construction NOT
    the active turn-actor when a reaction fires.
    """
    spell = get_lib_loader().get_spell(popped.spell_id or "")
    if spell is None:
        return

    for idx, c in enumerate(live.initiative):
        if c.entity_id == reactor.entity_id:
            live.initiative[idx] = c.model_copy(update={"reaction_available": False})
            break

    slot_level = popped.slot_level if popped.slot_level is not None else spell.level
    if spell.level > 0:
        _pay_for_cast(live, reactor, spell, slot_level)
    # ``_emit_spell_cast`` normalises ``slot_level`` to ``None`` for a
    # cantrip; ``slot_level`` here stays the raw popped/derived value the
    # payment above used.

    _emit(
        live,
        ReactionTriggered(
            actor_id=reactor.entity_id,
            reaction_name=popped.spell_id or "",
            trigger_event_uuid="",
        ),
    )
    _emit_spell_cast(live, reactor.entity_id, spell, slot_level)

    spellcasting_ability = _spellcasting_ability_for(reactor, spell.slug)
    payload = _build_hydration_payload(live, caster=reactor)
    actx = build_activity_context(
        reactor,
        [reactor],
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=slot_level,
        base_spell_level=spell.level,
        spellcasting_ability=spellcasting_ability,
        concentration=spell.concentration,
        source_passive_effects=list(spell.passive_effects),
        # Monster/reaction paths don't delegate casts yet — PC-path
        # delegation lives in _build_cast_spell_book; extending it here is a
        # recorded follow-up.
        spell_book={},
        passive_damage_modifiers=payload["passive_damage_modifiers"],
        save_modifiers=payload["save_modifiers"],
        roster=live.initiative,
        check_modifiers=payload["check_modifiers"],
        d20_test_penalty=payload["d20_test_penalty"],
        # C15: is_proficient_attack left on default (True) — this reaction
        # path only resolves cast SaveActivity/DamageActivity from a Spell
        # (Shield's own reaction cast), never an AttackActivity with a
        # fetched Weapon, so the proficiency gate never applies here.
        target_distance_ft=_target_distance_map(live, reactor.entity_id, [reactor]),
        attacker_grappler_id=_condition_source_entity(live, reactor, "grappled"),
        legendary_resistance_armed=payload["legendary_resistance_armed"],
        legendary_resistances_remaining_by_entity=payload[
            "legendary_resistances_remaining_by_entity"
        ],
        undead_fortitude_holds=live.undead_fortitude_holds,
        # A readied spell reads its own ``@scale.*`` carrier (Tsunami, Ice
        # Storm from a staff); the reaction path builds no other.
        scale_values=_formula_carriers(reactor),
    )
    pre_event_count = len(live.event_log)
    for activity in spell.activities:
        resolve_activity(activity, actx, weapon=None)

    # A readied concentration spell concentrates like an on-turn cast.
    _fold_resolution_outcome(
        live,
        reactor,
        spell=spell,
        actx=actx,
        pre_event_count=pre_event_count,
        concentration_max_rounds=_concentration_max_rounds(spell),
    )
    _sync_legendary_resistance(live, pre_event_count)

    for ev in live.event_log[pre_event_count:]:
        if (
            isinstance(ev, EffectApplied)
            and ev.effect.target_id == reactor.entity_id
            and ev.effect.duration.rounds is not None
        ):
            live.reaction_effects_pending_expiry.setdefault(reactor.entity_id, []).append(
                (ev.effect.target_id, ev.effect.id, ev.effect.origin)
            )


def _readied_cast_eligible(
    live: _LiveCombat, reactor: Combatant, pending: _PendingReaction
) -> bool:
    """A readied leveled spell (Shield) needs an unexpended slot at its
    readied level, or — readied at its own level — its unspent slotless
    cast (``_cast_payable``). SRD §Spell Slots: "When you cast a spell,
    you expend a slot of that spell's level or higher"; a cantrip (level
    0) has no slot to expend and is always eligible."""
    spell = get_lib_loader().get_spell(pending.spell_id or "")
    if spell is None or spell.level == 0:
        return True
    level = pending.slot_level if pending.slot_level is not None else spell.level
    return _cast_payable(live, reactor, spell, level)


def _drain_targeted_reactions(
    live: _LiveCombat,
    *,
    trigger: ReactionTrigger,
    triggering_actor_id: str,
    targets: Sequence[Combatant],
) -> set[str]:
    """Pop + fully resolve one matching reaction per entry in ``targets``
    (each target can own at most one match). Returns the set of target
    entity_ids whose reaction fired — Shield vs. Magic Missile's force
    carve-out needs to know which targets just got shielded."""
    fired: set[str] = set()
    for target in targets:
        popped = _pop_pending_reaction(
            live,
            trigger,
            triggering_actor_id=triggering_actor_id,
            only_owner_id=target.entity_id,
            eligible=lambda reactor, pending: _readied_cast_eligible(live, reactor, pending),
        )
        if popped is None:
            continue
        reactor = _find_combatant(live, popped.owner_id)
        if reactor is None:
            continue
        _resolve_readied_spell_cast(live, reactor, popped)
        fired.add(target.entity_id)
    return fired


def _drain_counterspell_reaction(
    live: _LiveCombat, current: Combatant, actor_id: str, intent: PlayerIntent
) -> bool:
    """SRD 5.2 Counterspell — pop a pending ``"cast_spell"``-trigger reaction
    (if any) and resolve it against ``current`` (the interrupted caster) via
    Counterspell's OWN canonical ``save``-kind activity, through the
    existing, unmodified ``activities/save.py`` resolver.

    Returns ``True`` iff the triggering cast was countered (``CastFailed``
    emitted + the turn already advanced) — the caller must return
    immediately, BEFORE ``_consume_spell_slot`` ever runs for the triggering
    spell (this is what preserves the interrupted caster's slot — see
    ``docs/dev/reaction-queue.md``, "Slot-consumption redesign"). ``False``
    means no reaction fired OR the save succeeded; either way the triggering
    cast proceeds exactly as if this function had never been called.

    Gates: the reactor must be able to pay for Counterspell at the readied
    level (``_cast_payable``) and be within its own ``range.value`` with
    line of sight — an ineligible reactor's armed reaction is skipped, not
    consumed.
    """
    if intent.intent_type != "cast_spell" or not intent.spell_id:
        return False

    caster_zone = live.actor_zone.get(actor_id)

    def _eligible(reactor: Combatant, pending: _PendingReaction) -> bool:
        spell = get_lib_loader().get_spell(pending.spell_id or "counterspell")
        if spell is None:
            return False
        level = pending.slot_level if pending.slot_level is not None else spell.level
        if spell.level > 0 and not _cast_payable(live, reactor, spell, level):
            return False
        range_ft = spell.range.value
        reactor_zone = live.actor_zone.get(reactor.entity_id)
        if range_ft is None or reactor_zone is None or caster_zone is None:
            return True  # no geometry ⇒ no penalty (engine-wide convention)
        return _in_range_with_los(live.topology, reactor_zone, caster_zone, int(range_ft))

    popped = _pop_pending_reaction(
        live, "cast_spell", triggering_actor_id=actor_id, eligible=_eligible
    )
    if popped is None:
        return False
    reactor = _find_combatant(live, popped.owner_id)
    if reactor is None:
        return False
    # The spell lookup here duplicates ``_eligible``'s (identical
    # ``pending.spell_id or "counterspell"`` / ``popped.spell_id or
    # "counterspell"`` expression): ``_eligible`` runs once per CANDIDATE
    # during the scan inside ``_pop_pending_reaction`` — the armed entry is
    # not known yet, so its spell can't be pre-fetched — and this second
    # lookup is against ``popped``, the one entry the scan actually selected.
    counterspell = get_lib_loader().get_spell(popped.spell_id or "counterspell")
    if counterspell is None:
        return False
    save_activity = next((a for a in counterspell.activities if isinstance(a, SaveActivity)), None)
    if save_activity is None:
        return False

    # Counterspell is paid for (a slot, or its slotless cast) whether or not
    # it succeeds — only the INTERRUPTED spell's slot is preserved, below.
    for idx, c in enumerate(live.initiative):
        if c.entity_id == reactor.entity_id:
            live.initiative[idx] = c.model_copy(update={"reaction_available": False})
            break
    cs_level = popped.slot_level if popped.slot_level is not None else counterspell.level
    if counterspell.level > 0:
        _pay_for_cast(live, reactor, counterspell, cs_level)

    _emit(
        live,
        ReactionTriggered(
            actor_id=reactor.entity_id,
            reaction_name=popped.spell_id or "counterspell",
            trigger_event_uuid="",
        ),
    )
    _emit_spell_cast(live, reactor.entity_id, counterspell, cs_level)

    reactor_spellcasting_ability = _spellcasting_ability_for(reactor, counterspell.slug)
    payload = _build_hydration_payload(live, caster=reactor)
    actx = build_activity_context(
        reactor,
        [current],
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=cs_level,
        base_spell_level=counterspell.level,
        spellcasting_ability=reactor_spellcasting_ability,
        concentration=False,
        source_passive_effects=list(counterspell.passive_effects),
        # Monster/reaction paths don't delegate casts yet — PC-path
        # delegation lives in _build_cast_spell_book; extending it here is a
        # recorded follow-up.
        spell_book={},
        passive_damage_modifiers=payload["passive_damage_modifiers"],
        save_modifiers=payload["save_modifiers"],
        roster=live.initiative,
        check_modifiers=payload["check_modifiers"],
        d20_test_penalty=payload["d20_test_penalty"],
        target_distance_ft=_target_distance_map(live, reactor.entity_id, [current]),
        # C15: is_proficient_attack left on default (True) — Counterspell
        # resolves a SaveActivity, never an AttackActivity with a fetched
        # Weapon, so the proficiency gate never applies here.
        attacker_grappler_id=_condition_source_entity(live, reactor, "grappled"),
        legendary_resistance_armed=payload["legendary_resistance_armed"],
        legendary_resistances_remaining_by_entity=payload[
            "legendary_resistances_remaining_by_entity"
        ],
        undead_fortitude_holds=live.undead_fortitude_holds,
        scale_values=_formula_carriers(reactor),
    )
    pre_event_count = len(live.event_log)
    resolve_activity(save_activity, actx, weapon=None)
    _sync_legendary_resistance(live, pre_event_count)
    save_events = [
        ev
        for ev in live.event_log[pre_event_count:]
        if isinstance(ev, SaveRolled) and ev.target_id == current.entity_id
    ]
    succeeded = save_events[-1].succeeded if save_events else True
    if succeeded:
        return False

    _spend_countered_slotless_cast(live, current, intent)
    _emit(
        live,
        CastFailed(
            actor_id=actor_id,
            spell_id=intent.spell_id or "",
            reason="countered",
        ),
    )
    _end_turn_and_advance(live, actor_id)
    return True


async def _dispatch_turn_nonending_intent(
    live: _LiveCombat, current: Combatant, intent: PlayerIntent
) -> bool:
    """Dispatch the turn-non-ending intents; return ``True`` when handled.

    SRD §Action Economy — these intents keep the actor on turn (no
    ``_end_turn_and_advance``); the actor may follow with another intent:

    * ``move_mark`` — SRD §Hunter's Mark: *"If the target drops to 0 Hit
      Points before this spell ends, you can take a Bonus Action to move
      the mark to a new creature you can see within range."* A narrow seam:
      no IR evaluation, no slot consumption, no concentration re-check.
    * ``move`` — SRD §Movement: walk the fewest-cells legal route to
      ``target_zone_id``, paying each step from the per-turn movement budget
      (movement is interleaved with Actions / Bonus Actions). Rejections emit
      ``MoveFailed`` without mutating budget or position.
    * ``dash`` — SRD §Dash: spend the Action (or, with Cunning Action, the
      Bonus Action) to add ``base_speed`` to the movement
      budget. Rejections raise ``IntentRejectedError("no_action_economy")``.
    * ``disengage`` — SRD §Disengage: spend the Action (or, with Cunning
      Action, the Bonus Action); movement provokes
      no Opportunity Attacks for the rest of the turn. Like Dash, keeps
      the actor on turn so a same-turn Disengage→Move sequence works
      (this closes the turn-ending fall-through where
      "disengage" fell through to the generic Action tail that
      unconditionally calls ``_end_turn_and_advance``).
    * ``drop_concentration`` — SRD §Concentration: end Concentration
      voluntarily at any time, no action required. Touches no budget and
      keeps the actor on turn.
    * ``hide`` — SRD §Actions in Combat, Hide: gate on cover/obscurement,
      roll a DC 15 Dexterity (Stealth) check, grant Invisible on success.
      Touches NO Action-economy budget (see
      ``_handle_hide``'s docstring for the divergence from strict SRD
      cost) and keeps the actor on turn.
    * ``stand_up`` — SRD 5.2 Prone, Restricted Movement: spend half Speed
      (rounded down) of movement to end Prone. Touches NO Action/Bonus
      Action budget — movement only — and keeps the actor on turn.
    * ``drop_prone`` — SRD 5.2 Prone: "you can give yourself the Prone
      condition without using an action". Touches NO budget at all and
      keeps the actor on turn (`_handle_drop_prone`); standing back up
      stays ``stand_up``.
    """
    if intent.intent_type == "move_mark":
        await _handle_move_mark(live, current, intent)
        return True
    if intent.intent_type == "move":
        _handle_move(live, current, intent)
        return True
    if intent.intent_type == "dash":
        _handle_dash(live, current, intent)
        return True
    if intent.intent_type == "disengage":
        _handle_disengage(live, current, intent)
        return True
    if intent.intent_type == "drop_concentration":
        _handle_drop_concentration(live, current, intent)
        return True
    if intent.intent_type == "hide":
        _handle_hide(live, current, intent)
        return True
    if intent.intent_type == "stand_up":
        _handle_stand_up(live, current, intent)
        return True
    if intent.intent_type == "drop_prone":
        _handle_drop_prone(live, current, intent)
        return True
    return False


def _pc_attack_context_kwargs(
    live: _LiveCombat,
    current: Combatant,
    geometry_targets: list[Combatant],
    *,
    base_weapon: Weapon | None,
    weapon: Weapon | None,
    payload: dict[str, Any],
    cover_origin: str | None = None,
) -> dict[str, Any]:
    """The ``build_activity_context`` keyword block a character's resolution
    shares between its on-turn intent (``submit_player_intent``) and its
    opportunity attack (``_resolve_opportunity_attack``): every argument that
    depends on the attacker, its targets (``geometry_targets``), the weapon it
    swings (``base_weapon`` as fetched, ``weapon`` with its enchantments) and
    the hydration ``payload`` — never on the intent. ``cover_origin`` is an
    area's point of origin, ``None`` for the attacker's own cell."""
    target_unseen, attacker_unseen_by = _target_visibility_maps(live, current, geometry_targets)
    attacker_invisibility_pierced_by, target_invisibility_pierced = _invisibility_pierced_maps(
        live, current, geometry_targets
    )
    return {
        "passive_damage_modifiers": payload["passive_damage_modifiers"],
        "save_modifiers": payload["save_modifiers"],
        "check_modifiers": payload["check_modifiers"],
        "d20_test_penalty": payload["d20_test_penalty"],
        # The degenerate case where an area's point of origin coincides with
        # the target's own cell (a small sphere centred on the lone creature
        # it affects) is handled inside ``_target_cover_map`` itself.
        "target_cover": _target_cover_map(
            live, current.entity_id, geometry_targets, origin_cell=cover_origin
        ),
        "target_distance_ft": _target_distance_map(live, current.entity_id, geometry_targets),
        # SRD 5.2 §Actions in Combat — Dodge: per-target dodge-benefit flag
        # folded into attack disadvantage (attack.py). C16b: *"any attack roll
        # made against you has Disadvantage if you can see the attacker"* —
        # the dodging target must also see THIS attacker (``current``).
        "target_dodging": {
            t.entity_id: _dodge_benefit_active(live, t) and _combatant_can_see(live, t, current)
            for t in geometry_targets
        },
        # SRD 5.2 §Actions in Combat — Help, Assist an Attack Roll:
        # per-target ally-of-attacker Help grant folded into attack
        # advantage (attack.py); the one-use pop fires after resolution.
        "target_help_advantage": _target_help_advantage_map(
            live, current.entity_id, geometry_targets
        ),
        "attacker_grappler_id": _condition_source_entity(live, current, "grappled"),
        "target_unseen": target_unseen,
        "attacker_unseen_by": attacker_unseen_by,
        "attacker_invisibility_pierced_by": attacker_invisibility_pierced_by,
        "target_invisibility_pierced": target_invisibility_pierced,
        # SRD 5.2 Frightened line-of-sight gate (C16b): PRE-RESOLVED
        # attacker-own-perception flag.
        "attacker_fear_source_in_sight": _fear_source_in_sight(live, current),
        # ``@classes.<class>.levels`` formula tokens read this carrier; the
        # ``@scale.*`` map itself rides ``build_activity_context`` (a duplicate
        # key there is a TypeError).
        "class_levels": _class_levels(current),
        "character_level": current.character_level,
        # SRD 5.2 Arcane Vigor: "@attributes.hd.*" reads the caster's own Hit
        # Point Dice pool. ``None`` when it holds none — the formula then
        # refuses rather than inventing a die size.
        "hit_dice_spent": _hit_dice_spent(current),
        "hit_dice_total": _hit_dice_pool_size(current),
        "hit_dice_largest_face": _largest_hit_die_face(current),
        # SRD 5.2 True Strike: the rider's extra damage lives on the effect
        # (``system.damage.parts``), so the attack reads it from the live
        # effects rather than from its own activity.
        "extra_damage_parts": _effect_damage_parts(
            live, current, base_weapon
        ),
        # SRD 5.2 Martial Arts: PRE-RESOLVED (armor/Shield + the granted
        # feature) — ``attack.py`` still gates per swing on the weapon being
        # unarmed or a Monk weapon.
        "martial_arts": _martial_arts_active(current),
        # SRD §Advantage / §Sneak Attack — the caster's own active effects
        # (attacker-side advantage flags) plus the two Sneak Attack sidecars:
        # the per-turn "spent" gate rebuilt from the live Combatant flag, and
        # the per-target ally-adjacent predicate (a spatial read owned here,
        # not in the pure resolver).
        "active_effects": tuple(live.active_effects.get(current.entity_id, [])),
        "sneak_attack_spent": {current.entity_id: current.sneak_attack_spent_this_turn},
        "sneak_attack_ally_adjacent": _sneak_ally_adjacent_map(live, current, geometry_targets),
        # SRD 5.2 Savage Attacker: the per-turn use, rebuilt from the live
        # Combatant flag (``_record_savage_attacker_spent`` writes it back).
        "savage_attacker_spent": {current.entity_id: current.savage_attacker_spent_this_turn},
        # SRD 5.2 Grappler: which targets this attacker grapples.
        "target_grappled_by_attacker": _grappled_by_map(live, current, geometry_targets),
        # SRD 5.2 §Weapon Proficiency (C15) — real gate: proficient iff
        # ``current.weapon_proficiencies`` is the unset sentinel (host never
        # opted in) or the weapon's category/slug is listed; ``True`` for a
        # ``None`` weapon.
        "is_proficient_attack": _is_proficient_with_weapon(current, weapon),
        # SRD 5.2 §Range — per-target "beyond normal range" flag
        # folded into attack disadvantage (attack.py, "range:long").
        "target_beyond_normal_range": _target_beyond_normal_range_map(
            live, current.entity_id, weapon, geometry_targets
        ),
        # SRD 5.2 "Ranged Attacks in Close Combat": per-ATTACKER
        # flag folded into attack disadvantage (attack.py, "ranged_in_melee")
        # for an effectively-ranged attack.
        "attacker_ranged_in_melee": _hostile_adjacent_to_attacker(live, current),
        # SRD 5.2 §Weapon Mastery — Vex / Sap: PRE-RESOLVED
        # per-target vex-grant / per-attacker sap-mark flags, mirroring the
        # Help geometry above. The one-use pops fire after resolution.
        "attacker_vex_advantage": _attacker_vex_advantage_map(
            live, current.entity_id, geometry_targets
        ),
        "attacker_sapped": current.entity_id in live.sap_marks,
        # C18 §Monster action economy — Legendary Resistance sidecars:
        # a PC attack/cast/item/feature can force a save on a monster
        # target holding a pre-armed conversion.
        "legendary_resistance_armed": payload["legendary_resistance_armed"],
        "legendary_resistances_remaining_by_entity": payload[
            "legendary_resistances_remaining_by_entity"
        ],
        # C18 §Monster action economy — Undead Fortitude
        # write-back handshake (see ``_LiveCombat.undead_fortitude_holds``):
        # the live object itself, never a copy.
        "undead_fortitude_holds": live.undead_fortitude_holds,
        "weapon_enchantment_to_hit": _weapon_enchantment_to_hit(current, base_weapon, weapon),
        "weapon_enchantment_spellcasting": _weapon_enchantment_spellcasting(
            live, current, base_weapon
        ),
        "weapon_enchantment_ability": _weapon_enchantment_ability(
            live, current, base_weapon
        ),
        # SRD 5.2 stat-block trait "Pack Tactics" holds whichever entry point
        # drives its bearer: a form's (C21) or a host-driven monster's.
        # ``attack.py`` applies it only to an attacker carrying the trait.
        "pack_tactics_ally_adjacent": _pack_tactics_map(live, current, geometry_targets),
        # The whole fight, exactly as the monster paths do: a per-target
        # sidecar (Mage Armor's AC mode, Barkskin's floor) belongs to whichever
        # creature the effect named, and a PC attacking an ALLY resolves with
        # that ally as its own target — but a creature the effect named may
        # still be outside ``geometry_targets``, and then the sidecar was
        # written and never read. ``build_activity_context`` resolves the
        # per-entity entry through this roster.
        "roster": live.initiative,
    }


async def submit_player_intent(
    handle: CombatHandle,
    actor_id: str,
    intent: PlayerIntent,
) -> None:
    """Accept a PC intent for the current turn, validate it, resolve it.

    Validation:
      - ``actor_id`` must be in the live combat's initiative order
      - it must currently be ``actor_id``'s turn
      - combat must not have ended

    On success: emit ``IntentSubmitted``, fetch the typed entity for the
    intent from the lib loader, and walk its activities through the per-kind
    resolvers under ``activities``, emitting the resulting
    ``CombatEvent`` stream.
    """
    live = _get_live(handle)
    current = _validate_intent_preconditions(live, handle, actor_id, intent=intent)

    # Turn-non-ending intents (move_mark / move / dash) dispatch through
    # their dedicated handlers and keep the actor on turn — see
    # ``_dispatch_turn_nonending_intent``'s docstring for the SRD framing
    # of each.
    if await _dispatch_turn_nonending_intent(live, current, intent):
        # The actor keeps its turn — unless an opportunity attack on its move
        # made it leave the initiative order.
        _hand_off_departed_turn(live)
        return

    # USE_FEATURE — resolve the feature to its single concrete activity BEFORE
    # any action-economy budget is consumed. A gate-rejected or multi-activity
    # no-op feature returns ``None`` (after a loud, tracked warning) and the turn
    # stays untouched: no Bonus Action / Action spent, no IntentSubmitted emitted,
    # turn preserved. This ordering is the fix for the economy bug where a
    # rejected feature still spent the Bonus Action.
    feature_invocation: _FeatureInvocation | None = None
    if intent.feature_id:
        feature_invocation = _resolve_feature_invocation(
            current,
            intent.feature_id,
            intent.activity_id,
            pool_points=intent.pool_points,
            raging=_rage_effect(live, actor_id) is not None,
            leaving_form=intent.form_id is None and _is_wild_shaped(live, actor_id),
        )
        if feature_invocation is None:
            # `_resolve_feature_invocation` is pure (no `live`) and only logs,
            # so the reject has to be announced here. Without this a player
            # pressing a feature the character does not have, or one the
            # corpus cannot execute, got no event, no reason and no result —
            # indistinguishable from a dead button (§5).
            _emit(
                live,
                CastFailed(
                    actor_id=actor_id,
                    spell_id="",
                    reason=_feature_invocation_refusal(live, current, intent),
                ),
            )
            return

    # SRD §Limited-Use Features / §Item Charges — a capped feature
    # (Second Wind) rejects when its per-rest uses are exhausted with no
    # intervening rest; a capped item (Pipes of Haunting) rejects when its
    # charge pool cannot cover this invocation's cost. Checked BEFORE any
    # budget consumption (mirroring the bonus-action gate): a rejected
    # invocation spends no Action/Bonus Action. The matching spend is
    # recorded only once the invocation is committed to resolving (below).
    if _gate_feature_and_item_uses(live, actor_id, current, intent, feature_invocation):
        return

    # SRD §Action Economy — classify the action cost BEFORE emitting
    # IntentSubmitted so a budget-exhausted intent doesn't pollute the
    # event log with a half-completed cast. Cast spells consult their
    # asset's typed ``casting_time.unit`` (ACTION / BONUS / REACTION);
    # non-cast intents (attack, dash, etc.) are always Actions on this
    # path. Reactions never come through here — they arrive via a future
    # off-turn intent path; treat REACTION as a routing error and surface
    # it as CastFailed.
    #
    # Reactions don't have a dedicated off-turn intent path yet (deferred
    # to the reaction-flow piece). Until then submit_player_intent is the
    # only ingress; consume ``reaction_available`` and otherwise advance
    # the turn like an Action so existing reaction-spell scenarios keep
    # working. The proper off-turn path will reject reactions through
    # this entrypoint.
    action_cost = _classify_action_cost(
        intent,
        feature_invocation,
        stat_block_slug=_current_stat_block_slug(live, actor_id),
    )
    cast_spell_for_timing = action_cost.cast_spell_for_timing
    is_bonus_action = action_cost.is_bonus_action
    is_reaction_cast = action_cost.is_reaction_cast

    # What pays for an attack's swing — the Attack action, the Light property's
    # extra attack, a Flurry of Blows strike or Martial Arts' Bonus Unarmed
    # Strike — is classified BEFORE the economy gate, which routes each funding
    # (``_intent_economy_failure``). An attack that looks like a Light extra
    # attack but fails one of its gates is an Attack-action swing; with
    # ``attacks_remaining`` spent, that gate's turn-keeping
    # ``AttackFailed(reason="no_action_economy")`` rejects it. The weapon is
    # fetched once, here, and the Loading gate below reuses it.
    attack_weapon = (
        get_lib_loader().get_weapon(intent.weapon_id)
        if intent.intent_type == "attack" and intent.weapon_id
        else None
    )
    funding = _classify_attack_funding(
        current,
        intent,
        attack_weapon,
        repeats_construct=_repeated_construct(live, current, intent) is not None,
    )

    # Pre-resolution reject gates — each checked BEFORE any action budget is
    # consumed, so a rejection spends no Action/Bonus Action/slot and leaves
    # the turn untouched. Order matters and is preserved from the original
    # sequential if-chain: spell range (SRD §Spell Range) -> weapon reach
    # (SRD §Weapon Reach / Range) -> Loading one-shot-per-turn cap (SRD 5.2
    # Loading) -> Charmed target (SRD 5.2 "You can't attack the charmer or
    # target the charmer with damaging abilities or magical effects") ->
    # pre-slot ``target_invalid`` (Hellish Rebuke's fixed target, SRD
    # §Hellish Rebuke) -> an area's own refusals (``_area_target_failure``: an
    # unaimed Cone/Line/Cube, an exclusion where there is no choice, a counted
    # area's bad names) -> a second Action Surge this turn (SRD 5.2 "only once
    # on a turn") -> a Bardic
    # Inspiration with no other creature to inspire -> an attack redeeming a
    # die it cannot roll -> an allowlisted conjuration's own refusals
    # (``_conjuration_gate_failure``). The first gate whose failure-builder
    # returns a non-``None`` event wins; that event is emitted and the intent
    # is rejected.
    pre_resolution_gates: tuple[Callable[[], CombatEvent | None], ...] = (
        lambda: _cast_activity_selection_failure(live, current, intent),
        lambda: _adjudication_failure(live, current, intent),
        lambda: _spell_out_of_range_failure(live, actor_id, intent, cast_spell_for_timing),
        lambda: _attack_out_of_range_failure(live, actor_id, intent),
        lambda: _attack_target_missing_failure(live, actor_id, intent),
        lambda: _loading_weapon_already_fired_failure(current, actor_id, intent, attack_weapon),
        lambda: _charmed_target_failure(live, actor_id, current, intent),
        lambda: _cast_target_invalid_failure(current, actor_id, intent),
        lambda: _area_target_failure(live, current, actor_id, intent, feature_invocation),
        lambda: _action_surge_failure(current, intent),
        lambda: _bardic_inspiration_target_failure(live, actor_id, intent),
        lambda: _granted_die_failure(live, current, intent),
        lambda: _rage_armor_failure(current, actor_id, intent),
        lambda: _raging_cast_failure(live, current, intent),
        lambda: _hit_dice_cast_failure(live, current, intent),
        lambda: _conjuration_gate_failure(live, current, intent),
        # Требования 7 и 8: `use_item` по активности без механики в
        # источнике. Стоит здесь, а не после резолва, чтобы отказ не стоил
        # игроку действия (§9).
        lambda: _narrative_item_failure(live, actor_id, current, intent, feature_invocation),
        # Требования 5 и 7: персонаж может применять только те заклинания,
        # что есть в его списке. Проверка стоит до траты слота (§9).
        lambda: _unavailable_spell_failure(live, actor_id, intent),
        # An attack that names no usable weapon is refused here, not at the
        # resolution tail: a refusal must not cost the Action (§9).
        lambda: _attack_weapon_failure(current, actor_id, intent),
    )
    # SRD 5.2 Summon Dragon: "It obeys your verbal commands (no action
    # required by you)." A command naming a stat-block ACTION (the Draconic
    # Spirit's Multiattack and Rend alike) is what the summon does on its own
    # turn. A command submitted BY the summon resolves below, on that turn; a
    # copy is kept for ``advance_monster_turn`` so a host that drives the
    # summon through its turn later still spends it, and the copy is popped by
    # whichever path resolves the command first.
    # §5: a legal command must not be a no-op just because the host drives
    # the summon through its turn instead of submitting for it.
    # The summon acting on its own turn receives the command the turn is
    # spent on; a command the OWNER submits goes to its first summon still
    # standing (it costs the owner no action, SRD 5.2 Summon Dragon).
    _acting_summon_owner = _summon_owner(live, actor_id)
    _command_recipient = None
    if _acting_summon_owner is not None:
        _command_recipient = actor_id
    else:
        _command_recipient = _summon_under_command_from_caster(
            live, _current_actor(live), actor_id
        )
    if _command_recipient is not None:
        live.summon_commands[_command_recipient] = intent.stat_block_action_id

    for build_pre_resolution_failure in pre_resolution_gates:
        failure = build_pre_resolution_failure()
        if failure is not None:
            _emit(live, failure)
            return

    action_economy_failure = _intent_economy_failure(current, intent, action_cost, funding)
    if action_economy_failure is not None:
        _emit(live, action_economy_failure)
        return

    # SRD 5.2 §Actions in Combat — Help, Assist an Attack Roll: "an enemy
    # within 5 feet of you". Gate BEFORE any budget is consumed — same
    # placement discipline as the ``pre_resolution_gates`` tuple above (spell
    # range / weapon reach / Charmed / cast target_invalid), so a rejected
    # Help spends no Action and leaves the turn untouched. Split out (like
    # ``_set_dodging``) purely to keep this dispatcher's cyclomatic
    # complexity under the lint ceiling.
    _reject_invalid_help_target(live, actor_id, intent)

    # SRD 5.2 Unarmed Strike — Grapple: same reach-gate placement discipline
    # as Help above. Split out purely to keep this dispatcher's cyclomatic
    # complexity under the lint ceiling.
    _reject_invalid_grapple_target(live, actor_id, intent)

    # SRD 5.2 Unarmed Strike — Shove: same reach-gate placement discipline
    # as Grapple above.
    _reject_invalid_shove_target(live, actor_id, intent)

    # SRD 5.2 "Ending a Grapple" — an ``escape_grapple`` intent from an actor
    # who is not (or no longer) Grappled has nothing to escape from. Gated
    # BEFORE any budget is consumed, same placement discipline as every other
    # pre-budget target gate above.
    _reject_invalid_escape_grapple_actor(actor_id, intent, current)

    # Consume the budget now: what ``funding`` names for an attack, the
    # classified Action / Bonus Action / Reaction otherwise. ``current`` is
    # refreshed so the turn-advance branch below sees the spend.
    current = _consume_intent_budget(
        live, actor_id, current, intent, action_cost, funding, attack_weapon
    )

    _emit(
        live,
        IntentSubmitted(
            actor_id=actor_id,
            intent_type=intent.intent_type,
            spell_id=intent.spell_id,
            target_id=intent.target_id,
            item_id=intent.item_id,
        ),
    )

    # SRD §Actions in Combat — Dodge / Help / Unarmed Strike Grapple/Shove /
    # "Ending a Grapple". Every one is Action-cost (already spent above),
    # resolves no activities of its own, and ends the turn immediately; each
    # target/actor-state gate already ran before any budget was touched
    # (``_reject_invalid_help_target`` / ``_reject_invalid_grapple_target`` /
    # ``_reject_invalid_shove_target`` / ``_reject_invalid_escape_grapple_
    # actor``). Collapsed into one dispatch
    # call — see ``_dispatch_simple_turn_ending_intent``'s docstring.
    if _dispatch_simple_turn_ending_intent(live, current, actor_id, intent):
        return

    # SRD §Ready — a "ready" intent pre-arms the pending-reaction queue
    # (docs/dev/reaction-queue.md): the Action is spent NOW (the budget
    # consumption above), the Reaction later, when the trigger fires.
    # Registration draws no dice and resolves no activities; the generic
    # tail below advances the turn exactly as for any other Action. No-op
    # for every other intent type (guard inside the helper).
    _register_pending_reaction(live, actor_id, intent)

    # SRD §Reactions — a 1-reaction-class cast consumes the actor's
    # reaction. Emit ReactionTriggered so downstream consumers (UI,
    # reaction-pool accounting, future off-turn polling) can observe the
    # spend. ``trigger_event_uuid`` is empty until a proper trigger model
    # threads the originating event UUID through.
    if is_reaction_cast:
        _emit(
            live,
            ReactionTriggered(
                actor_id=actor_id,
                reaction_name=intent.spell_id or "",
                trigger_event_uuid="",
            ),
        )

    # SRD 5.2 Counterspell (drain a pending "cast_spell" reaction) + the
    # count validation (a count-bearing cast's named targets — Magic Missile darts,
    # Hold Person's extra Humanoids) — BOTH run BEFORE the slot gate, so a
    # countered or rejected cast never reaches ``_consume_spell_slot`` and the
    # interrupted/rejected caster's slot is never expended; see
    # docs/dev/reaction-queue.md, "Slot-consumption redesign". Returns True
    # iff either gate fired a ``CastFailed`` and already advanced the turn
    # (the wasted action) — the caller must return.
    if _apply_pre_slot_cast_gates(live, current, actor_id, intent):
        return

    # SRD §Spellcasting — Spell Slots. Gate + decrement live on the
    # orchestrator; a rejected cast emits ``CastFailed`` + advances the turn
    # and signals the caller to return.
    if _spell_pool_charge_failure(live, current, intent) or _consume_spell_slot(
        live, current, actor_id, intent
    ):
        return

    # C17 — ``SpellCast`` metadata event (component/material/ritual bookkeeping,
    # never enforced). Emitted right after the slot gate passes so it is
    # ordered before any resolution events (damage, saves, ...) the cast produces.
    _emit_spell_cast_for_on_turn_intent(live, actor_id, intent)

    # SRD §Limited-Use Features — every action-economy / slot gate has
    # passed and the invocation is now committed to resolving; record the spend on
    # the caster's per-rest use counter so a later same-rest invocation rejects
    # above. The early gate rejects an over-cap invocation before reaching here, so
    # this only increments a within-cap use (no-op for uncapped / non-feature intents).
    _record_capped_feature_use(live, actor_id, intent.feature_id, feature_invocation)

    # SRD 5.2 Superior Defense / Stunning Strike — a cost the data routes to
    # ANOTHER feature's pool (Monk's Focus) is charged against that pool now
    # that the invocation is committed; the gate above already refused a short
    # or missing pool, so nothing is half-spent.
    _record_cross_feature_spend(live, current, feature_invocation)

    # SRD 5.2 Sorcerous Restoration — the mirror image: this invocation gives
    # spent uses back to another pool instead of taking them.
    _record_cross_feature_restore(live, current, feature_invocation)

    # SRD 5.2 Rage — a committed Bonus-Action extension, read by this turn's
    # ``engine:rage-extension`` hook.
    _record_rage_extension(live, actor_id, feature_invocation)

    # SRD 5.2 Wild Shape — a committed Bonus-Action leave ends the form (no-op
    # for every other intent).
    _leave_wild_shape(live, actor_id, feature_invocation)

    # SRD 5.2 Flurry of Blows — the committed invocation owes the monk its
    # Unarmed Strikes (no-op for every other intent).
    _grant_flurry_strikes(live, actor_id, intent)

    # SRD 5.2 Action Surge — the committed surge adds the extra action (no-op
    # for every other intent).
    _grant_action_surge(live, actor_id, intent)

    # SRD §Item Charges — the item-charge gate above has passed; commit the
    # spend on the actor's per-rest charge counter (no-op for uncapped /
    # non-use_item intents).
    _record_item_charge_spend(live, actor_id, intent)

    # ── Typed-Activity resolution (Foundry cutover, ─────────────
    #
    # Fetch the typed entity for the intent's kind from the lib loader and
    # collect the activities the resolver will walk. This is the sole PC
    # resolution path; the legacy evaluator's IR path is retired.
    resolved = _resolve_intent_activities(
        intent,
        feature_invocation,
        current,
        stat_block_slug=_current_stat_block_slug(live, current.entity_id),
    )
    activities = resolved.activities
    cast_spell = resolved.cast_spell
    fetched_weapon = _enchanted_weapon(live, current, resolved.fetched_weapon)
    spellcasting_ability = resolved.spellcasting_ability
    feature_passive_effects = resolved.feature_passive_effects
    # SRD 5.2 magic items -- the effects a use_item item.s own activity
    # refs resolve against (Boots of Speed Click Heels).
    item_passive_effects = resolved.item_passive_effects
    # SRD 5.2 §§Charges — the item behind a `use_item` intent, for the
    # `@item.uses.value` / `@item.uses.max` tokens its formulas read.
    fetched_item_for_context = (
        get_lib_loader().get_item(intent.item_id)
        if intent.intent_type == "use_item" and intent.item_id
        else None
    )

    # SRD 5.2 §Areas of Effect — an intent whose save, damage or heal activity
    # carries a template resolves against the creatures that area affects
    # (``_area_targets``); any other intent resolves against its named target.
    targets = _resolve_targets(live, current, intent, activities, cast_spell)

    # SRD §Reactions — drain any pending target-owned reactions (Shield)
    # BEFORE the sidecar projection below, so a just-applied reaction effect
    # (Shield's +5 AC) folds into this very resolution's hydration payload.
    # Returns the target ids whose reaction fired against a
    # Magic Missile trigger — needed for the carve-out injection below.
    shielded_vs_magic_missile = _drain_pre_resolution_reactions(live, current, intent, targets)

    # The orchestrator already owns the per-entity passive sidecars; project
    # them once and hand the two dicts ``build_activity_context`` needs in
    # (it stays pure — no orchestrator import, no double-compute).
    payload = _build_hydration_payload(live, caster=current)

    # SRD Shield — *"...and you take no damage from Magic Missile."* Inject
    # the transient, spell-slug-scoped force carve-out into THIS payload only
    # (rebuilt fresh per resolution; nothing persists). No-op for an empty
    # set (guard inside the helper).
    _apply_magic_missile_shield_carveout(payload, shielded_vs_magic_missile)


    pre_event_count = len(live.event_log)

    # SRD 5.2 Spiritual Weapon — a construct's Bonus-Action repeat resolves
    # here, after the target's readied reactions drained (no-op otherwise).
    _resolve_construct_attack_intent(live, current, intent)

    actx: ActivityResolutionContext | None = None

    if not activities:
        # Slug absent from the lib (e.g. a wrapper-only spell) or a non-
        # resolving intent kind. Emit nothing, but log the loss — never a
        # silent no-op. The divergence triage classifies these.
        if intent.intent_type == "cast_spell" and intent.spell_id:
            _LOGGER.warning("activity_resolution_empty slug=%s", intent.spell_id)
            # Требование 8: игрок должен узнать, ПОЧЕМУ ход не дал результата.
            # Раньше здесь была только запись в лог: игрок видел
            # "intent_submitted" и тишину, что неотличимо от «ничего не
            # произошло по правилам». Ресурс не тратится, но отказ назван.
            _emit(
                live,
                CastFailed(
                    actor_id=current.entity_id,
                    spell_id=intent.spell_id,
                    reason="unknown_spell",
                ),
            )
        elif intent.intent_type == "attack":
            # The pre-resolution gate ``_attack_weapon_failure`` already refused
            # this swing by name, before any budget was spent; nothing else
            # resolves from an empty activity list.
            _LOGGER.warning("activity_resolution_empty slug=%s", intent.weapon_id)
            # An attack naming a weapon the loader does not know (a typo, an
            # item outside the release) must say so: only "intent_submitted"
            # is indistinguishable from "the rules produced nothing" (§5).
            _emit(
                live,
                AttackFailed(
                    actor_id=current.entity_id,
                    target_id=intent.target_id,
                    reason="unknown_weapon",
                ),
            )
        elif intent.intent_type == "use_item" and intent.item_id:
            _LOGGER.warning("activity_resolution_empty slug=%s", intent.item_id)
    else:
        # SRD 5.2 §Weapon Mastery — Cleave: the once-per-turn
        # gate + the deterministic second target, both pre-resolved here
        # (spatial + per-turn state are orchestrator-owned); ``attack.py``
        # chains the extra roll only when BOTH are set. ``None``/False for
        # every non-cleave weapon and non-attack intent. Single-target
        # distance read (weapon attacks resolve against one primary target).
        _primary_distance_ft = (
            _target_distance_map(live, current.entity_id, targets).get(targets[0].entity_id)
            if targets
            else None
        )
        cleave_available = _cleave_available(current, fetched_weapon, _primary_distance_ft)
        cleave_candidate = (
            _cleave_candidate(live, current, fetched_weapon, targets) if cleave_available else None
        )
        # Every PER-TARGET sidecar is built
        # over the primary targets PLUS the cleave candidate, so the chained
        # roll sees the candidate's own full SRD geometry (visibility, cover,
        # distance, range tier, Dodge, Help, Vex) through the same
        # ``_attack_roll_sources`` path as a main swing. ``ctx.targets``
        # itself stays the PRIMARY list — damage / effects / turn-state
        # writebacks never treat the candidate as a primary target. The
        # Help/Vex one-use pops also walk this list: a chained
        # ``AttackRolled`` against the candidate consuming a Help or Vex
        # grant held against IT is SRD-correct ("the next attack roll
        # against that creature").
        geometry_targets: list[Combatant] = (
            [*targets, cleave_candidate] if cleave_candidate is not None else targets
        )
        # SRD 5.2 Versatile property — the attacker's declared
        # two-handed grip (``intent.two_handed``) applies only when the
        # weapon carries VERSATILE AND this swing is an actual melee attack
        # (not a Thrown weapon being thrown at range, per
        # ``_versatile_grip_applies``). Single-target-distance read: weapon
        # attacks resolve against one primary target, so the first entry's
        # distance stands in for "this swing's" distance.
        use_versatile_damage = intent.two_handed and _versatile_grip_applies(
            fetched_weapon, _primary_distance_ft
        )
        # BEFORE the context is built: True Strike's rider is extra damage on
        # this very attack, so it has to be live while the attack context
        # reads the effects. The rider names ONE activity, so the effect it
        # carries fires for that one and never for a sibling.

        # SRD 5.2 "choose the damage type": a pick the activities do not        # offer is refused BEFORE any resource is spent — falling back to the
        # first listed would be a silent substitution of the player's
        # decision (§5, §9).
        if intent.damage_type:
            offered = _damage_type_offer(activities)
            if offered and intent.damage_type not in offered:
                _emit(live, CastFailed(
                    actor_id=current.entity_id,
                    spell_id=intent.spell_id or "",
                    reason="damage_type_invalid",
                ))
                return None

        if cast_spell is not None and activities:
            _apply_rider_effects_on_attack(live, current, cast_spell, activities, None)

        # What this resolution can add to the caster's movement modes.
        # The context is built BEFORE the activities run, so the effect
        # Spider Climb / Aquatic Adaptation is about to land is not in
        # ``live.active_effects`` yet; the readings it needs ("a Climb Speed
        # equal to your Speed") come from this resolution's own riders.
        source_passive_effects_for_context = (
            list(cast_spell.passive_effects)
            if cast_spell
            else list(feature_passive_effects or item_passive_effects or [])
        )
        # What this resolution can add to the caster's movement modes.
        # The context is built BEFORE the activities run, so the effect
        # Spider Climb / Aquatic Adaptation is about to land is not in
        # ``live.active_effects`` yet; the readings it needs ("a Climb Speed
        # equal to your Speed") come from this resolution's own riders.
        actx_effects = (*live.active_effects.get(current.entity_id, ()), *source_passive_effects_for_context)

        actx = build_activity_context(
            current,
            targets,            rng=live.rng,
            event_emitter=lambda ev: _emit(live, ev),
            slot_level=intent.slot_level,
            base_spell_level=cast_spell.level if cast_spell is not None else None,
            # A magic item's own activity (Shield of the Cavalier's Shield
            # Bash: "2d6 + 2 + your ability modifier") asks for a modifier the
            # hero may not have a spellcasting ability for. The item's user
            # supplies it; the attack activity falls back to it only when its
            # own damage expression names ``@mod``.
            spellcasting_ability=(
                spellcasting_ability
                if spellcasting_ability is not None
                else _ITEM_USER_ABILITY
            ),
            concentration=cast_spell.concentration if cast_spell is not None else False,
            # SRD 5.2 "choose the damage type": the caster names it, the
            # engine applies it. Absent a choice a multi-typed part falls
            # back to the first listed.
            damage_type_choices=_damage_type_choices(intent, activities),
            # SRD 5.2 Overchannel: "2d12 Necrotic damage for each level of
            # the spell slot" on every use AFTER the first, as
            # "(2 + @item.uses.spent - 1)d12".
            item_uses_spent=(
                _feature_use_spent(live, current.entity_id, intent.feature_id)
                if intent.feature_id
                else _activity_uses_spent(
                    live, current.entity_id, intent.item_id, intent.activity_id
                )
            ),
            source_passive_effects=(
                list(cast_spell.passive_effects)
                if cast_spell
                else (feature_passive_effects or item_passive_effects)
            ),
            # The item whose activity this is, so its effects carry
            # flags={"item": slug} and a repeat activation switches them off
            # instead of stacking (SRD 5.2 Boots of Speed). Spells and
            # features pass None and keep adding up.
            item_id=intent.item_id if intent.intent_type == "use_item" else None,
            # ``spell_book`` is the Foundry-uuid → Spell map a ``CastActivity``
            # delegates through (scroll/wand casting a referenced spell) — the
            # real uuid→Spell resolution over THIS invocation's own resolved
            # activities. A miss is not silent: ``resolve_cast``
            # (activities/cast.py) logs ``cast_spell_unresolved uuid=...`` at
            # WARNING and returns.
            spell_book=_build_cast_spell_book(activities),
            # SRD §Casting a Spell at a Higher Level — a charges_to_spend
            # ``use_item`` upcast forces the delegated cast to the level the
            # extra charges paid for; ``None`` (the common case) lets
            # ``resolve_cast`` fall through to the wrapper's own/base level.
            cast_level_override=_item_cast_level_override(intent),
            # SRD 5.2 Bardic Inspiration: the die THIS attack asks to redeem
            # (``PlayerIntent.redeem_granted_die``), sized from the granting
            # bard now — ``None`` for every non-attack intent and an attack
            # that asks for none.
            granted_die=_redeemed_die(live, current, intent),
            # SRD 5.2 Bardic Inspiration, the other half: a die held by a
            # creature that is not the actor is drawn on its own failed
            # saving throw (``activities/save_primitive.py``). Keyed by
            # holder, so a spell's target — not the caster — spends its die.
            granted_dices=_granted_dice_by_entity(live),
            # A FEATURE invocation must not inherit the blanket spell
            # save_dc_override; its save activity computes its own ability+PB DC.
            is_feature_invocation=bool(intent.feature_id),
            # SRD 5.2 Light: the extra attack adds no positive ability modifier —
            # unless Two-Weapon Fighting: "you can add your ability modifier to
            # the damage of that attack if you aren't already adding it".
            suppress_positive_ability_damage_mod=(
                funding == "light_offhand" and "two-weapon-fighting" not in current.fighting_styles
            ),
            # SRD 5.2 Versatile property — see
            # ``use_versatile_damage`` computation above.
            use_versatile_damage=use_versatile_damage,
            # SRD 5.2 §Weapon Mastery — Cleave: see the
            # ``cleave_available`` / ``cleave_candidate`` computation above.
            # The monster site does not thread these: a monster attack
            # carries no ``Weapon``, so it can never cleave today.
            cleave_available=cleave_available,
            cleave_candidate=cleave_candidate,
            # SRD 5.2 Lay on Hands — the ``@scaling`` token this resolution's
            # formulas see: the pool points drawn, set only when the resolved
            # feature activity scales its own-pool cost by amount.
            # SRD 5.2 Hat of Many Spells: the `@scaling` there is the SPELL
            # LEVEL the player named (`intent.slot_level`), not a feature
            # pool amount — «DC 10 plus the spell's level».
            # SRD 5.2 Staff of Power: "16 * @item.uses.value" — the charges
            # LEFT, read from the item's own `uses.max`; an item that states
            # no cap leaves both `None` and the formula refuses rather than
            # inventing a number.
            item_uses_value=_item_uses_left(
                live, current, fetched_item_for_context, intent, activities
            ),
            item_uses_max=_item_uses_cap(
                live, current, fetched_item_for_context, intent, activities
            ),
            scaling_value=(
                feature_invocation.scaling_value
                if feature_invocation is not None
                # Предметная активность `save`/`check` (Helm of Brilliance,
                # Hat of Many Spells) берёт названное игроком количество.
                # Путь `cast` оставлен как был: там `@scaling` — не уровень
                # заклинания, и подстановка его меняла бы чужие формулы.
                else (
                    _scaling_amount_named(intent)
                    if intent.intent_type == "use_item" and cast_spell is None
                    else None
                )
            ),
            conjuration=_conjuration_carrier(live, current, intent),
            # A stat-block command (C21) rolls at the stat block's
            # own scores and Proficiency Bonus.
            stat_block_magnitudes=(
                _stat_block_magnitudes_of(live, current) if intent.stat_block_action_id else None
            ),
            # SRD 5.2 Tsunami: its save activity reads BOTH
            # ``(@item.uses.value)d10`` (the spell's own pool) and
            # ``(@attributes.movement.walk)d10`` — formula tokens this cast
            # path never carried, so the public call raised
            # "Unhandled roll-data token". No duplicate key here: the attack
            # kwargs below carry none of these.
            scale_values=_formula_carriers(current),
            # SRD 5.2 "a Climb Speed equal to your Speed": the corpus writes it
            # as a reference to the walk speed, which only the orchestrator can
            # resolve -- with the fight's own bonuses already folded in, and
            # with this resolution's own effect included.
            movement_walk_speed=_effective_speed(current, live),
            speed_ratio_effects=_speed_ratio_effects(source_passive_effects_for_context),
            movement_modes=_projected_movement_modes(
                live,
                current,

                _upcoming_movement_changes(actx_effects, _effective_speed(current, live)),
            ),
            **_pc_attack_context_kwargs(
                live,
                current,
                geometry_targets,
                base_weapon=resolved.fetched_weapon,
                weapon=fetched_weapon,
                payload=payload,
                # SRD 5.2 §Cover — an area of effect measures cover from its
                # point of origin, which for a target-origin template is NOT the
                # caster's cell. ``None`` for every non-AoE cast/attack ⇒ the
                # caster's cell.
                cover_origin=_area_cover_origin(live, current.entity_id, intent, activities),
            ),
        )
        for activity in activities:
            resolve_activity(activity, actx, weapon=fetched_weapon)

        # SRD 5.2 Bardic Inspiration — a die rolled this resolution is spent.
        _expend_granted_die(live, current, actx)
        _expend_granted_dice_rolled_by_targets(live, actx)

        # SRD 5.2 Arcane Vigor — "Those dice are then expended." The healing
        # formula that rolled off the caster's own Hit Point Dice recorded how
        # many it used; spend them now that the resolution committed.
        _expend_hit_dice(live, current, activities)

        # SRD 5.2 §Actions in Combat — Help: one-use pop. If this attack
        # landed against a target the caster's Help grant was folded onto
        # (``target_help_advantage`` above), spend exactly ONE matching
        # helper's grant now that the roll has happened.
        _pop_help_grant(live, current.entity_id, geometry_targets, pre_event_count)

        # SRD 5.2 §Weapon Mastery — Vex / Sap: spend the
        # one-use grants/marks that PRE-EXISTED this resolution and were
        # CONSUMED by an attack roll this resolution (mirrors the Help pop
        # immediately above) BEFORE folding any NEW procs THIS resolution
        # produced — a fresh vex/sap proc from THIS hit must survive to gate
        # the attacker's/target's NEXT attack, not this one.
        _pop_vex_grants(live, current.entity_id, geometry_targets, pre_event_count)
        _pop_sap_mark(live, current.entity_id, pre_event_count)
        _fold_mastery_procs(live, current.entity_id, actx)

        # SRD 5.2 §Actions in Combat — Hide, break clause: "the condition
        # ends on you immediately after ... you make an attack roll" or
        # "cast a spell with a Verbal component." The swing/cast just
        # resolved above still benefited from Invisible (the attack
        # context was built before this removal, so the C12 advantage
        # fold already applied) — this only affects the NEXT action.
        _break_hide_on_attack_or_verbal_cast(live, current, intent, cast_spell)

        # SRD §Sneak Attack, "Once per turn" — record that the rider fired so a
        # (future) second qualifying attack this turn is capped. The rider folds
        # inside the pure resolver; the orchestrator owns the actor-state write.
        # A rider fired iff the caster was sneak-eligible for a hit target this
        # resolution (finesse/ranged weapon + Advantage or an adjacent ally),
        # was not already spent, and at least one target took damage.
        _record_sneak_attack_spent(live, current, fetched_weapon, targets, actx, pre_event_count)
        _record_savage_attacker_spent(live, current, actx)

        # SRD 5.2 §Spell Descriptions — typed forced-movement riders (e.g.
        # Thunderwave's "pushed 10 feet away from you") fire after the
        # save/damage resolution so the push never perturbs the seeded roll
        # order and a target that died is left where it fell.
        _apply_forced_movement_riders(live, current, intent, pre_event_count)

        # SRD 5.2 §Two-Weapon Fighting — after a resolved MAIN-HAND swing
        # (never the off-hand swing itself) with a Light weapon, record its
        # slug so a same-turn different-Light-weapon attack opens the
        # off-hand Bonus Action window (``_twf_window_open``). Recorded
        # unconditionally on hit-or-miss (SRD: the option is "when you take
        # the Attack action... you can use a Bonus Action", not gated on
        # the main-hand swing landing).
        if (
            intent.intent_type == "attack"
            and funding != "light_offhand"
            and fetched_weapon is not None
            and WeaponProperty.LIGHT in fetched_weapon.properties
        ):
            current = _record_light_weapon_swing(live, actor_id, current, fetched_weapon.slug)

        # SRD 5.2 Loading — after ANY resolved swing (main-hand or off-hand)
        # with a Loading weapon, mark the actor's one-fire-per-turn cap so a
        # subsequent same-turn attack attempt with a Loading weapon is
        # rejected pre-budget above. Recorded unconditionally
        # on hit-or-miss, mirroring the Light-weapon record above — the
        # SRD caps firing the weapon, not landing the shot.
        if (
            intent.intent_type == "attack"
            and fetched_weapon is not None
            and WeaponProperty.LOADING in fetched_weapon.properties
        ):
            current = _record_loading_weapon_fired(live, actor_id, current)

    # SRD §Concentration — the concentration anchor, then C13's writeback of
    # ``Combatant.concentration_effect_id`` and its persistent lifecycle links
    # (concentration ownership, effect→condition bijection, repeat-save specs).
    _fold_resolution_outcome(
        live,
        current,
        spell=cast_spell,
        actx=actx,
        pre_event_count=pre_event_count,
        concentration_max_rounds=_concentration_max_rounds(cast_spell),
    )
    _sync_legendary_resistance(live, pre_event_count)

    # SRD §Hold Person / §Hold Monster — *"At the end of each of its turns,
    # the target repeats the save."* This runs as the ``engine:repeat-save``
    # ``turn_end`` hook inside ``_end_turn_and_advance``, NOT here: a bonus
    # action does not end the turn, so it must not trigger a repeat save.
    #
    # Advance the turn. End-of-round wraps to next round + emits a
    # RoundStarted; a follow-up RoundEnded would land in the cutover
    # path where the evaluator drives the loop. Keeping the additive
    # surface minimal: TurnEnded → TurnStarted (and RoundStarted on
    # wrap) is what a narrator-side consumer needs to see today.
    #
    # SRD §Action Economy — a bonus action does NOT end the turn; the
    # actor keeps initiative and may follow with a regular Action.
    #
    # Every swing outside the Attack action (``funding`` other than
    # ``"action"``: the Light extra attack, a Flurry strike, the Bonus Unarmed
    # Strike) is bonus-funded too, and ``is_bonus_action`` only covers a
    # Bonus-Action cast or feature. Without this a one-attack actor's extra
    # swing would reach ``_attack_action_is_spent`` below and end the turn,
    # discarding the movement it still owes. A free ``special`` activation
    # (Action Surge) is part of the turn rather than an action, so it keeps
    # the turn too.
    if is_bonus_action or action_cost.is_free_action or funding != "action":
        _keep_turn(live)
        return
    # SRD 5.2 Movement: break up your movement, some before and some after an
    # action. The turn survives a spent Action while the actor still has an
    # option: movement in the budget, an unspent Bonus Action, or an Action
    # Surge extra action. A leftover Bonus Action does not re-open the Action,
    # so a second Action-costed intent is still refused.
    #
    # HOST CONTRACT: this branch keeps the turn but nothing here ends it. The
    # host must submit ``pass`` to hand the turn on once the player is done.
    # Only an Action intent with nothing left falls through to ``_end_action``
    # below; a ``move`` or ``stand_up`` that exhausts the budget leaves the
    # turn open by design — the budget need not reach zero, and the engine
    # cannot tell a blocked route from a player who changed their mind.
    if intent.intent_type != "pass" and current is not None and (
        current.movement_remaining > 0
        or current.bonus_action_available
        or current.extra_actions_remaining > 0
    ):
        _keep_turn(live)
        return
    # SRD §Extra Attack — a main-hand attack keeps the turn while
    # swings remain this Action, OR a two-weapon-fighting off-hand window
    # is still open (``_attack_action_is_spent``).
    if intent.intent_type == "attack" and not _attack_action_is_spent(live, current):
        _keep_turn(live)
        return
    # An Action intent ends the turn unless an Action Surge extra action is left.
    _end_action(live, actor_id, intent)


def _opportunity_attackers(
    live: _LiveCombat, *, mover_id: str, from_cell: str, to_cell: str
) -> list[str]:
    """SRD 5.2 Opportunity Attacks: "You can make an Opportunity Attack when a
    creature that you can see leaves your reach using its action, its Bonus
    Action, its Reaction, or one of its speeds." The ids of every creature the
    step ``from_cell`` -> ``to_cell`` lets make one, in initiative order.

    A reactor is a member of either side (a summon takes no reactions) that is
    the mover's enemy, alive above 0 HP, not Incapacitated, has its Reaction
    and a melee attack to make (``_opportunity_attack_of``), can see the mover
    (``_combatant_can_see``) and is not Charmed by it (SRD 5.2 Charmed: "You
    can't attack the charmer"). The step provokes when ``from_cell`` is within
    that attack's reach, with line of sight and short of total cover, and
    ``to_cell`` is not: reach is Chebyshev, so stepping around inside it never
    provokes. A mover that took the Disengage action
    this turn provokes nothing, from either side ("Your movement doesn't
    provoke Opportunity Attacks for the rest of the turn").
    """
    mover = _find_combatant(live, mover_id)
    if mover is None or mover.disengaging_this_turn:
        return []
    reactors: list[str] = []
    for reactor in live.initiative:
        reactor_id = reactor.entity_id
        if (
            reactor_id not in live.party_ids and reactor_id not in live.encounter_ids
        ) or not _is_enemy(live, reactor_id, mover_id):
            continue
        if not reactor.is_alive or reactor.hp_current <= 0 or not reactor.reaction_available:
            continue
        if conditions_block_actions(_condition_names(reactor)):
            continue
        reactor_cell = live.actor_zone.get(reactor_id)
        attack = _opportunity_attack_of(live, reactor) if reactor_cell is not None else None
        if (
            attack is None
            or reactor_cell is None
            or not _in_range_with_los(live.topology, reactor_cell, from_cell, attack.reach_ft)
            or live.topology.within_range(reactor_cell, to_cell, attack.reach_ft)
        ):
            continue
        if not _combatant_can_see(live, reactor, mover):
            continue
        if _condition_source_entity(live, reactor, "charmed") == mover_id:
            continue
        reactors.append(reactor_id)
    return reactors


def _walk_must_stop(live: _LiveCombat, mover_id: str) -> bool:
    """Whether a walk ends where its mover now stands: the mover left the
    initiative order (a summon at 0 HP), died, dropped to 0 HP, or its
    effective Speed fell to 0 (SRD 5.2 Unconscious, Grappled: "Speed 0")."""
    mover = _find_combatant(live, mover_id)
    return (
        mover is None
        or mover_id in live.dead_ids
        or mover.hp_current <= 0
        or _effective_speed(mover, live) == 0
    )


def _fire_opportunity_attacks_on_step(
    live: _LiveCombat, *, mover_id: str, from_cell: str, to_cell: str
) -> bool:
    """Make every opportunity attack the step ``from_cell`` -> ``to_cell``
    provokes (``_opportunity_attackers``) while the mover still stands in
    ``from_cell``: SRD 5.2 "The attack occurs right before the creature leaves
    your reach." Each reactor spends its Reaction, hit or miss. Every walk
    calls this before each step (``_handle_move``, the closing walk, the flee
    walk); forced movement never does (``push_combatant``).

    Returns ``True`` when the walk must stop here (``_walk_must_stop``) or an
    attack pushed the mover off ``from_cell``. The reactors after that one
    attack nothing: the mover never leaves their reach.
    """
    for reactor_id in _opportunity_attackers(
        live, mover_id=mover_id, from_cell=from_cell, to_cell=to_cell
    ):
        # Re-read both: an earlier attack this step can end a concentration
        # and dismiss a summon, so the initiative order may have shifted.
        reactor = _find_combatant(live, reactor_id)
        mover = _find_combatant(live, mover_id)
        if reactor is None or mover is None:
            continue
        _emit(
            live,
            IntentSubmitted(actor_id=reactor_id, intent_type="reaction", target_id=mover_id),
        )
        _update_combatant(live, reactor_id, reaction_available=False)
        _resolve_opportunity_attack(live, reactor, mover)
        # An attack's forced movement (the Push mastery) carried the mover
        # off from_cell: the walk ends where it landed, and later reactors
        # no longer see it leave their reach.
        if _walk_must_stop(live, mover_id) or live.actor_zone.get(mover_id) != from_cell:
            return True
    return False


@dataclass(frozen=True)
class _OpportunityAttack:
    """The one melee attack a creature makes as its opportunity attack (SRD
    5.2: "one melee attack with a weapon or an Unarmed Strike"): the
    activities it resolves, the character's weapon (``None`` for a stat-block
    or legacy attack) and the reach it threatens with it."""

    activities: tuple[Any, ...]
    weapon: Weapon | None
    reach_ft: int


def _opportunity_attack_weapon_slug(pc: PartyMemberSpec) -> str | None:
    """The weapon ``pc`` makes its opportunity attacks with: its
    ``opportunity_attack_weapon_id``, else the first melee weapon in its
    ``equipment``, else ``None`` — an Unarmed Strike.

    Raises ``ValueError`` when ``opportunity_attack_weapon_id`` names no melee
    weapon in the corpus: the attack must be a melee attack."""
    loader = get_lib_loader()
    if pc.opportunity_attack_weapon_id is not None:
        weapon = loader.get_weapon(pc.opportunity_attack_weapon_id)
        if weapon is None or weapon.range.kind != "melee":
            raise ValueError(
                f"start_combat: {pc.entity_id} opportunity_attack_weapon_id "
                f"{pc.opportunity_attack_weapon_id!r} is not a melee weapon"
            )
        return weapon.slug
    for slug in pc.equipment:
        weapon = loader.get_weapon(slug)
        if weapon is not None and weapon.range.kind == "melee":
            return weapon.slug
    return None


def _stat_block_opportunity_attack(
    monster: Monster, melee_reach_ft: int
) -> _OpportunityAttack | None:
    """The first action on ``monster``'s stat block whose attack is not ranged,
    riders included (``expand_action_to_activities``), or ``None``. SRD 5.2
    Reach: "Certain creatures have melee attacks with a reach greater than 5
    feet" — a melee attack's explicit ft range is its reach (a Pike's 10 ft),
    while a melee-or-thrown attack's ft range is its throw (a Spear's "reach
    5 ft. or range 20/60 ft."), so that one reaches ``melee_reach_ft``."""
    for action in monster.actions:
        attack = next((a for a in action.activities if isinstance(a, AttackActivity)), None)
        if attack is None or attack.attack.type.value == "ranged":
            continue
        reach = melee_reach_ft
        value = attack.range.value or ""
        if attack.attack.type.value == "melee" and attack.range.units == "ft" and value.isdigit():
            reach = int(value) or melee_reach_ft
        return _OpportunityAttack(
            activities=tuple(expand_action_to_activities(monster, action)),
            weapon=None,
            reach_ft=reach,
        )
    return None


def _opportunity_attack_of(live: _LiveCombat, creature: Combatant) -> _OpportunityAttack | None:
    """The opportunity attack ``creature`` makes, or ``None`` when it has no
    melee attack to make one with.

    * A creature acting from a stat block — a monster's template, or the form a
      transformed creature took — makes its first non-ranged stat-block attack
      (``_stat_block_opportunity_attack``).
    * A character swings ``live.opportunity_attack_weapons``' weapon at the
      weapon's reach, or makes an Unarmed Strike at its ``melee_reach_ft``.
    * Anyone else — a template-less foe, or a character when the asset
      loader carries neither its weapon nor ``unarmed-strike`` — swings its
      legacy ``attack_bonus`` / ``damage_dice``
      (``_synthesize_attack_from_legacy_fields``) at its ``melee_reach_ft``.
    """
    slug = _current_stat_block_slug(live, creature.entity_id)
    if slug is not None:
        monster = get_lib_loader().get_monster(slug)
        if monster is None:
            return None
        return _stat_block_opportunity_attack(monster, creature.melee_reach_ft)
    weapon = (
        get_lib_loader().get_weapon(
            live.opportunity_attack_weapons.get(creature.entity_id, _UNARMED_STRIKE)
        )
        if creature.entity_type == "Character"
        else None
    )
    if weapon is not None:
        attack = next((a for a in weapon.activities if isinstance(a, AttackActivity)), None)
        return _OpportunityAttack(
            activities=(attack or _synthesize_attack_from_weapon(weapon),),
            weapon=weapon,
            reach_ft=(
                creature.melee_reach_ft
                if weapon.slug == _UNARMED_STRIKE
                else _weapon_melee_reach_ft(weapon)
            ),
        )
    synthesized = _synthesize_attack_from_legacy_fields(creature)
    if synthesized is None:
        return None
    return _OpportunityAttack(
        activities=(synthesized,), weapon=None, reach_ft=creature.melee_reach_ft
    )


def _resolve_opportunity_attack(live: _LiveCombat, reactor: Combatant, mover: Combatant) -> None:
    """Resolve ``reactor``'s opportunity attack (``_opportunity_attack_of``)
    on ``mover`` through the activity context its own turn's attack uses —
    cover, Fighting Styles, Martial Arts, proficiency, enchantments, weapon
    mastery, Sneak Attack and the target's readied Shield — flagged
    ``is_opportunity_attack``. It is a Reaction, not the Attack action, so
    none of that action's own bookkeeping applies: no Cleave, no Light
    property extra attack, no Loading mark, no Bardic Inspiration die."""
    attack = _opportunity_attack_of(live, reactor)
    if attack is None:  # pragma: no cover - _opportunity_attackers skips it
        return
    if attack.weapon is None:
        _resolve_monster_attack_activities(
            live, reactor, [mover], attack.activities, is_opportunity_attack=True
        )
        return
    targets = [mover]
    weapon = _enchanted_weapon(live, reactor, attack.weapon)
    # SRD Shield: "+5 bonus to AC, including against the triggering attack".
    _drain_targeted_reactions(
        live, trigger="hit_by_attack", triggering_actor_id=reactor.entity_id, targets=targets
    )
    payload = _build_hydration_payload(live, caster=reactor)
    pre_event_count = len(live.event_log)
    kwargs = _pc_attack_context_kwargs(
        live, reactor, targets, base_weapon=attack.weapon, weapon=weapon, payload=payload
    )
    # A melee attack on a creature still inside its reach (``_opportunity_attackers``
    # checked it) is never beyond a normal range, even an Unarmed Strike reaching
    # ``reach_ft``.
    kwargs["target_beyond_normal_range"] = {}
    actx = build_activity_context(
        reactor,
        targets,
        rng=live.rng,
        event_emitter=lambda ev: _emit(live, ev),
        slot_level=None,
        base_spell_level=None,
        spellcasting_ability=None,
        concentration=False,
        source_passive_effects=[],
        spell_book={},
        is_opportunity_attack=True,
        **kwargs,
    )
    for activity in attack.activities:
        resolve_activity(activity, actx, weapon=weapon)
    _consume_attack_roll_grants(live, reactor, targets, pre_event_count)
    _fold_mastery_procs(live, reactor.entity_id, actx)
    _break_hide(live, reactor.entity_id)
    _record_sneak_attack_spent(live, reactor, weapon, targets, actx, pre_event_count)
    _record_savage_attacker_spent(live, reactor, actx)
    _fold_resolution_outcome(live, reactor, spell=None, actx=actx, pre_event_count=pre_event_count)
    _sync_legendary_resistance(live, pre_event_count)


async def advance_monster_turn(
    handle: CombatHandle, *, legendary: bool = False, actor_id: str | None = None
) -> None:
    """Drive one monster turn through typed selection + the Activity resolver.

    Validation mirrors ``submit_player_intent``:

      - combat must not have ended
      - the current actor must be a non-Character entity (Monster /
        NPC); calling on a PC turn raises ``IntentRejectedError`` so
        a host can branch on it

    Selection: ``select_typed_monster_action``
    picks an action from the typed ``Monster.actions`` (fetched from the lib
    loader by ``monster_template_slug``); ``expand_action_to_parts`` fans
    multiattack out into its sub-attacks. Targeting: the lowest-HP living
    enemy, first in initiative order on a tie (the legacy gambit's
    ``target_priority="lowest_hp"`` semantics) — the party's members and the
    summons they own, a charmer excepted (``_select_monster_targets``). An
    area resolves against the creatures its best aim affects instead
    (``_resolve_monster_parts``).
    Resolution: each returned ``Activity`` runs through
    ``resolve_activity`` against a context
    built by ``build_activity_context`` — the same typed path as the PC
    turn.

    On dead monsters, an unresolvable slug, or no usable action (flee
    threshold, no attack, no PC targets), the orchestrator records
    ``IntentSubmitted(pass)`` and advances the turn without resolving any
    activity — the safe no-op the legacy dispatch also produced.

    ``legendary=True`` (C18 §Monster action economy) takes a SEPARATE path:
    a host calls this ANY time another creature's turn has just ended
    (including a PC's) to let one eligible encounter member spend a
    legendary action. It spends no action economy and ends no turn — see
    ``_eligible_legendary_actor`` and ``_take_legendary_action``. It changes
    the turn order only through a departure (C21): a summon it drops leaves
    the initiative order, shifting ``current_turn_index`` when it sat before
    the pointer, and when that summon was the current actor (its turn begun,
    nothing done yet) the next creature's turn opens once the legendary
    action has resolved — ``TurnStarted``, ``TurnPhase``, its turn-start
    hooks and death save, but no ``TurnEnded`` and no new window
    (``_hand_off_departed_turn``). ``actor_id`` picks a specific encounter
    member (else the first eligible one in initiative order); both raise
    ``IntentRejectedError("no_legendary_action", ...)`` when nothing
    qualifies right now.
    """
    live = _get_live(handle)
    if live.ended:
        raise IntentRejectedError("combat_ended", f"handle={handle.handle_id}")

    if legendary:
        _take_legendary_action(live, _eligible_legendary_actor(live, actor_id))
        _hand_off_departed_turn(live, turn_began=False)
        return

    current = _current_actor(live)
    if current.entity_type == "Character":
        raise IntentRejectedError(
            "not_actor_turn",
            f"current_turn={current.entity_id!r} is a Character, not a monster",
        )

    # C18 §Monster action economy — legendary-action reset, recharge rolls,
    # regeneration. Runs once per driven turn regardless of the flee/
    # incapacitated gate below (SRD ties these to turn start, not to
    # whether the monster acts); a dead monster has no turn start at all.
    _run_monster_turn_start(live, current)

    # SRD 5.2 Summon Dragon: "It obeys your verbal commands (no action
    # required by you). If you don't issue any, it takes the Dodge action
    # and uses its movement to avoid danger." A summon never reaches the
    # monster AI below: its own turn is the command its owner gave it
    # (``_run_summon_commanded_turn``) or else this uncommanded Dodge/pass.
    if current.entity_id in live.summons:
        if not _run_summon_commanded_turn(live, current):
            _run_uncommanded_summon_turn(live, current)
        return

    # Dead / unconscious monsters skip with a no-op record. The legacy
    # behavior-based flee gate (monster_ai.select_monster_action) is reapplied
    # here against the live Combatant — the typed selector only sees the static
    # Monster, so a wounded AGGRESSIVE/RANGED monster would otherwise keep
    # attacking instead of fleeing. A fleeing monster takes the same no-action /
    # pass path dead monsters take.
    skip_to_record_pass = (
        not current.is_alive
        or current.hp_current <= 0
        or _monster_is_fleeing(current)
        # SRD 5.2 Incapacitated — the monster takes no action this turn.
        or conditions_block_actions(_condition_names(current))
    )

    # Build the target list: every living enemy, a summon included
    # (lowest_hp priority — the legacy gambit's target rule). Empty targets
    # degrades to pass. SRD 5.2
    # Charmed — "You can't attack the charmer or target the charmer with
    # damaging abilities or magical effects" — is folded into
    # ``_select_monster_targets``. Knock-on, accepted deliberately:
    # ``enemies`` is also the threat list ``_execute_flee_retreat``
    # measures distance against, so a charmed FLEEING monster no longer
    # counts its charmer as someone to run from. Flavour-defensible (you do
    # not flee the creature that has charmed you) and SRD-silent, but it is
    # a second consequence of this one filter.
    enemies = _select_monster_targets(live, current)
    if not enemies:
        skip_to_record_pass = True

    chosen_target: Combatant | None = _lowest_hp_target(enemies)

    # ── Fleeing retreat ──────────────────────────────────────────
    # A live monster over the flee threshold spends its movement putting
    # distance between itself and the nearest threat BEFORE the pass is
    # recorded. Selection/attack stay gated off (``skip_to_record_pass`` is
    # already True for a fleeing monster), so the turn still collapses to
    # ``IntentSubmitted(intent_type="pass")`` — but now with real
    # ``ActorMoved`` events preceding it (reusing ``"pass"``;
    # no new IntentType is minted). Dead/unconscious monsters never retreat.
    current = _apply_monster_flee_stance(live, current, enemies)

    # ── Typed-Activity monster resolution (Foundry cutover, ─────────
    #
    # Fetch the typed ``Monster`` from the lib loader, pick its action, and fan
    # out multiattack. This is the sole monster-turn path; the legacy evaluator's IR
    # path is retired.
    monster_slug = live.monster_slug_by_entity.get(current.entity_id)
    monster_parts, cast_selection = _resolve_monster_activities(
        live, current, monster_slug, skip_to_record_pass, chosen_target
    )
    monster_activities = [activity for _, activity in monster_parts]
    has_action = bool(monster_activities) or cast_selection is not None

    # Monster gambit range awareness. When the chosen attack is
    # out of range, the monster MOVEs toward the target along the
    # shortest path, paying each step's cost out of its per-turn
    # movement budget. If the move brings it within range, the attack
    # then proceeds; otherwise the attack is skipped this turn (no
    # ``AttackFailed`` — the monster simply spent its movement closing
    # the distance). Ranged gambits whose normal range already covers
    # the target stay put and fire as before. Movement is planned and
    # executed BEFORE ``IntentSubmitted`` is emitted so the recorded
    # intent_type reflects what the monster actually does this turn —
    # ``"attack"`` when it ends in range, ``"pass"`` when it spent the
    # turn closing the gap.
    attack_skipped_due_to_range = False
    # SRD §Actions in Combat, Dash — set when the gambit below spends the
    # monster's Action on a Dash to close a gap it couldn't otherwise
    # cross. Dash IS the Action this turn (SRD action economy: one Action
    # per turn), so no Attack fires in the same turn a Dash was taken —
    # mirrors ``_handle_dash``'s Action-consuming default (the PC path also
    # supports a Rogue Cunning-Action Dash; monsters have no such
    # bonus-action gambit today, so this is always the base Action).
    dashed_this_turn = False
    if has_action and chosen_target is not None:
        monster_range_ft = _monster_attack_range_ft(monster_activities, current.melee_reach_ft)
        attacker_zone = live.actor_zone.get(current.entity_id)
        target_zone = live.actor_zone.get(chosen_target.entity_id)
        if (
            isinstance(monster_range_ft, int)
            and monster_range_ft > 0
            and attacker_zone is not None
            and target_zone is not None
            and not _in_range_with_los(live.topology, attacker_zone, target_zone, monster_range_ft)
        ):
            # Plan the path and walk it greedily within budget.
            path = live.topology.shortest_path(attacker_zone, target_zone)
            dashed_budget = _monster_dash_movement_budget(
                _path_total_distance(live.topology, path),
                current.movement_remaining,
                # SRD 5.2 Dash adds the creature's EFFECTIVE Speed: a Speed-0
                # monster (Grappled / Restrained / …) "can't increase" it, so
                # the gambit is declined outright (budget <= 0 → None).
                _effective_speed(current, live),
            )
            if dashed_budget is not None and current.action_available:
                dashed_this_turn = True
                for idx, c in enumerate(live.initiative):
                    if c.entity_id == current.entity_id:
                        live.initiative[idx] = c.model_copy(
                            update={
                                "action_available": False,
                                "movement_remaining": dashed_budget,
                            }
                        )
                        break
                _emit(
                    live,
                    DashTaken(
                        actor_id=current.entity_id,
                        doubled_movement_remaining=dashed_budget,
                        budget_consumed="action",
                    ),
                )
            # path[0] is attacker_zone; skip it. Walk forward step by step
            # until either (a) a step can't be taken (budget, or an
            # opportunity attack stopped the monster), or (b) we end up
            # within attack range.
            for next_zone in path[1:]:
                if not _take_walk_step(live, current.entity_id, next_zone):
                    break
                if _in_range_with_los(live.topology, next_zone, target_zone, monster_range_ft):
                    break
            # Re-check range after the (possibly partial) move.
            final_zone = live.actor_zone[current.entity_id]
            if not _in_range_with_los(live.topology, final_zone, target_zone, monster_range_ft):
                attack_skipped_due_to_range = True

    # SRD §Opportunity Attacks — if the AoO interrupted the move and dropped
    # the monster, the mover is dead; no attack fires this turn. The turn
    # still advances through the IntentSubmitted(pass) / TurnEnded shape so
    # initiative progresses to the next actor.
    mover_dead_post_aoo = current.entity_id in live.dead_ids
    # A resolved cast candidate takes its own branch below
    # (``_resolve_monster_cast``), never the mundane-attack one: it has no
    # ``monster_activities`` to walk and never engages the movement-closing
    # gambit above (``attack_skipped_due_to_range``/``dashed_this_turn`` stay
    # at their defaults for a cast turn — a stat-block spell resolves from
    # the monster's current position).
    will_cast = (
        cast_selection is not None
        and chosen_target is not None
        and not mover_dead_post_aoo
        and not dashed_this_turn
    )
    will_attack = (
        has_action
        and cast_selection is None
        and chosen_target is not None
        and not attack_skipped_due_to_range
        and not mover_dead_post_aoo
        and not dashed_this_turn
    )
    intent_type: IntentType = (
        "dash"
        if dashed_this_turn
        else ("cast_spell" if will_cast else ("attack" if will_attack else "pass"))
    )
    _emit(
        live,
        IntentSubmitted(
            actor_id=current.entity_id,
            intent_type=intent_type,
            target_id=chosen_target.entity_id if chosen_target is not None else None,
        ),
    )

    if will_cast:
        assert cast_selection is not None  # mypy: narrowed by will_cast
        assert chosen_target is not None  # mypy: narrowed by will_cast
        current = next(c for c in live.initiative if c.entity_id == current.entity_id)
        monster_action, cast_activity, spell = cast_selection
        _resolve_monster_cast(live, current, chosen_target, monster_action, cast_activity, spell)
    elif will_attack:
        # Re-read the actor snapshot — the move loop above may have
        # rebuilt the initiative slot with decremented movement_remaining;
        # the resolver runs against the post-move Combatant.
        current = next(c for c in live.initiative if c.entity_id == current.entity_id)
        assert chosen_target is not None  # mypy: narrowed by will_attack
        _resolve_monster_parts(live, current, chosen_target, monster_parts)

    # Advance the turn — the single shared path (F3a); this site used to carry
    # its own copy of the wrap-and-emit block.
    _end_turn_and_advance(live, current.entity_id)


def drain_pending_events(handle: CombatHandle) -> list[CombatEvent]:
    """Non-blocking drain of currently queued events for ``handle``.

    A host uses it to pump events emitted during a single
    ``submit_player_intent`` / ``advance_monster_turn`` call out to the
    broadcast layer without blocking on ``narration_events`` (which only
    terminates when ``end_combat`` enqueues its sentinel).

    The sentinel ``None`` (enqueued by ``end_combat``) is preserved on the
    queue so an in-flight ``narration_events`` consumer still terminates;
    it is not returned to the caller.
    """
    live = _get_live(handle)
    out: list[CombatEvent] = []
    while True:
        try:
            ev = live.event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if ev is None:
            # Sentinel — put it back so any active ``narration_events``
            # consumer still sees the terminator and exits cleanly.
            live.event_queue.put_nowait(None)
            break
        out.append(ev)
    return out


async def narration_events(
    handle: CombatHandle,
) -> AsyncIterator[CombatEvent]:
    """Stream the combat's events to the narrator.

    The iterator terminates when ``end_combat`` is called — the closer
    drains a sentinel ``None`` onto the queue and we stop iteration on
    receiving it.
    """
    live = _get_live(handle)
    while True:
        event = await live.event_queue.get()
        if event is None:
            return
        yield event


def _derive_ended_reason(live: _LiveCombat) -> Literal["victory", "defeat_tpk", "flee", "forced"]:
    """SRD §Combat resolution — derive ``ended_reason`` from final tracked state.

    - all encounter members dead → victory
    - all party members dead → defeat_tpk
    - every living foe has fled (``Combatant.has_fled``) → flee
    - otherwise → forced (caller closed mid-combat)
    """
    all_foes_dead = all(eid in live.dead_ids for eid in live.encounter_ids)
    all_pcs_dead = all(eid in live.dead_ids for eid in live.party_ids)
    if all_foes_dead and live.encounter_ids:
        return "victory"
    if all_pcs_dead and live.party_ids:
        return "defeat_tpk"
    living_foes = [
        c
        for c in live.initiative
        if c.entity_id in live.encounter_ids and c.entity_id not in live.dead_ids
    ]
    if living_foes and all(c.has_fled for c in living_foes):
        return "flee"
    return "forced"


def _project_outcome(live: _LiveCombat) -> CombatOutcome:
    """Fold ``_LiveCombat`` event-derived running state into a ``CombatOutcome``.

    Residual HP / temp HP — from the tracked dicts updated by ``_emit``, less
    a still-running Polymorph's own grant.
    Carried conditions — every still-active ``ConditionApplied`` for a
    surviving combatant. Carried-effect duration is taken from the most
    recent ``EffectApplied`` (the duration the effect was registered with).
    Deaths — the ordered ``DeathRecord`` list synthesized in ``_emit``.
    XP — SRD §Encounter XP, summed across dead encounter members and divided
    equally among surviving PCs (a solo PC takes the full total).
    Loot drops — dropped from this seam's projection (loot tables aren't
    plumbed into ``EncounterMemberSpec`` yet); a host looks up a monster's
    ``loot_table`` itself.
    Expended resources — accumulated from ``EffectApplied`` with
    ``is_concentration=True`` during the combat.
    """
    residual_hp = {eid: hp for eid, hp in live.tracked_hp.items() if eid in live.party_ids}
    # Effects end with the combat, and a live Polymorph's own Temporary Hit
    # Points "vanish if any remain when the spell ends" (SRD 5.2), as
    # ``_revert_transform_on_expiry`` empties them when it ends in combat.
    vanishing = {eid for eid, t in live.transforms.items() if t.clears_temp_hp_on_end}
    residual_temp_hp = {
        eid: thp
        for eid, thp in live.tracked_temp_hp.items()
        if eid in live.party_ids and thp > 0 and eid not in vanishing
    }

    # SRD §Encounter XP: total XP from dead foes ÷ surviving PCs.
    total_xp = sum(
        live.xp_value_by_entity.get(eid, 0) for eid in live.dead_ids if eid in live.encounter_ids
    )
    surviving_pcs = [eid for eid in live.party_ids if eid not in live.dead_ids]
    xp_awarded: dict[str, int] = {}
    if total_xp > 0 and surviving_pcs:
        per_pc = total_xp // len(surviving_pcs)
        if per_pc > 0:
            xp_awarded = {pc: per_pc for pc in surviving_pcs}

    loot_drops: list[LootDrop] = []

    return CombatOutcome(
        handle_id=live.handle_id,
        ended_reason=_derive_ended_reason(live),
        deaths=list(live.deaths_recorded),
        residual_hp=residual_hp,
        residual_temp_hp=residual_temp_hp,
        loot_drops=loot_drops,
        xp_awarded=xp_awarded,
        expended_resources={k: dict(v) for k, v in live.expended_resources.items()},
    )


class SwapInitiativeResult(BaseModel):
    """Outcome of ``swap_initiative``: SRD 5.2 Alert — Initiative Swap."""

    model_config = ConfigDict(extra="forbid")

    swapped: bool = False
    #: A ``RejectionReason`` value when ``swapped`` is False; ``None`` on success.
    rejected_reason: str | None = None
    initiative_before: dict[str, int] = Field(default_factory=dict)
    initiative_after: dict[str, int] = Field(default_factory=dict)


async def swap_initiative(
    handle: CombatHandle,
    swapper_id: str,
    ally_id: str,
) -> SwapInitiativeResult:
    """SRD 5.2 Alert — Initiative Swap: "Immediately after you roll Initiative,
    you can swap your Initiative with the Initiative of one willing ally in the
    same combat. You can't make this swap if you or the ally has the
    Incapacitated condition."

    This is the one thing Alert does that a roll modifier cannot: the
    initiative ORDER of two party members is exchanged. It is its own call
    because it happens once, BEFORE any turn, and `submit_player_intent` is
    gated on whose turn it is.

    Refusals are named, and nothing changes on a refusal:

    * `alert_swap_unavailable` — ``swapper_id`` does not hold Alert.
    * `unknown_combatant` — either id names no creature in the fight.
    * `not_a_willing_ally` — ``ally_id`` is a foe, not a party member. The
      engine cannot ask the other hero, so "willing" is the host's claim:
      the host submits the swap only when that player agreed.
    * `alert_swap_disabled` — either creature has the Incapacitated
      condition. The book names this condition specifically; Unconscious,
      Paralyzed, Petrified and Stunned all IMPLY it, so they refuse too.
    * `alert_swap_used` — one swap per Initiative roll.

    """
    live = _get_live(handle)
    swapper = _find_combatant(live, swapper_id)
    ally = _find_combatant(live, ally_id)

    def _refused(reason: str) -> SwapInitiativeResult:
        return SwapInitiativeResult(swapped=False, rejected_reason=reason)

    if swapper is None or ally is None:
        return _refused("unknown_combatant")
    if swapper_id == ally_id:
        return _refused("not_a_willing_ally")
    if _ALERT not in getattr(swapper, "feats", ()):
        return _refused("alert_swap_unavailable")
    if ally.entity_type != "Character":
        return _refused("not_a_willing_ally")
    if swapper_id in live.alert_swaps_used:
        return _refused("alert_swap_used")
    for creature in (swapper, ally):
        # ``Combatant.conditions`` holds ActiveCondition records; the rule
        # layer reads names, so the record's own value is what it sees. Its
        # IMPLIES table then covers Unconscious, Paralyzed, Petrified and
        # Stunned — each of which carries Incapacitated.
        names = [
            getattr(record, "condition", record)
            for record in creature.conditions
        ]
        if is_condition_active(Condition.INCAPACITATED, names):
            return _refused("alert_swap_disabled")
    before = {swapper_id: swapper.initiative, ally_id: ally.initiative}
    _update_combatant(live, swapper_id, initiative=ally.initiative)
    _update_combatant(live, ally_id, initiative=swapper.initiative)
    live.initiative.sort(key=lambda c: (-c.initiative, -c.dexterity, c.entity_id))
    live.alert_swaps_used.add(swapper_id)
    return SwapInitiativeResult(
        swapped=True,
        initiative_before=before,
        initiative_after={swapper_id: ally.initiative, ally_id: swapper.initiative},
    )


async def end_combat(handle: CombatHandle) -> EndCombatResult:
    """Close the combat and return the projected outcome.

    Idempotent: calling twice returns the same outcome (with an empty
    ``events`` list on subsequent calls — the close events were only
    emitted once on the first invocation), no re-emission of events. The
    ended combat stays readable — a repeat ``end_combat``, ``get_live``,
    ``drain_pending_events``, ``narration_events`` — until
    ``_ENDED_COMBATS_KEPT`` later combats have ended; its handle then raises
    ``UnknownHandleError`` (``_keep_ended``).
    """
    live = _get_live(handle)
    surviving = tuple(eff for target_list in live.active_effects.values() for eff in target_list)
    if live.ended and live.final_outcome is not None:
        return EndCombatResult(
            outcome=live.final_outcome,
            events=[],
            final_active_effects=surviving,
        )

    outcome = _project_outcome(live)
    end_events: list[CombatEvent] = []
    live.event_listeners.append(end_events.append)
    try:
        _emit(live, CombatEnded(reason=outcome.ended_reason))
    finally:
        live.event_listeners.remove(end_events.append)
    # Sentinel to terminate any active ``narration_events`` consumers.
    live.event_queue.put_nowait(None)

    live.ended = True
    live.final_outcome = outcome
    _keep_ended(live)
    # Re-snapshot after CombatEnded emission in case any listener mutated
    # the active_effects registry (e.g. expire handler).
    surviving = tuple(eff for target_list in live.active_effects.values() for eff in target_list)
    return EndCombatResult(
        outcome=outcome,
        events=end_events,
        final_active_effects=surviving,
    )


def _keep_ended(live: _LiveCombat) -> None:
    """Record ``live`` as ended and release the oldest ended combat beyond
    ``_ENDED_COMBATS_KEPT``. A live combat is never released, and neither is a
    new combat a later ``start_combat`` registered under a reused handle id
    (``combat:<session>:<seed>``): only the very combat that ended leaves."""
    _ENDED.append((live.handle_id, live))
    while len(_ENDED) > _ENDED_COMBATS_KEPT:
        handle_id, ended = _ENDED.popleft()
        if _REGISTRY.get(handle_id) is ended:
            del _REGISTRY[handle_id]


def _reset_registry_for_tests() -> None:
    """Wipe the in-memory registry. Test-only — no production caller.

    Pytest's per-function isolation runs each test against fresh module
    state by convention, but the registry is module-global by design
    here. This helper lets boundary
    tests start from a clean slate.
    """
    _REGISTRY.clear()
    _ENDED.clear()




__all__ = [
    "CombatHandle",
    "CombatSeamError",
    "EncounterMemberSpec",
    "EndCombatResult",
    "GridScene",
    "IntentRejectedError",
    "LiveCombatView",
    "PartyMemberSpec",
    "PlayerIntent",
    "StartCombatResult",
    "UnknownHandleError",
    "advance_monster_turn",
    "drain_pending_events",
    "end_combat",
    "get_actor_active_effects",
    "get_live",
    "narration_events",
    "start_combat",
    "submit_player_intent",
]


from dnd5e_engine.results import EndCombatResult, StartCombatResult  # noqa: E402

StartCombatResult.model_rebuild()
EndCombatResult.model_rebuild()