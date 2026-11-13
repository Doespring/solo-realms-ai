"""Pure interpreter for always-on passive derived stats.

Maps the **allowlisted** Foundry dotted change-keys (``system.traits.dr.value``,
``system.attributes.senses.*``) and species ``trait_grants`` tokens
(``dr:<type>`` / ``di:<type>``) into a typed ``DerivedPassiveStats``
(resistances, immunities, senses). The ``build_party_member`` seam calls this
with the PC's always-on feature changes + species data and projects the result
onto the spec.

Also projects ``condition_immunities`` (``system.traits.ci.value``) and movement
(``system.attributes.movement.*``) — a flat ``walk_speed_bonus`` plus a typed
``CombatantMovementModes`` carrier.

PURITY CONTRACT: zero I/O, zero logging, never raises. Allowlist misses,
deferred keys (languages, ``ac.calc``, ability/proficiency grants), and
non-literal values are collected into ``skipped_keys`` so the calling seam can
log them — the interpreter itself has no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence

from dnd5e_srd_data.schema.common import PassiveEffectChange, Senses
from pydantic import BaseModel, ConfigDict

# Foundry CONST.ACTIVE_EFFECT_MODES values the senses allowlist honors.
_MODE_MULTIPLY = 1  # Foundry "multiply": value is the factor
_MODE_ADD = 2
_MODE_UPGRADE = 4  # "max" semantics

# Allowlisted senses change-key suffixes → CombatantSenses field name.
_SENSE_KEYS = {
    "system.attributes.senses.darkvision": "darkvision",
    "system.attributes.senses.blindsight": "blindsight",
    "system.attributes.senses.tremorsense": "tremorsense",
    "system.attributes.senses.truesight": "truesight",
}
_DR_KEY = "system.traits.dr.value"
_DI_KEY = "system.traits.di.value"
_CI_KEY = "system.traits.ci.value"

# The Foundry corpus carries ONE malformed damage type: Armor of Vulnerability
# ships ``bludegoning`` on all three of its siblings (measured across all 546
# items -- 3 correct ``bludgeoning``, 3 misspelled, and every misspelling is
# this armour). Left alone the resistance never matches a real hit, so the item
# silently loses a third of its rule. Repaired here, at the read, rather than in
# the data: the digest stays what upstream shipped.
_DAMAGE_TYPE_TYPOS: Final[dict[str, str]] = {"bludegoning": "bludgeoning"}


def _damage_type(value: str) -> str:
    """A damage-type token from a ``system.traits`` change, typos repaired."""
    token = str(value).strip().strip('"').strip()
    return _DAMAGE_TYPE_TYPOS.get(token, token)

# SRD §Movement. The flat walk-speed change folds into a scalar bonus
# (composed with the species base_speed at the build seam); the non-walk modes
# land on the typed ``CombatantMovementModes`` carrier. Foundry's symbolic
# ``@attributes.movement.walk`` token (Roving's "equal to your Speed") resolves
# to the boosted walk speed — the single formula token this seam handles (no
# general formula engine).
_MOVE_WALK_KEY = "system.attributes.movement.walk"
_MOVE_MODE_KEYS = {
    "system.attributes.movement.climb": "climb",
    "system.attributes.movement.swim": "swim",
    "system.attributes.movement.fly": "fly",
    "system.attributes.movement.burrow": "burrow",
}
_MOVE_WALK_REF = "@attributes.movement.walk"
# Boots of the Winterlands 'Winter Strider': "You ignore Difficult Terrain
# created by ice or snow." Names the terrain KINDS, not a blanket exemption.
_MOVE_IGNORE_DIFFICULT_KEY = "system.attributes.movement.ignoredDifficultTerrain"
# Ioun Stone of Awareness / Alert: "Advantage on Initiative rolls." Foundry
# spells it as mode 2 with value 1 on the initiative roll mode; 1 =
# advantage, -1 = disadvantage.
# SRD 5.2 Demon Armor: "You know Abyssal" -- an item that TEACHES a language.
# Recorded so the sheet stops dropping it; the engine runs no dialogue.
_LANGUAGE_KEY = "system.traits.languages.value"
_INIT_MODE_KEY = "system.attributes.init.roll.mode"

# Foundry's condition-immunity (``ci``) trait uses the damage-type-style token
# ``"poison"`` for the Poisoned condition; every other SRD condition's ``ci``
# token already equals its condition slug. Normalize the sole irregular token
# so the projected ``condition_immunities`` holds condition slugs the
# ConditionApplied emit-gate compares directly. A single-entry alias,
# deliberately not a general trait-vocabulary engine.
_CI_TOKEN_TO_CONDITION = {"poison": "poisoned"}


class CombatantSenses(BaseModel):
    """A creature's special senses in feet (lib ``Senses`` minus
    passive_perception). ``None`` = sense unavailable. Carried on the spec and
    the live ``Combatant``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    darkvision: int | None = None
    blindsight: int | None = None
    tremorsense: int | None = None
    truesight: int | None = None


class CombatantMovementModes(BaseModel):
    """A creature's non-walk movement speeds in feet (SRD §Movement). ``None`` =
    that mode unavailable. Mirrors ``CombatantSenses``'s shape; carried on
    the spec and the live ``Combatant`` alongside the scalar walk ``base_speed``.
    Kept multi-mode (not collapsed to one scalar — collapsing is lossy: a
    creature may have distinct climb / swim / fly / burrow speeds)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    climb: int | None = None
    swim: int | None = None
    fly: int | None = None
    burrow: int | None = None


class DerivedPassiveStats(BaseModel):
    """Typed output of ``interpret_passive_stats`` — the always-on passive
    deltas to project onto a PC's spec at combat-build time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resistances: tuple[str, ...] = ()
    immunities: tuple[str, ...] = ()
    # SRD §Condition Immunity — condition slugs the creature can't suffer
    # (Nature's Ward → ``"poisoned"``). ci trait tokens are normalized to
    # condition slugs before landing here (see ``_CI_TOKEN_TO_CONDITION``).
    condition_immunities: tuple[str, ...] = ()
    senses: CombatantSenses = CombatantSenses()
    # SRD §Movement — flat walk-speed bonus (Roving's +10) the build seam adds
    # to the species base_speed; and the typed non-walk modes carrier.
    walk_speed_bonus: int = 0
    # Multiplies the creature walk speed (Boots of Speed: x2). 1 = unchanged.
    walk_speed_multiplier: int = 1
    movement_modes: CombatantMovementModes = CombatantMovementModes()
    ignored_difficult_terrain: tuple[str, ...] = ()
    # Languages an item grants (Demon Armor -> abyssal).
    languages: tuple[str, ...] = ()
    # SRD 5.2 Ioun Stone of Awareness: "Advantage on Initiative rolls."
    # 1 = advantage, -1 = disadvantage, 0 = a straight roll.
    initiative_advantage: int = 0
    skipped_keys: tuple[str, ...] = ()


def _parse_literal_int(value: str) -> int | None:
    """Return the int if ``value`` is a plain numeric literal, else ``None``.

    Defends against symbolic Foundry values (``@scale.*`` formulas) reaching a
    numeric field. Quote-escaping (``"\"60\""``) is stripped before parsing.
    """
    cleaned = value.strip().strip('"').strip()
    try:
        return int(cleaned)
    except ValueError:
        return None


def _resolve_movement_modes(
    mode_changes: Sequence[tuple[str, str, int]],
    *,
    resolved_walk: int,
    skipped: list[str],
) -> dict[str, int]:
    """Fold buffered non-walk movement changes into a ``{field: speed}`` dict.

    The symbolic ``@attributes.movement.walk`` token resolves to ``resolved_walk``
    (the boosted walk speed); any other symbolic value, or an unmodeled mode, is
    appended to ``skipped``. Mode 4 = max (upgrade), mode 2 = additive,
    mode 1 = multiply (Boots of Speed double every speed the bearer has).
    """
    move_values: dict[str, int] = {}
    # The walk mode multiplies ``species_base_speed``, which this helper does
    # not own; the caller applies it. Returned as ``"walk"`` and popped by
    # ``interpret_passive_stats`` before building ``CombatantMovementModes``.
    for field, raw_value, mode in mode_changes:
        if raw_value.strip() == _MOVE_WALK_REF:
            literal: int | None = resolved_walk
        else:
            literal = _parse_literal_int(raw_value)
        if literal is None:
            skipped.append(f"system.attributes.movement.{field}")
            continue
        current = move_values.get(field)
        if mode == _MODE_UPGRADE:
            move_values[field] = literal if current is None else max(current, literal)
        elif mode == _MODE_ADD:
            move_values[field] = literal if current is None else current + literal
        elif mode == _MODE_MULTIPLY:
            # Boots of Speed write multiply on all five kinds, but they do
            # not GRANT a fly/swim/climb/burrow speed the bearer lacks: SRD
            # 5.2 doubling applies to a speed the creature already has.
            # A multiply with no established base leaves the mode unset.
            if current is not None:
                move_values[field] = current * literal
            else:
                skipped.append(f"system.attributes.movement.{field}")
        else:
            skipped.append(f"system.attributes.movement.{field}")
    return move_values


def interpret_passive_stats(
    *,
    changes: Sequence[PassiveEffectChange],
    trait_grants: Sequence[str],
    species_senses: Senses | None,
    species_base_speed: int = 30,
) -> DerivedPassiveStats:
    """Interpret always-on passive changes + species data into typed deltas.

    ``species_base_speed`` is the creature's unmodified walking speed (feet); the
    symbolic ``@attributes.movement.walk`` token on a non-walk mode resolves
    against the *boosted* walk speed (``species_base_speed + walk_speed_bonus``).

    PURE: no I/O, no logging, never raises. Unknown / deferred keys and
    non-literal numeric values are returned in ``skipped_keys``.
    """
    resistances: list[str] = []
    immunities: list[str] = []
    condition_immunities: list[str] = []
    skipped: list[str] = []
    sense_values: dict[str, int] = {}
    # Flat additive walk-speed bonus (Roving's +10). Non-walk mode changes are
    # buffered and resolved *after* the loop, once walk_speed_bonus is known, so
    # a ``@attributes.movement.walk`` token sees the boosted speed.
    walk_speed_bonus = 0
    # MULTIPLY on the walk mode, factored into ``species_base_speed`` at the
    # very end so a symbolic ``@attributes.movement.walk`` token still sees
    # the real walk speed.
    walk_multiplier = 1
    mode_changes: list[tuple[str, str, int]] = []
    ignored_terrain: list[str] = []
    initiative_advantage = 0
    languages: list[str] = []

    # Species trait_grants: dr:<t> -> resistance, di:<t> -> immunity,
    # anything else (ci:, languages:, ...) -> skipped (deferred / out of scope).
    for token in trait_grants:
        prefix, _, rest = token.partition(":")
        if prefix == "dr" and rest:
            resistances.append(rest)
        elif prefix == "di" and rest:
            immunities.append(rest)
        else:
            skipped.append(token)

    for change in changes:
        key = change.key
        if key == _DR_KEY:
            resistances.append(_damage_type(change.value))
        elif key == _DI_KEY:
            immunities.append(_damage_type(change.value))
        elif key == _CI_KEY:
            token = change.value.strip().strip('"').strip()
            if token:
                condition_immunities.append(_CI_TOKEN_TO_CONDITION.get(token, token))
        elif key in _SENSE_KEYS:
            literal = _parse_literal_int(change.value)
            if literal is None:
                skipped.append(key)
                continue
            field = _SENSE_KEYS[key]
            current = sense_values.get(field)
            if change.mode == _MODE_UPGRADE:
                sense_values[field] = literal if current is None else max(current, literal)
            elif change.mode == _MODE_ADD:
                sense_values[field] = literal if current is None else current + literal
            else:
                # other modes (override/multiply/downgrade/custom) not modeled
                skipped.append(key)
        elif key == _LANGUAGE_KEY:
            language = change.value.strip().strip('"').strip().lower()
            if language and language not in languages:
                languages.append(language)
        elif key == _INIT_MODE_KEY:
            literal = _parse_literal_int(change.value)
            if literal is None:
                skipped.append(key)
            else:
                initiative_advantage += literal
        elif key == _MOVE_IGNORE_DIFFICULT_KEY:
            terrain = change.value.strip().strip('"').strip().lower()
            if terrain and terrain not in ignored_terrain:
                ignored_terrain.append(terrain)
        elif key == _MOVE_WALK_KEY or key in _MOVE_MODE_KEYS:
            # A non-walk mode (climb/swim/fly/burrow) defers to the loop tail.
            # The walk mode is read here: Boots of Speed write mode 1
            # (multiply) with value 2, and mode 1 used to be skipped
            # outright, so a pair of boots lent nothing at all. Mode 2
            # (add) is the ordinary flat speed bonus -- Roving, Fast
            # Movement, horseshoes -- which must keep working.
            literal = _parse_literal_int(change.value)
            if key == _MOVE_WALK_KEY:
                if literal is None:
                    skipped.append(key)
                elif change.mode == _MODE_MULTIPLY:
                    walk_multiplier *= literal
                elif change.mode == _MODE_ADD:
                    walk_speed_bonus += literal
                else:
                    skipped.append(key)
                continue
            mode_changes.append((_MOVE_MODE_KEYS[key], change.value, change.mode))
        else:
            # Any non-allowlisted key (ci, bonuses, hp, ...) -> deferred.
            skipped.append(key)

    # Merge species senses by max (mode-4 / upgrade semantics).
    if species_senses is not None:
        for field in ("darkvision", "blindsight", "tremorsense", "truesight"):
            species_value = getattr(species_senses, field)
            if species_value is None:
                continue
            current = sense_values.get(field)
            sense_values[field] = species_value if current is None else max(current, species_value)

    # Resolve non-walk movement modes now that walk_speed_bonus is settled. The
    # symbolic ``@attributes.movement.walk`` token ("equal to your Speed")
    # resolves to the boosted walk speed; any other symbolic value is deferred.
    move_values = _resolve_movement_modes(
        mode_changes, resolved_walk=species_base_speed + walk_speed_bonus, skipped=skipped
    )
    # A MULTIPLY on the walk mode scales the walk speed itself (Boots of
    # Speed: "walk x2"), so it leaves here as a multiplier rather than a
    # mode. It composes with the flat bonus the ordinary way: x2 of the walk
    # speed the creature would otherwise have.
    # The caller applies ``walk_multiplier`` to the species walk speed (it
    # carries the armour penalty too), so this stays a pure return.

    return DerivedPassiveStats(
        resistances=tuple(resistances),
        immunities=tuple(immunities),
        condition_immunities=tuple(condition_immunities),
        senses=CombatantSenses(**sense_values),
        walk_speed_bonus=walk_speed_bonus,
        walk_speed_multiplier=walk_multiplier,
        movement_modes=CombatantMovementModes(**move_values),
        ignored_difficult_terrain=tuple(ignored_terrain),
        languages=tuple(languages),
        initiative_advantage=initiative_advantage,
        skipped_keys=tuple(skipped),
    )
