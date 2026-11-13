"""Standalone check resolver — ``resolve_check``.

Resolves a skill check, ability check, or saving throw with no combat handle
required (out-of-combat skill prompts, poison ticks, environmental hazards).
Each check honours the ``active_effects`` you pass, so Bless / Guidance / Bane
land uniformly here and in combat.

Determinism
-----------
Set ``rng`` to a seeded ``random.Random`` for a reproducible
result. If you leave it ``None``, the d20 is drawn from the process-global
``random`` module and the check is reproducible only if you seed that yourself.
In-combat rolls are unaffected: they always use the generator threaded from
``start_combat(rng_seed=...)``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from dnd5e_engine.rules.character import ABILITY_NAME_BY_CODE
from dnd5e_engine.rules.effects import apply_changes_to_check
from dnd5e_engine.rules.skills import (
    SKILL_ABILITIES,
    ability_check,
    saving_throw,
    skill_check,
)
from dnd5e_engine.types.effects import ActiveEffect

CheckKind = Literal["skill", "ability", "saving_throw"]


@dataclass(frozen=True)
class CheckSpec:
    """All inputs needed to resolve a standalone check.

    Self-contained: the resolver makes zero I/O calls and reads nothing
    outside the spec. ``active_effects`` are **pre-filtered by the caller** —
    the engine does not decide which of a creature's effects apply to this
    check. The resolver folds Foundry-shaped
    `ActiveEffectChange` entries whose `key` matches the check's bucket
    (`check.bonus` for skill+ability, `save.bonus` for saving_throw).
    Override-mode changes on `flags.advantage.*` / `flags.disadvantage.*`
    keys surface in `effect_breakdown` for narrator visibility but do NOT
    toggle the roll mechanic — pass `advantage=True` on the spec for that.
    """

    kind: CheckKind
    ability_scores: dict[str, int]
    proficient_skills: tuple[str, ...]
    proficient_saves: tuple[str, ...]
    proficiency_bonus: int

    # `skill` populated when kind == "skill"; ignored otherwise.
    skill: str | None = None
    # `ability` populated when kind in {"ability", "saving_throw"}; for
    # "skill" the ability is derived from SKILL_ABILITIES.
    ability: str | None = None

    dc: int | None = None
    advantage: bool = False
    disadvantage: bool = False
    # Underscore-slug skill names that get ×2 proficiency (already-proficient
    # skills only; skill_check enforces the is_proficient gate). Empty default
    # keeps every existing caller behaviour-neutral until populated downstream.
    expertise_skills: tuple[str, ...] = ()
    # SRD 5.2 Jack of All Trades: "You can add half your Proficiency Bonus
    # (round down) to any ability check you make that uses a skill proficiency
    # you lack and that doesn't otherwise use your Proficiency Bonus" — skill
    # checks only.
    jack_of_all_trades: bool = False
    # SRD 5.2 Reliable Talent: "Whenever you make an ability check that uses one
    # of your skill or tool proficiencies, you can treat a d20 roll of 9 or lower
    # as a 10" — this spec models skills, not tools.
    reliable_talent: bool = False
    active_effects: tuple[ActiveEffect, ...] = field(default_factory=tuple)
    # Seeded generator for a reproducible roll. ``None`` draws from the
    # process-global ``random`` module (reproducible only if the caller seeds
    # it). Excluded from equality/repr so two specs comparing equal still
    # describe the same check.
    rng: random.Random | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Validate the spec at construction, not mid-roll. (SOLO-PATCH)

        The resolver used to raise ``ValueError`` from the middle of the
        calculation when ``kind="ability"`` arrived without an ability,
        which aborted a whole game turn. Two things change here:

        * ``kind="skill"`` with no ``ability`` gets its ability derived from
          ``SKILL_ABILITIES`` (the resolver already resolved the skill that
          way for the modifier; this makes the spec agree with the roll).
        * Every other incoherent combination is rejected **here**, on the
          caller's frame, so a host author sees a data error when the check
          is built rather than a dead turn during play.

        Derivation is deliberately additive: an explicit ``ability`` that
        contradicts the skill's ability is left alone (the host may model a
        non-standard skill), only a missing one is filled in.
        """
        if self.kind == "skill":
            if not self.skill:
                raise ValueError("CheckSpec.kind='skill' requires `skill`")
            if not self.ability:
                normalized = self.skill.lower().replace(" ", "_")
                derived = SKILL_ABILITIES.get(normalized)
                if derived:
                    object.__setattr__(self, "ability", derived)
            return
        if self.kind in ("ability", "saving_throw") and not self.ability:
            raise ValueError(
                f"CheckSpec.kind={self.kind!r} requires `ability`; "
                "set it when building the check (the resolver does not guess)"
            )
        self._normalize_ability()

    def _normalize_ability(self) -> None:
        """Accept ``"str"`` as well as ``"strength"``. (SOLO-PATCH)

        Hosts speak the short code (``SceneCheck.ability`` is
        ``Literal["str", "dex", ...]``) while the engine's sheet is keyed by
        the full name (``derived_sheet.ability_scores`` is
        ``{"strength": 15, ...}``). Passing ``"wis"`` straight through made
        ``ability_scores.get("wis", 10)`` miss and score an unmodified 10 —
        a silent +0 instead of +3. A typo is not silently repaired: an
        unknown code raises, because guessing a score is worse than failing.
        """
        ability = self.ability
        if not ability:
            return
        key = ability.lower()
        full = ABILITY_NAME_BY_CODE.get(key)
        if full is None:
            if key in ABILITY_NAME_BY_CODE.values():
                full = key
            else:
                raise ValueError(
                    f"CheckSpec.ability={ability!r} is not a D&D ability; "
                    f"expected one of {sorted(ABILITY_NAME_BY_CODE)} "
                    f"or {sorted(ABILITY_NAME_BY_CODE.values())}"
                )
        object.__setattr__(self, "ability", full)


@dataclass(frozen=True)
class CheckResult:
    """Result fragment for a standalone check.

    A plain value type: everything a host needs to render or adjudicate the
    roll, with no engine state attached.
    """

    kind: CheckKind
    skill: str  # empty for ability / saving_throw
    ability: str
    roll_total: int
    modifier: int
    dc: int | None
    success: bool | None
    natural_roll: int
    is_proficient: bool
    # Narrator-facing breakdown strings produced by apply_changes_to_check,
    # e.g. ["effect(1d4:3)", "effect(advantage)"]. Empty when no effects fired.
    effect_breakdown: tuple[str, ...] = field(default_factory=tuple)


# Foundry-shaped `ActiveEffectChange.key` strings. Per D&D 5e, skill
# checks ARE ability checks (with prof bonus). The resolver folds
# changes from BOTH the generic bucket (e.g. `check.bonus`) AND the
# kind-specific bucket (e.g. `check.skill_check.bonus`). This lets the
# common seed templates (Bless, Guidance) ship the generic key while
# more targeted effects (e.g. a Charisma-only save buff via
# `save.charisma.bonus`, or a skill-check-only buff via
# `check.skill_check.bonus`) still land.
_KIND_TO_BUCKETS: dict[CheckKind, tuple[str, ...]] = {
    "skill": ("check.bonus", "check.skill_check.bonus"),
    "ability": ("check.bonus", "check.ability_check.bonus"),
    # save: generic save.bonus, kind-specific save.saving_throw.bonus
    # (for completeness), and the per-ability save.<ability>.bonus
    # synthesized at resolve_check call time below.
    "saving_throw": ("save.bonus", "save.saving_throw.bonus"),
}


def resolve_check(spec: CheckSpec) -> CheckResult:
    """Resolve a standalone skill / ability / saving-throw check.

    Active effects are pre-filtered by the caller. The resolver folds
    Foundry-shaped
    `ActiveEffectChange` entries whose `key` matches the kind's bucket
    (`check.bonus` for skill+ability, `save.bonus` for saving_throw).
    Override-mode changes on `flags.advantage.*` / `flags.disadvantage.*`
    contribute to `effect_breakdown` only; the roll mechanic's advantage
    is set via `spec.advantage`.

    Zero I/O. Pass ``spec.rng`` for a reproducible roll — see the module
    docstring for the determinism contract.
    """
    buckets: tuple[str, ...] = _KIND_TO_BUCKETS[spec.kind]
    # Saving throws additionally honor the per-ability bucket
    # (e.g. `save.wisdom.bonus`) — Cloak of Protection vs. Ring of
    # Mind Shielding semantic.
    if spec.kind == "saving_throw" and spec.ability:
        buckets = (*buckets, f"save.{spec.ability.lower()}.bonus")

    # Derive advantage / disadvantage
    # from ``flags.advantage.*`` / ``flags.disadvantage.*`` override
    # changes on active_effects. The ieffect2 translator emits effects
    # like frightened/blinded as flag changes (per-bucket or per-ability),
    # so a frightened actor on a SKILL_CHECK / FLEE should roll at
    # disadvantage. The check kind's relevant flag-bucket roots:
    flag_roots: tuple[str, ...] = ()
    if spec.kind == "skill" or spec.kind == "ability":
        flag_roots = ("flags.advantage.check", "flags.disadvantage.check")
    elif spec.kind == "saving_throw":
        flag_roots = ("flags.advantage.save", "flags.disadvantage.save")
    flag_advantage = False
    flag_disadvantage = False
    # for skill checks, derive the
    # relevant ability from the skill so an ability-tagged
    # ``flags.disadvantage.check.strength`` does not silently penalize a
    # Perception (Wisdom) check. For ability checks the spec carries the
    # ability directly; for saves it's the save ability.
    if spec.kind == "skill" and spec.skill:
        normalized_skill_for_flag = spec.skill.lower().replace(" ", "_")
        ability_suffix = SKILL_ABILITIES.get(normalized_skill_for_flag, "").lower()
    else:
        ability_suffix = (spec.ability or "").lower() if spec.ability else ""
    for eff in spec.active_effects:
        for ch in eff.changes:
            if ch.mode != "override" or ch.value is not True:
                continue
            for root in flag_roots:
                # Accept the broad form ("flags.advantage.check") and the
                # ability-qualified form ("flags.advantage.check.strength"
                # / "flags.advantage.save.wisdom"). Per-ability filter only
                # admits a flag tagged for the active ability (Wisdom save
                # is not disadvantaged by frightened, which targets only
                # checks; a Strength save isn't disadvantaged by a
                # disadvantage tag on Dex saves).
                if ch.key == root:
                    if "advantage" in root and "disadvantage" not in root:
                        flag_advantage = True
                    else:
                        flag_disadvantage = True
                    break
                prefix = f"{root}."
                if ch.key.startswith(prefix) and (
                    not ability_suffix or ch.key[len(prefix) :] == ability_suffix
                ):
                    if "advantage" in root and "disadvantage" not in root:
                        flag_advantage = True
                    else:
                        flag_disadvantage = True
                    break
    effective_advantage = spec.advantage or flag_advantage
    effective_disadvantage = spec.disadvantage or flag_disadvantage

    if spec.kind == "skill":
        base = skill_check(
            skill=spec.skill,
            ability_scores=spec.ability_scores,
            proficient_skills=list(spec.proficient_skills),
            proficiency_bonus=spec.proficiency_bonus,
            dc=spec.dc,
            advantage=effective_advantage,
            disadvantage=effective_disadvantage,
            expertise=spec.skill.lower().replace(" ", "_") in spec.expertise_skills,
            jack_of_all_trades=spec.jack_of_all_trades,
            rng=spec.rng,
            reliable_talent=spec.reliable_talent,
        )
        normalized_skill = spec.skill.lower().replace(" ", "_")
        ability_used = SKILL_ABILITIES.get(normalized_skill, "intelligence")
    elif spec.kind == "ability":
        base = ability_check(
            ability=spec.ability,
            ability_scores=spec.ability_scores,
            dc=spec.dc,
            advantage=effective_advantage,
            disadvantage=effective_disadvantage,
            rng=spec.rng,
        )
        ability_used = spec.ability.lower()
    elif spec.kind == "saving_throw":
        base = saving_throw(
            ability=spec.ability,
            ability_scores=spec.ability_scores,
            proficient_saves=list(spec.proficient_saves),
            proficiency_bonus=spec.proficiency_bonus,
            dc=spec.dc,
            advantage=effective_advantage,
            disadvantage=effective_disadvantage,
            rng=spec.rng,
        )
        ability_used = spec.ability.lower()
    else:  # pragma: no cover -- exhaustively typed by CheckKind
        raise ValueError(f"unknown CheckKind: {spec.kind!r}")

    # Apply each bucket in order, threading the running total. apply_changes_to_check
    # is additive per call; consecutive calls let multiple bucket keys
    # land on the same check.
    modified_total = base.roll.total
    breakdown: list[str] = []
    for bucket in buckets:
        modified_total, bucket_breakdown = apply_changes_to_check(
            base_total=modified_total,
            bucket=bucket,
            effects=spec.active_effects,
            rng=spec.rng,
        )
        breakdown.extend(bucket_breakdown)
    success = (modified_total >= spec.dc) if spec.dc is not None else None
    # The kept die — under advantage/disadvantage ``dice[0]`` need not be it.
    natural_roll = base.roll.total - base.roll.modifier

    return CheckResult(
        kind=spec.kind,
        skill=base.skill,
        ability=ability_used,
        roll_total=modified_total,
        # Invariant: roll_total = natural_roll + modifier. Effect dice
        # contributions are folded into the reported modifier so the
        # `DiceOutcome` contract (`roll_total = die + modifier`) holds
        # downstream. The breakdown of individual effect contributions
        # remains in `effect_breakdown` for narrator visibility.
        modifier=modified_total - natural_roll,
        dc=spec.dc,
        success=success,
        natural_roll=natural_roll,
        is_proficient=base.is_proficient,
        effect_breakdown=tuple(breakdown),
    )


__all__ = [
    "CheckKind",
    "CheckResult",
    "CheckSpec",
    "resolve_check",
]
