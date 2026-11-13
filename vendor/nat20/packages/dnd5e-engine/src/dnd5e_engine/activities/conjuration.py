"""Conjurations: the allowlisted summon, enchant and transform sources (C21).

A few SRD 5.2 spells and features resolve by making something. Spiritual Weapon
makes a construct: "You create a floating, spectral force that resembles a
weapon of your choice". Summon Dragon makes a creature that joins the
initiative order: "It manifests in an unoccupied space that you can see within
range and uses the Draconic Spirit stat block". Magic Weapon enchants a weapon:
"that weapon becomes a magic weapon". Wild Shape, and Polymorph on a failed
save, put a creature in a Beast's stat block: "Your game statistics are
replaced by the Beast's stat block". The dataset carries their Foundry
``summon`` / ``enchant`` / ``transform`` activities, but no model the engine
can resolve generically, so the engine keeps a typed registry keyed by source
slug: a Python registry now, a dataset field later, as conditions and traits
began. Every other summon, enchant and transform activity stays
narrative.

The resolver routes an allowlisted activity only when the orchestrator hands it
a pre-validated ``ConjurationCarrier``, and reports what the orchestrator must
fold after resolution through typed requests. Pure data: no loader, no
orchestrator import, no I/O.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType
from typing import Final, Literal

from dnd5e_srd_data.schema.common import (
    AttackActivity,
    AttackBlock,
    AttackDamageBlock,
    AttackTypeBlock,
    DamagePart,
    DamagePartBlock,
    DamageScalingBlock,
    SummonBonusesBlock,
    SummonMatchBlock,
)
from dnd5e_srd_data.schema.item import Weapon
from dnd5e_srd_data.schema.monster import Monster

from dnd5e_engine.events import DamageType
from dnd5e_engine.types.effects import ActiveEffect

ConjurationKind = Literal["construct", "enchant", "transform", "transform_rider", "summon"]
TransformSource = Literal["wild-shape", "polymorph"]

# The only sources the resolver routes. Polymorph is a ``save`` activity whose
# failed save carries the transform (``"transform_rider"``), so its routing is
# the orchestrator's. Summon Dragon is the one creature summon (``SUMMONS``);
# the other summon spells, the monster summon riders, the item summons, Sacred
# Weapon and the other Polymorph-family spells are absent and stay narrative.
CONJURATION_ALLOWLIST: Final[Mapping[str, ConjurationKind]] = MappingProxyType(
    {
        "spiritual-weapon": "construct",
        "magic-weapon": "enchant",
        "wild-shape": "transform",
        "polymorph": "transform_rider",
        "summon-dragon": "summon",
        "find-steed": "summon",
        "animate-dead": "summon",
        "giant-insect": "summon",
        "create-undead": "summon",
        "shillelagh": "enchant",
        "find-familiar": "summon",
        # SRD 5.2 Figurine of Wondrous Power: each figurine summons its own
        # creature (Giant Goat, warhorse, mule, elephant). The item carries
        # four SummonActivity entries, so the source is the ITEM, not a spell.
        "ivory-goats": "summon",
    }
)

#: ``ActiveEffect.flags`` key naming the weapon slug an enchantment rides on
#: (Magic Weapon: "You touch a nonmagical weapon"). A host may seed an
#: enchantment through ``start_combat(active_effects=)`` with it.
ENCHANTED_WEAPON_FLAG: Final = "enchanted_weapon"
#: ``ActiveEffect.flags`` key naming the Beast form a transform effect holds.
TRANSFORM_FORM_FLAG: Final = "transform_form"


@dataclass(frozen=True)
class ConjurationCarrier:
    """The orchestrator's pre-validated inputs for one resolution of an
    allowlisted source: the weapon an enchantment touches, the Beast form a
    transform takes, the cell a construct appears in. ``None`` fields leave the
    matching activity narrative."""

    source_slug: str
    weapon_slug: str | None = None
    form_slug: str | None = None
    cell: str | None = None


@dataclass(frozen=True)
class ConstructRequest:
    """A construct the orchestrator registers after resolution: its spell, its
    owner, its cell, the level it was cast at, and the creatures its immediate
    attack may target."""

    spell_id: str
    owner_id: str
    cell: str
    slot_level: int
    target_ids: tuple[str, ...]


@dataclass(frozen=True)
class TransformRequest:
    """A creature the orchestrator shape-shifts after resolution."""

    target_id: str
    form_slug: str
    source: TransformSource


# Foundry item-change keys an enchantment writes onto its weapon. Magic Weapon's
# riders carry exactly these two plus a cosmetic ``name`` override.
_MAGICAL_BONUS_KEY: Final = "system.magicalBonus"
_PROPERTIES_KEY: Final = "system.properties"
_MAGICAL_PROPERTY: Final = "mgc"

#: Shillelagh rewrites the weapon itself rather than blessing it: "the
#: weapon's damage die becomes a d8" and "you can use your spellcasting
#: ability instead of Strength". Its riders name the change under the same
#: Foundry paths the item sheet uses, so the enchantment needs no new
#: vocabulary — only more keys than Magic Weapon carries:
#: ``system.damage.base.denomination`` overrides the die size,
#: ``system.damage.base.types`` its type, and
#: ``activities[attack].attack.bonus`` the attack-roll bonus.
_DENOM_KEY: Final = "system.damage.base.denomination"
_DAMAGE_TYPES_KEY: Final = "system.damage.base.types"
_ATTACK_BONUS_KEY: Final = "activities[attack].attack.bonus"
_ATTACK_ABILITY_KEY: Final = "activities[attack].attack.ability"
#: The two ways a rider says "fight with the spellcasting ability":
#: Shillelagh writes ``none`` (drop the weapon's own), True Strike writes
#: the literal ``spellcasting``.
_SPELLCASTING_ABILITY_SIGNALS: Final = frozenset({"none", "spellcasting"})


def _with_die(part: DamagePart, die: str) -> DamagePart:
    """``part`` with its die size replaced by ``die``, keeping the count.

    SRD 5.2 Shillelagh: "the weapon's damage die becomes a d8". A part
    written ``2d6`` keeps its two dice and lands on ``2d8``: the spell
    changes the die, not how many are rolled.
    """
    count = part.dice.split("d")[0] if "d" in part.dice else "1"
    return part.model_copy(update={"dice": f"{count or 1}d{die}"})


def enchant_ability_override(effects: Sequence[ActiveEffect], slug: str) -> str | None:
    """The ability an enchantment makes ``slug`` attack with instead of STR,
    or ``None`` when no effect does.

    SRD 5.2 Shillelagh: "you can use your spellcasting ability instead of
    Strength for the attack and damage rolls of melee attacks using that
    weapon". Its rider writes that as ``activities[attack].attack.ability``
    ``override`` ``"none"`` (drop Strength) plus an ``add`` draft of
    ``max(@abilities.str.mod, @attributes.spell.mod)``. The engine reads the
    ``none`` as the signal that the weapon fights with the caster's
    spellcasting ability: the explicit "instead of Strength" of the text.
    """
    for effect in effects:
        if effect.flags.get(ENCHANTED_WEAPON_FLAG) != slug:
            continue
        for change in effect.changes:
            if change.key == _ATTACK_ABILITY_KEY and str(change.value) in _SPELLCASTING_ABILITY_SIGNALS:
                return "spellcasting"
    return None


def enchant_weapon(weapon: Weapon, effects: Sequence[ActiveEffect]) -> Weapon:
    """``weapon`` as the enchantments among ``effects`` make it.

    SRD 5.2 Magic Weapon: "that weapon becomes a magic weapon with a +1 bonus
    to attack rolls and damage rolls." Only an effect whose
    ``flags[ENCHANTED_WEAPON_FLAG]`` names this weapon's slug applies: an
    ``upgrade`` of ``system.magicalBonus`` raises ``magical_bonus`` to at least
    its value (a bonus never stacks with another), and an ``add`` of the
    ``mgc`` property makes the weapon magical, which also overcomes resistance
    to nonmagical damage. Every other change key (the ``name`` override) is
    ignored. Returns ``weapon`` itself when nothing applies.
    """
    bonus, magical = weapon.magical_bonus, weapon.magical
    # The die and the damage type Shillelagh rewrites; ``None`` keeps the
    # weapon's own values.
    die: str | None = None
    attack_bonus_expr: str | None = None
    for effect in effects:
        if effect.flags.get(ENCHANTED_WEAPON_FLAG) != weapon.slug:
            continue
        for change in effect.changes:
            if change.key == _MAGICAL_BONUS_KEY and change.mode == "upgrade":
                bonus = max(bonus, int(change.value))
            elif (
                change.key == _PROPERTIES_KEY
                and change.mode == "add"
                and change.value == _MAGICAL_PROPERTY
            ):
                magical = True
            elif change.key == _DENOM_KEY and change.mode == "override":
                die = str(change.value)
            elif change.key == _ATTACK_BONUS_KEY and change.mode == "add":
                # A formula, not a number: Shillelagh adds
                # "max(@abilities.str.mod, @attributes.spell.mod)". The
                # ability itself is handled by enchant_ability_override;
                # the bonus is what the attack roll adds on top.
                attack_bonus_expr = str(change.value)
    update: dict[str, object] = {}
    if (bonus, magical) != (weapon.magical_bonus, weapon.magical):
        update["magical_bonus"] = bonus
        update["magical"] = magical
    if die is not None:
        # The die replaces every part's denomination, the type every part's
        # type: "the weapon's damage die becomes a d8 ... it can be Force
        # damage or the weapon's normal damage type" speaks of the weapon,
        # not of one of its parts.
        parts: list[DamagePart] = []
        for part in weapon.damage_parts:
            piece = part
            if die is not None:
                piece = _with_die(piece, die)
            parts.append(piece)
        update["damage_parts"] = tuple(parts)
    if not update:
        return weapon
    result = weapon.model_copy(update=update)
    if attack_bonus_expr is not None:
        # Carried on the weapon so the attack resolver can add it; the rider
        # is the enchantment's, not the weapon item's own field.
        object.__setattr__(result, "_enchant_attack_bonus_expr", attack_bonus_expr)
    return result


@dataclass(frozen=True)
class ConstructSpec:
    """A caster-owned spell construct's fixed numbers: how far it reaches and
    moves, and the damage its attack deals at the spell's base level."""

    reach_ft: int
    move_ft: int
    damage_die: int
    damage_type: DamageType
    base_level: int


# SRD 5.2 Spiritual Weapon: "you can immediately make one melee spell attack
# against one creature within 5 feet of the force. On a hit, the target takes
# Force damage equal to 1d8 plus your spellcasting ability modifier. As a Bonus
# Action on your later turns, you can move the force up to 20 feet and repeat
# the attack against a creature within 5 feet of it." The Foundry actor that
# carries this attack upstream is a quarantined CC-BY conjuration, so the
# registry holds the numbers.
CONSTRUCTS: Final[Mapping[str, ConstructSpec]] = MappingProxyType(
    {
        "spiritual-weapon": ConstructSpec(
            reach_ft=5, move_ft=20, damage_die=8, damage_type="force", base_level=2
        ),
    }
)


def construct_attack_activity(
    spec: ConstructSpec, *, slot_level: int, attack_bonus: int, damage_bonus: int
) -> AttackActivity:
    """The construct's attack as a flat melee spell attack: ``attack_bonus`` to
    hit and one damage part of ``spec.damage_die`` dice plus ``damage_bonus``.

    SRD 5.2 Spiritual Weapon: "The damage increases by 1d8 for every slot
    level above 2." The dice count carries the slot level, so the part's own
    upcast scaling is zeroed: the resolution context also carries the slot
    level (its spell level is what makes the hit magical) and would otherwise
    add the same dice again. No ``@``-token: the caller resolves both numbers.
    """
    return AttackActivity(
        id="construct-attack",
        name="Construct attack",
        attack=AttackBlock(
            bonus=str(attack_bonus),
            flat=True,
            type=AttackTypeBlock(value="melee", classification="spell"),
        ),
        damage=AttackDamageBlock(
            include_base=False,
            parts=[
                DamagePartBlock(
                    number=1 + slot_level - spec.base_level,
                    denomination=spec.damage_die,
                    bonus=str(damage_bonus),
                    types=[spec.damage_type],
                    scaling=DamageScalingBlock(number=0),
                )
            ],
        ),
    )


@dataclass(frozen=True)
class StatBlockMagnitudes:
    """The numbers a creature acting from a stat block rolls with: its six
    ability scores (``"str"`` … ``"cha"``) and its Proficiency Bonus. On a
    resolution context they replace the entity-type approximations (a
    Monster's uniform ``attack_bonus`` model), so a governing ability, ``@mod``
    and ``@prof`` read the stat block. SRD 5.2 Wild Shape: "Your game
    statistics are replaced by the Beast's stat block".

    A summoned creature also carries its summoner's numbers (SRD 5.2 Draconic
    Spirit, Rend: "Bonus equals your spell attack modifier"; "1d6 + 4 + the
    spell's level Piercing damage"): ``attack_bonus`` is a flat to-hit that
    replaces ability modifier + Proficiency Bonus, and ``attack_damage_bonus``
    is added to each attack's damage. A transformed creature sets neither."""

    ability_scores: Mapping[str, int]
    proficiency_bonus: int
    attack_bonus: int | None = None
    attack_damage_bonus: int = 0


@dataclass(frozen=True)
class WildShapeTier:
    """One row of SRD 5.2's Beast Shapes table: from Druid level ``min_level``,
    a Beast form of Challenge Rating up to ``max_cr``, with a Fly Speed only
    when ``fly_allowed``."""

    min_level: int
    max_cr: float
    fly_allowed: bool


# SRD 5.2 Wild Shape, Beast Shapes table — Druid level 2: Max CR 1/4, no Fly
# Speed; level 4: 1/2, no Fly Speed; level 8: 1, Fly Speed.
WILD_SHAPE_TIERS: Final[tuple[WildShapeTier, ...]] = (
    WildShapeTier(min_level=2, max_cr=0.25, fly_allowed=False),
    WildShapeTier(min_level=4, max_cr=0.5, fly_allowed=False),
    WildShapeTier(min_level=8, max_cr=1.0, fly_allowed=True),
)


def wild_shape_tier(druid_level: int) -> WildShapeTier | None:
    """The Beast Shapes row a Druid of ``druid_level`` uses; ``None`` below
    level 2, where there is no Wild Shape."""
    rows = [tier for tier in WILD_SHAPE_TIERS if tier.min_level <= druid_level]
    return rows[-1] if rows else None


# The roll data a summon stat block reads from the spell that summons it
# (``@flags.dnd5e.summon.level`` / ``.mod``).
_SUMMON_ROLL_DATA: Final = "@flags.dnd5e.summon"


def uses_summon_roll_data(monster: Monster) -> bool:
    """True for a summon stat block such as Giant Insect: its formulas read the
    summoning spell's level and its caster's modifier, so its numbers are
    undefined without a summoner and it is never a Wild Shape or Polymorph
    form."""
    return _SUMMON_ROLL_DATA in monster.model_dump_json()


@dataclass(frozen=True)
class TransformRider:
    """A spell whose shape-shift rides its own saving throw (the
    ``FORCED_MOVEMENT_RIDERS`` pattern): on a ``failed_save`` the target takes
    the cast's Beast form as ``source``."""

    trigger: Literal["failed_save"]
    source: TransformSource


# SRD 5.2 Polymorph: "The target must succeed on a Wisdom saving throw or
# shape-shift into a Beast form for the duration."
#: The condition a spell's OWN TEXT names as the consequence of a failed save,
#: for the handful of spells whose shipped activity carries the save but no
#: ``effects`` / ``riders`` for it. Read from the corpus description, not from
#: memory: Grease ("must succeed on a Dexterity saving throw or have the
#: Prone condition"), Sleet Storm ("... or have the Prone condition"),
#: Telekinesis ("... is Restrained"), Earthquake ("... knocked Prone").
#:
#: These are NOT applied by the engine: the data does not say when or for how
#: long, and inventing a duration from prose would be a guess (§2). The
#: orchestrator uses this table only to ASK the DM (§8) instead of reporting a
#: save whose failure changed nothing (§7). A spell whose text names no
#: condition in machine-readable form is absent — that is the honest state, not
#: an omission.
SAVE_CONSEQUENCES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "grease": ("prone",),
        "sleet-storm": ("prone",),
        "telekinesis": ("restrained",),
        "earthquake": ("prone",),
    }
)


TRANSFORM_RIDERS: Final[Mapping[str, TransformRider]] = MappingProxyType(
    {"polymorph": TransformRider(trigger="failed_save", source="polymorph")}
)


@dataclass(frozen=True)
class SummonSpec:
    """The stat block a creature-summoning spell seats.

    ``stat_block_slug`` is the only form a spell offers (Summon Dragon).
    ``forms`` is the open choice a spell puts to the player instead (Find
    Steed: "a Celestial, a Fey, or a Fiend"), keyed by the name the player
    names in ``PlayerIntent.form_id``; the spell seats whichever form was
    chosen and nothing when none was.
    """

    stat_block_slug: str | None = None
    forms: Mapping[str, str] = field(default_factory=dict)
    #: How many creatures one cast seats, read from the activity's
    #: ``target.affects.count`` formula. SRD 5.2 Animate Dead:
    #: ``"1 + @scaling.increase * 2"`` — one body, plus two per level.
    count_formula: str = ""
    #: Per-form counts where the number of bodies depends on the form
    #: as well as the slot (Create Undead: three ghouls, or two ghasts,
    #: or two mummies at the same slot). Overrides ``count_formula``.
    count_formulas: Mapping[str, str] = field(default_factory=dict)

    def count_formula_for(self, form: str | None) -> str:
        """The count formula this spec uses for ``form``, or ``""``."""
        key = (form or "").strip().lower()
        if key and key in self.count_formulas:
            return self.count_formulas[key]
        return self.count_formula

    def resolve(self, form: str | None) -> str | None:
        """The stat block this spec seats for the chosen ``form``, or
        ``None`` when a choice was owed and not made: the cast is refused
        rather than guessed (SRD 5.2 Find Steed states no default)."""
        if self.stat_block_slug is not None:
            return self.stat_block_slug if not form else None
        if not form:
            return None
        return self.forms.get(str(form).strip().lower())


# SRD 5.2 Summon Dragon: "It manifests in an unoccupied space that you can see
# within range and uses the Draconic Spirit stat block." The spell's one
# Foundry profile points at that stat block; the registry names it by slug.
SUMMONS: Final[Mapping[str, SummonSpec]] = MappingProxyType(
    {
        "summon-dragon": SummonSpec(stat_block_slug="draconic-spirit"),
        # SRD 5.2 Find Steed: "You summon a spirit that assumes the form of
        # a loyal, Medium Celestial, Fey, or Fiend." The spell asks the
        # player which; the three forms are the profiles its one summon
        # activity carries, keyed by the name a player would use.
        "find-steed": SummonSpec(
            forms={
                "celestial": "celestial-steed",
                "fey": "fey-steed",
                "fiend": "fiend-steed",
            }
        ),
        # SRD 5.2 Animate Dead: "Choose a pile of bones or a corpse of a
        # Medium or Small Humanoid within range. The target becomes an
        # Undead creature: a Skeleton if you chose bones or a Zombie if
        # you chose a corpse." Both stat blocks are in the corpus. The
        # spell's own count formula raises one body, plus two for each
        # level above 3.
        "animate-dead": SummonSpec(
            forms={"skeleton": "skeleton", "zombie": "zombie"},
            count_formula="1 + @scaling.increase * 2",
        ),
        # SRD 5.2 Create Undead (level 6): "Choose up to three corpses of
        # Medium or Small Humanoids within range. Each one becomes a
        # Ghoul". The spell's four profiles carry the counts "@item.level
        # - 3" (ghouls at 6/7: 3, 4), "@item.level - 6" twice (ghasts and
        # wights at 8/9: 2, 3) and a flat "2" (mummies at 9). The profiles
        # name no stat block, so the form decides which profile applies:
        # the book text is the only authority for the mapping.
        # SRD 5.2 Giant Insect: "You summon a giant centipede, spider, or
        # wasp (chosen when you cast the spell) ... and uses the Giant
        # Insect stat block. The form you choose determines certain
        # details in its stat block." All three forms read ONE stat
        # block: the spell's own bonuses already carry "AC 11 + the
        # spell's level" and "HP 30 + 10 for each spell level above 4",
        # and the block itself has no Fly speed, matching "Fly 40 ft.
        # (Wasp only)" — the per-form detail is not in the corpus, so it
        # is not invented here.
        "giant-insect": SummonSpec(
            forms={
                "giant-centipede": "giant-insect",
                "giant-spider": "giant-insect",
                "giant-wasp": "giant-insect",
            },
        ),
        # SRD 5.2 Find Familiar: "a spirit that takes an animal form you
        # choose: Bat, Cat, Frog, Hawk, Lizard, Octopus, Owl, Rat, Raven,
        # Spider, Weasel, or another Beast that has a Challenge Rating of 0."
        # The named forms are the book's own list; each stat block is in the
        # corpus. The trailing "or another Beast of CR 0" is left to the DM
        # through ``_ADJUDICATED_SUMMONS`` instead of a guess.
        "find-familiar": SummonSpec(
            forms={
                "bat": "bat",
                "cat": "cat",
                "frog": "frog",
                "hawk": "hawk",
                "lizard": "lizard",
                "octopus": "octopus",
                "owl": "owl",
                "rat": "rat",
                "raven": "raven",
                "spider": "spider",
                "weasel": "weasel",
            }
        ),
        "create-undead": SummonSpec(            forms={
                "ghoul": "ghoul",
                "ghast": "ghast",
                "wight": "wight",
                "mummy": "mummy",
            },
            count_formulas={
                "ghoul": "@item.level - 3",
                "ghast": "@item.level - 6",
                "wight": "@item.level - 6",
                "mummy": "2",
            },
        ),
    }
)


@dataclass(frozen=True)
class SummonRollData:
    """The roll data a summoned creature's numbers read: the summoning spell's
    slot level (Foundry ``@item.level`` in the activity's bonuses,
    ``@flags.dnd5e.summon.level`` in the stat block) and its caster's
    spellcasting ability modifier (``@flags.dnd5e.summon.mod``). SRD 5.2 Summon
    Dragon: "Use the spell slot's level for the spell's level in the stat
    block."
    """

    level: int
    mod: int
    #: ``@scaling.increase`` — how many levels above the spell's base level
    #: the slot was cast at (0 for a base-level slot). SRD 5.2 Animate
    #: Dead raises ``1 + @scaling.increase * 2`` bodies, not one.
    scaling_increase: int = 0


_SUMMON_TOKEN: Final = re.compile(r"@[A-Za-z_][\w.]*")
_INLINE_FORMULA: Final = re.compile(r"\[\[([^\[\]]*)\]\]")
_MULTIATTACK: Final = "multiattack"
_SUMMON_OPERATORS: Final[Mapping[type[ast.operator], Callable[[Fraction, Fraction], Fraction]]] = (
    MappingProxyType(
        {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
        }
    )
)


def evaluate_summon_formula(expr: str, roll_data: SummonRollData) -> int:
    """The whole number a summon formula evaluates to at ``roll_data``.

    A blank formula is 0. ``@item.level`` and ``@flags.dnd5e.summon.level``
    read the slot level, ``@flags.dnd5e.summon.mod`` the caster's modifier;
    then the arithmetic folds over the parsed tree (never ``eval``): integer
    literals, unary minus, ``+ - * /`` with exact division, parentheses and
    ``floor(x)``. Any other token or construct, a zero divisor, or a
    fractional result raises ``ValueError`` before anything is seated.
    """
    if not expr.strip():
        return 0
    values = {
        "@item.level": roll_data.level,
        "@flags.dnd5e.summon.level": roll_data.level,
        "@flags.dnd5e.summon.mod": roll_data.mod,
        "@scaling.increase": roll_data.scaling_increase,
    }

    def _value(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in values:
            raise ValueError(f"Unhandled summon roll-data token {token!r} in {expr!r}")
        return f"({values[token]})"

    try:
        tree = ast.parse(_SUMMON_TOKEN.sub(_value, expr), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Unparseable summon formula {expr!r}") from exc
    result = _fold_summon_node(tree.body, expr)
    if result.denominator != 1:
        raise ValueError(f"Fractional summon formula result {result} from {expr!r}")
    return int(result)


def _fold_summon_node(node: ast.expr, expr: str) -> Fraction:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return Fraction(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_fold_summon_node(node.operand, expr)
    if isinstance(node, ast.BinOp) and type(node.op) in _SUMMON_OPERATORS:
        left, right = _fold_summon_node(node.left, expr), _fold_summon_node(node.right, expr)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError(f"Zero divisor in summon formula {expr!r}")
        return _SUMMON_OPERATORS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "floor"
        and len(node.args) == 1
        and not node.keywords
    ):
        return Fraction(math.floor(_fold_summon_node(node.args[0], expr)))
    raise ValueError(f"Unsupported construct in summon formula {expr!r}: {ast.dump(node)}")


def summon_attack_count(monster: Monster, roll_data: SummonRollData) -> int | None:
    """How many attacks a summoned ``monster``'s Multiattack makes: the first
    inline formula of its ``multiattack`` description that reads the summon
    roll data, evaluated at ``roll_data``; ``None`` when there is none.

    SRD 5.2 Draconic Spirit: "The spirit makes a number of Rend attacks equal
    to half the spell's level (round down)" — Foundry's
    ``[[floor(@flags.dnd5e.summon.level / 2)]]``. Item enrichers such as
    ``[[/item Rend]]`` read no roll data and are skipped.
    """
    action = next((a for a in monster.actions if a.slug == _MULTIATTACK), None)
    if action is None:
        return None
    for formula in _INLINE_FORMULA.findall(action.description):
        if _SUMMON_ROLL_DATA in formula:
            return evaluate_summon_formula(formula, roll_data)
    return None


def summon_magnitudes(
    monster: Monster,
    match: SummonMatchBlock,
    *,
    spell_attack_bonus: int,
    proficiency_bonus: int,
    attack_damage_bonus: int,
) -> StatBlockMagnitudes:
    """A summoned ``monster``'s numbers under its spell's ``match`` flags.

    Its own six ability scores; the summoner's Proficiency Bonus when
    ``match.proficiency`` (SRD 5.2 Draconic Spirit: "PB equals your Proficiency
    Bonus"), else the stat block's; the summoner's spell attack bonus as a flat
    to-hit when ``match.attacks`` ("Bonus equals your spell attack modifier");
    and ``attack_damage_bonus`` on each attack's damage.
    """
    scores = monster.ability_scores
    return StatBlockMagnitudes(
        ability_scores={
            "str": scores.str,
            "dex": scores.dex,
            "con": scores.con,
            "int": scores.int,
            "wis": scores.wis,
            "cha": scores.cha,
        },
        proficiency_bonus=proficiency_bonus if match.proficiency else monster.proficiency_bonus,
        attack_bonus=spell_attack_bonus if match.attacks else None,
        attack_damage_bonus=attack_damage_bonus,
    )


@dataclass(frozen=True)
class SummonRequest:
    """A creature the orchestrator seats after resolution: the spell that
    summoned it, its owner, the space it manifests in, the slot level, and the
    spell's ``bonuses`` / ``match`` blocks its numbers read."""

    spell_id: str
    owner_id: str
    cell: str
    slot_level: int
    bonuses: SummonBonusesBlock
    match: SummonMatchBlock
    #: The form the player chose where the spell offers a choice (Find
    #: Steed); ``None`` for a spell with a single stat block.
    form: str | None = None
    #: How many creatures one cast seats, already evaluated from the
    #: activity's ``target.affects.count`` formula (Animate Dead:
    #: ``1 + @scaling.increase * 2``). 1 for every spell that raises one.
    count: int = 1


__all__ = [
    "CONJURATION_ALLOWLIST",
    "CONSTRUCTS",
    "ENCHANTED_WEAPON_FLAG",
    "SAVE_CONSEQUENCES",
    "SUMMONS",
    "TRANSFORM_FORM_FLAG",
    "TRANSFORM_RIDERS",
    "WILD_SHAPE_TIERS",
    "ConjurationCarrier",
    "ConjurationKind",
    "ConstructRequest",
    "ConstructSpec",
    "StatBlockMagnitudes",
    "SummonRequest",
    "SummonRollData",
    "SummonSpec",
    "TransformRequest",
    "TransformRider",
    "TransformSource",
    "WildShapeTier",
    "construct_attack_activity",
    "enchant_weapon",
    "evaluate_summon_formula",
    "summon_attack_count",
    "summon_magnitudes",
    "uses_summon_roll_data",
    "wild_shape_tier",
]
