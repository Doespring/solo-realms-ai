"""PassiveEffect (lib definition) → ActiveEffect (runtime) builder.

The typed lib ships ``PassiveEffect`` — the effect *definition* riding on a
spell/item (``changes`` / ``statuses`` / ``duration``). The resolver emits the
engine's ``ActiveEffect`` (the runtime effect carried by ``EffectApplied``).
This module is the one translation seam between the two.

``ActiveEffectChange.mode`` is a string Literal (``ChangeMode``); the lib's
``PassiveEffectChange.mode`` is the Foundry ``CONST.ACTIVE_EFFECT_MODES`` int.
``_MODE_MAP`` is the single source of truth for that int→str correspondence;
its values are pinned to the ``ChangeMode`` Literal order (index == int key).

The ``id``/``origin`` slug conventions MIRROR ``effects/ieffect2.py``
(``_effect_id_from_name`` / ``_origin_from_name``) verbatim — the orchestrator
parses ``origin`` back into ``cast:<slug>:<caster_id>``, so any drift
here breaks that round-trip.
"""

from __future__ import annotations

import logging
import re
import typing
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, get_args

from dnd5e_engine.activities.formula import resolve_roll_data
from dnd5e_engine.events import ConditionApplied, ConditionType, EffectApplied
from dnd5e_engine.rules.conditions import granted_condition_immunities
from dnd5e_engine.types.combat import Combatant
from dnd5e_engine.types.effects import (
    ActiveEffect,
    ActiveEffectChange,
    ActiveEffectDuration,
    ChangeMode,
)

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import (
        PassiveEffect,
        PassiveEffectChange,
        _ActivityBaseWithEffects,
    )

    from .context import ActivityResolutionContext

_LOGGER = logging.getLogger(__name__)

# The valid SRD condition ids — a status string becomes a ConditionApplied only
# when it names one of these. Derived from the ConditionType Literal so the two
# never drift.
_CONDITION_VALUES: frozenset[str] = frozenset(get_args(ConditionType))

# Foundry CONST.ACTIVE_EFFECT_MODES → ChangeMode Literal. Pinned to the Literal
# member order in types/effects.py (index == int key); test_effects_builder
# asserts the two stay in lockstep.
_MODE_MAP: dict[int, ChangeMode] = {
    0: "custom",
    1: "multiply",
    2: "add",
    3: "downgrade",
    4: "upgrade",
    5: "override",
}

_DEFAULT_CHANGE_PRIORITY = 20


def is_condition_immune(target: Combatant, condition: str, *, imposed: Iterable[str] = ()) -> bool:
    """SRD 5.2 §Immunity: "If you have Immunity to a damage type or a
    condition, it doesn't affect you in any way." A target immune to
    ``condition`` never has it attach.

    Immune when the condition is among ``target.condition_immunities`` (its
    stat block, its features) or granted by a condition it has
    (``rules.conditions.granted_condition_immunities``: Petrified grants
    Poisoned). ``imposed`` names the other conditions arriving with this one
    (one effect's statuses), so an effect that both petrifies and poisons
    leaves its target Petrified only.

    The one gate every action or effect that applies a condition consults: the
    effect-status riders below, ``activities/mastery.py``'s Topple, the
    orchestrator's effect folds, and the conditions its actions apply
    (Grapple, Shove, Hide). A save that comes first (Topple's, a grapple's)
    still rolls; only the condition is withheld.
    """
    if condition in target.condition_immunities:
        return True
    held = [ac.condition for ac in target.conditions]
    # A status the target is immune to never lands, so it grants nothing.
    # Filtering static immunities alone is exact while no condition grants
    # immunity to a condition that grants one (Petrified grants Poisoned only).
    landing = [s for s in imposed if s not in target.condition_immunities]
    return condition in granted_condition_immunities([*held, *landing])


def _name_slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def _effect_id_from_name(name: str) -> str:
    """Synthesize an ActiveEffect.id from the effect name (mirrors ieffect2)."""
    return f"effect:{_name_slug(name)}"


def _origin_from_name(name: str, caster_id: str) -> str:
    """Synthesize an ActiveEffect.origin from name + caster (mirrors ieffect2)."""
    return f"cast:{_name_slug(name)}:{caster_id}"


def _passive_change_to_active(
    ch: PassiveEffectChange, ctx: ActivityResolutionContext | None
) -> ActiveEffectChange:
    """Translate one lib ``PassiveEffectChange`` to an ``ActiveEffectChange``.

    ``mode`` int→str via ``_MODE_MAP``; an out-of-range int defaults to
    ``"custom"`` and logs loudly. ``priority`` defaults to 20 when the source
    leaves it ``None``.

    ``value`` stays the Foundry string EXCEPT for level-scaled roll-data tokens
    (``@scale.*`` / ``@classes.*``): Rage's ``system.bonuses.mwak.damage`` ships
    ``"+@scale.barbarian.rage-damage"``, which must be the CONCRETE bonus
    (``"+2"`` at L5) the moment the effect lands so the next attack's hydration
    fold reads a real number, not an unresolved token. When ``ctx`` is supplied
    and the value carries an ``@``-token, it is resolved here at apply-time via
    the same pure formula layer the dice/heal paths use.
    """
    mode = _MODE_MAP.get(ch.mode)
    if mode is None:
        _LOGGER.warning(
            "effect_change_mode_unknown mode=%s key=%s value=%s — defaulting to custom",
            ch.mode,
            ch.key,
            ch.value,
        )
        mode = "custom"
    priority = ch.priority if ch.priority is not None else _DEFAULT_CHANGE_PRIORITY
    value = ch.value
    if ctx is not None and isinstance(value, str) and "@" in value:
        # Resolve @scale.*/@classes.* (and any other roll-data token) against the
        # caster's pre-resolved carriers. No governing ability is supplied —
        # feature passive-effect change values never carry a bare ``@mod``.
        value = resolve_roll_data(value, ctx)
    return ActiveEffectChange(key=ch.key, mode=mode, value=value, priority=priority)


def _duration_from_passive(duration: dict[str, typing.Any] | None) -> ActiveEffectDuration:
    """Map the lib's free-form duration dict to the structured runtime duration.

    Only ``rounds`` / ``turns`` / ``seconds`` are carried; absent keys (and a
    ``None``/empty dict) leave the corresponding field ``None``.
    """
    if not duration:
        return ActiveEffectDuration()
    return ActiveEffectDuration(
        rounds=duration.get("rounds"),
        turns=duration.get("turns"),
        seconds=duration.get("seconds"),
    )


def _effect_flags(
    pe: PassiveEffect,
    *,
    concentration: bool,
    extra_flags: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """``ActiveEffect.flags`` for the effect ``pe`` is about to become.

    ``concentration`` rides from the cast that applied it; ``extra_flags`` are
    the caller's own keys (an allowlisted conjuration's weapon slug, the item a
    repeat activation toggles). ``escape_dc`` is the definition's own SRD
    ``(escape DC N)``: it travels on the runtime effect because that is the only
    place the condition-application fold (``orchestrator._emit_apply_effect_applied``)
    can still see it when it materialises the victim's ``ActiveCondition`` --
    which must store the DC so ``escape_grapple`` reads a real number instead of
    refusing (``_escapable_grapple``: "nothing to roll against otherwise").
    """
    flags: dict[str, Any] = {"concentration": True} if concentration else {}
    if pe.escape_dc is not None:
        flags["escape_dc"] = int(pe.escape_dc)
    flags.update(extra_flags or {})
    return flags


def passive_effect_to_active_effect(
    pe: PassiveEffect,
    *,
    target_id: str,
    caster_id: str,
    concentration: bool = False,
    ctx: ActivityResolutionContext | None = None,
    extra_flags: Mapping[str, Any] | None = None,
) -> ActiveEffect:
    """Build the runtime ``ActiveEffect`` the resolver emits for one target.

    ``id``/``origin`` follow the ieffect2 slug conventions so the orchestrator
    can parse them back. ``flags`` carries ``{"concentration": True}``
    only when the caster's cast is concentration-gated, then ``extra_flags``
    (an allowlisted conjuration's keys, e.g. the enchanted weapon's slug).

    ``ctx`` (when supplied) resolves level-scaled roll-data tokens in each
    change value at apply-time (Rage's ``@scale.barbarian.rage-damage`` → the
    concrete ``+2``); absent, change values stay verbatim Foundry strings.
    """
    # An item-sourced effect is qualified by its item in BOTH id and origin:
    # 23 effect NAMES are shared by several items in the corpus ("Cold
    # Resistance" on armor-of-resistance, boots-of-the-winterlands,
    # potion-of-resistance and ring-of-resistance), and expiry removes an
    # effect by (id, origin), so an unqualified pair would let one item
    # expiring take the other item's effect with it. Spells keep the bare
    # ieffect2 shape: their id/origin round-trip through the cast: parser.
    item_id = (extra_flags or {}).get("item")
    return ActiveEffect(
        id=_effect_id_from_name(f"{pe.name} ({item_id})" if item_id else pe.name),
        name=pe.name,
        origin=_origin_from_name(
            f"{pe.name} ({item_id})" if item_id else pe.name, caster_id
        ),
        target_id=target_id,
        disabled=False,
        transfer=pe.transfer,
        duration=_duration_from_passive(pe.duration),
        changes=[_passive_change_to_active(ch, ctx) for ch in pe.changes],
        statuses=set(pe.statuses),
        flags=_effect_flags(pe, concentration=concentration, extra_flags=extra_flags),
    )


_MOVEMENT_TOKEN_RE = re.compile(r"^@attributes\.movement\.([a-z]+)$")


def _movement_reference_speed(mode: str, target: Combatant, ctx: ActivityResolutionContext | None) -> int | None:
    """The creature's own rating for ``mode``, as ``@attributes.movement.<mode>``
    means it.

    ``walk`` is the creature's own Speed; the other modes live on
    ``Combatant.movement_modes`` and may be unset ("-" in the stat block), in
    which case the reference has nothing to point at.

    The live combatant the orchestrator hands down is NOT the whole story: a
    spell's Speed bonus (Haste) and the effect that is landing right now
    (Spider Climb, Aquatic Adaptation) live on the projected walk speed / the
    projected movement modes, never on this frozen ``Combatant``. Both are
    optional; only a supplied value wins, so every caller that passes no
    context keeps its exact behaviour.
    """
    source = target.base_speed if mode == "walk" else getattr(target.movement_modes, mode, None)
    if ctx is None:
        return source
    if mode == "walk":
        return ctx.movement_walk_speed or source
    if ctx.movement_modes is not None:
        return getattr(ctx.movement_modes, mode, None) or source
    return source


def _movement_changes_resolved(
    changes: Sequence[PassiveEffectChange],
    target: Combatant,
    ctx: ActivityResolutionContext | None,
) -> list[PassiveEffectChange]:
    """Resolve the ``@attributes.movement.*`` references among ``changes``.

    A reference to a speed the creature does not have is DROPPED rather than
    inventing one.
    """
    if not any(_MOVEMENT_TOKEN_RE.match(str(c.value)) for c in changes):
        return list(changes)
    resolved: list[PassiveEffectChange] = []
    for change in changes:
        match = _MOVEMENT_TOKEN_RE.match(str(change.value))
        if match is None:
            resolved.append(change)
            continue
        mode = match.group(1)
        source = _movement_reference_speed(mode, target, ctx)
        if source:
            resolved.append(change.model_copy(update={"value": str(source)}))
        else:
            _LOGGER.info(
                "movement_reference_unset mode=%s target_id=%s", mode, target.entity_id
            )
    return resolved


def _resolve_movement_references(
    pe: PassiveEffect,
    target: Combatant,
    ctx: ActivityResolutionContext | None = None,
) -> PassiveEffect:
    """Replace ``@attributes.movement.<mode>`` values with the target's own rating.

    The corpus writes "your flying speed equals your walking Speed" as
    ``system.attributes.movement.fly = "@attributes.movement.walk"`` (Potion of
    Flying). The formula layer treats an unknown token as a hard error, which
    made that potion raise at play time. Resolving the reference keeps the
    formula layer numeric.
    """
    # "a Swim Speed equal to your Speed" states the sentence as the bare
    # value "1" in the source, which is a mark for "your Speed", not a
    # distance. Which modes that mark belongs to is the caller's decision,
    # because only the caller knows which mode the sentence sits on.
    ratio_modes = ctx is not None and pe.id in ctx.speed_ratio_effects
    changes = pe.changes
    if ratio_modes and all(str(c.value).strip() == "1" for c in changes):
        speed = _movement_reference_speed("walk", target, ctx)
        if speed:
            changes = [
                c.model_copy(update={"value": str(speed)}) for c in changes
            ]
    resolved = _movement_changes_resolved(changes, target, ctx)
    if len(resolved) == len(pe.changes) and all(
        a is b for a, b in zip(resolved, pe.changes, strict=True)
    ):
        return pe
    return pe.model_copy(update={"changes": resolved})




def _single_unnamed_effect_fallback(
    activity: _ActivityBaseWithEffects,
    by_id: Mapping[str, PassiveEffect],
    *,
    ref_id: str,
) -> PassiveEffect | None:
    """The one effect definition a single dangling ref must mean, or ``None``.

    The bundled data has exactly one entry where an activity refs an id its
    entity does not define: SRD 5.2 Net (``canonical/items/net.json``) refs
    ``cMQAYD7OnCQK6Ux9`` on its save activity while the item's single effect
    definition is ``67ofG0FVqrrkAHKa`` ("Caught in Net", ``restrained``).
    The book text is unambiguous — a failed save restrains the target — and
    the item carries exactly one effect definition for its exactly one
    activity ref, so the intended pairing cannot be ambiguous.

    Deliberately narrow, because a guess would be worse than a warning:
    this fires only when the activity has exactly ONE ref, the entity defines
    exactly ONE effect and this one was not already claimed by another ref
    on a different activity, and the definition carries no activity rider of
    its own. Anything else keeps the ``effect_ref_unresolved`` warning.
    """
    refs = list(getattr(activity, "effects", ()) or ())
    if len(refs) != 1 or getattr(refs[0], "id", None) != ref_id:
        return None
    if len(by_id) != 1:
        return None
    (only,) = by_id.values()
    source_activity = _effect_source_activity(only)
    if source_activity is not None and source_activity != getattr(activity, "id", None):
        return None
    return only


def _effect_source_activity(effect: PassiveEffect) -> str | None:
    """The activity an effect definition declares it came from, if any."""
    flags = getattr(effect, "flags", None)
    if not isinstance(flags, Mapping):
        return None
    value = flags.get("dnd5e", {}) if "dnd5e" in flags else flags
    if not isinstance(value, Mapping):
        return None
    activity = value.get("activity")
    return activity if isinstance(activity, str) and activity else None

def apply_activity_effects(
    activity: _ActivityBaseWithEffects,
    ctx: ActivityResolutionContext,
    target: Combatant,
    *,
    save_succeeded: bool | None,
    cast_level: int,
    extra_flags: Mapping[str, Any] | None = None,
) -> None:
    """Apply an activity's effect riders to ``target``, emitting events.

    Each ``AppliedEffectRef`` on the activity points (by ``ref.id``) at a
    ``PassiveEffect`` definition the parent entity carries (``ctx.source_passive_
    effects``). For every ref that resolves and passes the level + on_save gates,
    one ``EffectApplied`` is emitted (the translated runtime ``ActiveEffect``),
    followed by one ``ConditionApplied`` per status that names a valid SRD
    condition.

    The EffectApplied-then-ConditionApplied emit order is load-bearing: the
    orchestrator pairs each condition to its effect by emit order.

    ``save_succeeded`` is the target's save outcome for save activities (``None``
    for non-save kinds, which apply unconditionally). ``cast_level`` is the slot
    level the activity was cast at, gated against each ref's ``level`` block.
    ``extra_flags`` are merged into every built effect's ``flags``.

    Mirrors (does not import) ``effects/ieffect2._apply_effect``'s emit shape.
    """
    by_id: dict[str, PassiveEffect] = {pe.id: pe for pe in ctx.source_passive_effects}

    for ref in activity.effects:
        pe = by_id.get(ref.id)
        if pe is None:
            pe = _single_unnamed_effect_fallback(activity, by_id, ref_id=ref.id)
        if pe is None:
            _LOGGER.warning(
                "effect_ref_unresolved ref_id=%s activity=%s",
                ref.id,
                activity.id,
            )
            continue
        # Level gate: the ref applies only within [min, max] (either may be
        # unset). cast_level outside the band → this ref doesn't fire at this
        # slot level.
        if ref.level.min is not None and cast_level < ref.level.min:
            continue
        if ref.level.max is not None and cast_level > ref.level.max:
            continue

        # On-save gate (save activities only). Foundry per-effect semantics:
        # on_save=True applies even on a successful save; False/None applies
        # only on a FAILED save. Non-save activities (save_succeeded is None)
        # apply unconditionally.
        if save_succeeded is True and not ref.on_save:
            continue

        # SRD 5.2 Potion of Flying: the corpus states its fly speed as a
        # REFERENCE -- ``@attributes.movement.walk``, "your flying speed
        # equals your walking Speed" -- not a number. The formula layer treats
        # an unknown token as a hard error, which turned the potion into a
        # ValueError at play time. The reference has the same meaning for this
        # purpose: the wearer's own walking Speed.
        pe = _resolve_movement_references(pe, target, ctx)
        # SRD 5.2 Boots of Speed: "If you click your heels together again, you
        # end the effect." The orchestrator toggles such an effect OFF when the
        # same item is activated again, which it can only do if the effect says
        # which ITEM it came from -- a spell's effect must keep stacking with
        # another cast, an item's must switch. Stamped here, where the source
        # item is known; spells carry no item_id and stay additive.
        item_flags = {"item": ctx.item_id} if ctx.item_id else None
        merged_flags = (
            {**(extra_flags or {}), **item_flags}
            if extra_flags and item_flags
            else (item_flags or extra_flags)
        )
        ae = passive_effect_to_active_effect(
            pe,
            target_id=target.entity_id,
            caster_id=ctx.caster.entity_id,
            concentration=ctx.concentration,
            ctx=ctx,
            extra_flags=merged_flags,
        )
        ctx.event_emitter(EffectApplied(effect=ae))

        # Conditions land AFTER the EffectApplied (load-bearing order), in a
        # deterministic (sorted) sequence. A status that isn't a valid SRD
        # ConditionType still rides on ae.statuses; only ConditionApplied is
        # gated on the mapping.
        for status in sorted(pe.statuses):
            if status in _CONDITION_VALUES:
                # condition-immunity gate (``is_condition_immune``, shared with
                # ``mastery.py``'s Topple rider). A target immune to this
                # condition (Nature's Ward → "poisoned") never has it attach —
                # the ConditionApplied is SUPPRESSED outright (the EffectApplied
                # rider above still fired narratively). Suppress, not
                # emit-and-neutralize: a condition is binary present/absent with
                # no amount to zero, so a ConditionApplied the engine treats as
                # not-applied would mislead every condition-tick reader.
                # ``target`` is the creature as this activity began, so a
                # Petrified an earlier rider landed doesn't shield a later
                # rider's Poisoned; no activity carries both riders.
                if is_condition_immune(target, status, imposed=pe.statuses):
                    _LOGGER.info(
                        "condition_immune_suppressed status=%s target_id=%s",
                        status,
                        target.entity_id,
                    )
                    continue
                ctx.event_emitter(
                    ConditionApplied(
                        target_id=target.entity_id,
                        condition=typing.cast("ConditionType", status),
                    )
                )
            else:
                _LOGGER.info("effect_status_unmapped status=%s", status)
