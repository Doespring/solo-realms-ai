"""The build-spec contract: the typed input that resolves into a complete PC.

A host's character builder or a test fixture produces these. Resolution
(build_party_member) is pure; selection (who fills the build-spec) is the producer's job.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from dnd5e_srd_data.loader import AssetLoader
from dnd5e_srd_data.schema.advancement import AdvancementEntry
from dnd5e_srd_data.schema.background import Background
from dnd5e_srd_data.schema.class_ import Class, Subclass
from dnd5e_srd_data.schema.common import PassiveEffectChange
from dnd5e_srd_data.schema.refs import FeatureChoice as Choice
from dnd5e_srd_data.schema.feat import FeatCategory
from dnd5e_srd_data.schema.item import Armor
from dnd5e_srd_data.schema.species import Species
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# SRD 5.2 — единственная редакция, опубликованная в этом корпусе. Материал 2014
# отсутствует; объявить лист посчитанным по нему нельзя (требование 2).
CORPUS_EDITIONS: Final = frozenset({"5.2"})

from dnd5e_engine.activities.passive_stats import (
    CombatantMovementModes,
    CombatantSenses,
    interpret_passive_stats,
)
from dnd5e_engine.events import Ability
from dnd5e_engine.rules.character import (
    ABILITY_NAME_BY_CODE,
    AbilityName,
    AbilityScoreMethod,
    AcCalcMode,
    ArmorTraining,
    HpMode,
    ProficiencyGrants,
    ability_score_improvement,
    ac_mode_eligible,
    ac_modes_from_changes,
    apply_ability_increases,
    armor_class,
    armor_speed_penalty,
    armor_training_from_changes,
    attunement_limit,
    defense_ac_bonus,
    extra_attack_count,
    has_flag,
    hit_dice_pool,
    hit_die_size,
    hit_point_bonus,
    hit_points_max,
    leveled_feature_slugs,
    proficiency_grants,
    subclass_gate_level,
    validate_ability_score_method,
    validate_increase_budget,
    weapon_proficiencies_from_changes,
)
from dnd5e_engine.activities.scale import build_scale_values
from dnd5e_engine.rules.choices import (
    FeatPick,
    MagicInitiatePick,
    ParsedChoices,
    parse_selected_choices,
)
from dnd5e_engine.rules.dice import ability_modifier, proficiency_bonus
from dnd5e_engine.rules.skills import SKILL_CODE_TO_SLUG, Skill, passive_perception
from dnd5e_engine.spellcasting import (
    SpellcastingProgression,
    derive_pact_slots,
    derive_spell_slots,
    multiclass_caster_level,
    slots_for_caster_level,
)
from dnd5e_engine.types.combat import SpellcastingAbility

# Long-form -> the canonical field; short-form aliases the backend cache / lib may pass.
# A plain ``dict(ABILITY_NAME_BY_CODE)`` keeps the source's Literal key/value types,
# which mypy then rejects at ``_normalize_abilities``'s ``str`` lookups (dict is
# invariant); the comprehension widens both to ``str`` at the assignment.
_ABILITY_ALIASES: dict[str, str] = {code: name for code, name in ABILITY_NAME_BY_CODE.items()}
_LONG = set(_ABILITY_ALIASES.values())


class AbilityScores(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10


class CharacterBuildSpec(BaseModel):
    """The typed input that resolves into a complete PC.

    ``classes`` is the multiclass carrier — a ``{class_slug: level}``
    map. ``class_slug`` (= the FIRST key, the primary class) and ``level`` (= the SUM
    of class levels) are kept as single-class aliases and always populated, so
    existing single-class callers (``CharacterBuildSpec(class_slug=..., level=...)``)
    keep working exactly as before.

    Caveat: ``model_copy(update=...)`` bypasses the ``mode="before"``
    validator below (Pydantic does not re-run before-validators on
    ``model_copy``), so a ``model_copy`` that changes only ``level`` or only
    ``classes`` can desync the two fields. Construct a fresh
    ``CharacterBuildSpec(...)`` instead of ``model_copy`` when changing
    either field.

    C19 derivation inputs — see ``derive_sheet``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    species_slug: str
    # Требование 3: редакция и набор пакетов сохраняются в персонаже.
    # Поле принадлежит персонажу, а не внешнему релизу: сохранённый лист
    # должен сам сообщать, по каким правилам он посчитан. Умолчание —
    # единственная редакция этого корпуса, поэтому старые сохранения,
    # записанные без поля, читаются как SRD 5.2 без миграции.
    rules_edition: str = "5.2"
    class_slug: str = ""
    classes: dict[str, int] = Field(default_factory=dict)
    # Требование 5: стартовый класс — решение ИГРОКА, а не следствие порядка
    # ключей в словаре `classes`. Раньше `_check_multiclass_abilities` брал
    # `next(iter(classes))`, и одни и те же характеристики принимались для
    # `{"barbarian": 2, "wizard": 2}` и отвергались для `{"wizard": 2,
    # "barbarian": 2}` — варвар оказывался стартовым и не проверялся.
    # Умолчание `None` сохраняет чтение старых сохранений; тогда порядок
    # ключей остаётся резервным правилом (см. docstring проверки).
    starting_class: str | None = None
    subclass_slug: str | None = None
    level: int = Field(ge=1, le=20, default=1)
    ability_scores: AbilityScores = Field(default_factory=AbilityScores)
    equipment: tuple[str, ...] = ()
    selected_choices: tuple[str, ...] = ()
    require_complete_choices: bool = False
    background_slug: str | None = None
    hp_mode: HpMode = "fixed"
    # Host-recorded Hit Die results per class for ``hp_mode="rolled"``: the
    # first class records ``level - 1`` rolls (its level 1 is the maximum),
    # every other class ``level``. Recorded, never re-rolled, so a sheet
    # re-derives identically.
    hp_rolls: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    # ``None`` lets derive_sheet pick the best mode the character's features and
    # worn equipment allow; a value forces that mode (SRD 5.2: "you can benefit
    # from only one at a time").
    ac_calc_mode: AcCalcMode | None = None
    attuned_items: tuple[str, ...] = ()
    # Host-recorded pick among an item's ALTERNATIVES, keyed by item slug and
    # holding the chosen effect's display name (Armor of Resistance "(Acid)" vs
    # "(Cold)", a War Mage wand's "+2" vs "+3"). The Foundry packs mark every
    # alternative active and leave the choice to the table, so without this the
    # engine takes the first sibling -- right for an unconfigured item, wrong for
    # one the player already picked. Recorded, never re-rolled.
    item_variants: dict[str, str] = Field(default_factory=dict)
    ability_score_method: AbilityScoreMethod | None = None

    @field_validator("rules_edition")
    @classmethod
    def _edition_must_be_the_corpus_edition(cls, value: str) -> str:
        """Требование 2: редакция не берётся по имени или расположению файла.

        Корпус этой сборки — SRD 5.2. Пока поле принималось без проверки,
        персонаж мог объявить себя посчитанным по правилам 2014, которых в
        корпусе нет: лист выглядел законным, но описывал редакцию, которой не
        существует. Неизвестная редакция отвергается, а не подменяется
        похожей (п.2: «не заменяй отсутствие источника догадкой»).
        """
        if value not in CORPUS_EDITIONS:
            raise ValueError(
                f"rules_edition {value!r} не опубликована в этом корпусе; "
                f"доступно: {sorted(CORPUS_EDITIONS)}. Материал 2014 в поставку "
                f"не входит и не подменяется правилами 5.2."
            )
        return value
    @model_validator(mode="before")
    @classmethod
    def _reconcile_classes(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        classes = dict(data.get("classes") or {})
        class_slug = data.get("class_slug")
        level = data.get("level")
        if classes:
            for slug, lvl in classes.items():
                if not isinstance(lvl, int) or lvl < 1:
                    raise ValueError(
                        f"class level for {slug!r} must be a positive int, got {lvl!r}"
                    )
            total = sum(classes.values())
            primary = next(iter(classes))
            if class_slug and class_slug not in classes:
                raise ValueError(
                    f"class_slug {class_slug!r} is not one of classes {sorted(classes)}"
                )
            if level is not None and level != total:
                raise ValueError(f"level {level} does not equal the sum of classes ({total})")
            data["class_slug"] = class_slug or primary
            data["level"] = total
            data["classes"] = classes
        elif class_slug:
            data["classes"] = {class_slug: level if level is not None else 1}
        else:
            raise ValueError("CharacterBuildSpec needs class_slug or classes")
        return data


class CombatInstance(BaseModel):
    """Combat-instance values a host may pin, overriding derivation.

    Entity identity (entity_id/name) always comes from here. Character values
    such as HP, AC, attack bonus and speed may be pinned here; leaving any of
    ``hp_current``/``hp_max``/``ac``/``attack_bonus``/``base_speed`` as its
    ``None`` default lets ``build_party_member`` derive it from the build
    spec instead. Deciding by ``is None`` (rather than a
    ``model_fields_set`` sentinel) means a ``CombatInstance`` rebuilt from
    ``CombatInstance(**inst.model_dump())``, or round-tripped through JSON,
    still derives whatever it left unset. ``initiative`` is seated as given
    (``0`` by default); ``None`` lets ``start_combat`` roll it — d20 + the
    derived Dexterity modifier, plus the Proficiency Bonus for a character
    with Alert (SRD 5.2 Initiative: "every participant rolls Initiative; they
    make a Dexterity check").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    entity_id: str
    name: str
    hp_current: int | None = None
    hp_max: int | None = None
    ac: int | None = None
    attack_bonus: int | None = None
    base_speed: int | None = None
    initiative: int | None = 0
    zone_id: str = ""
    concentration_effect_id: str | None = None
    spell_slots: dict[int, int] = Field(default_factory=dict)
    # SRD Pact Magic — the Warlock's separately-recovering slot pool
    # ``{slot_level: count_remaining}``, spent by the orchestrator.
    pact_slots: dict[int, int] = Field(default_factory=dict)
    spells_known: tuple[str, ...] = ()


def _normalize_abilities(raw: dict[str, int]) -> AbilityScores:
    out: dict[str, int] = {}
    for k, v in raw.items():
        key = _ABILITY_ALIASES.get(k, k)
        if key not in _LONG:
            raise ValueError(f"unknown ability key: {k!r}")
        out[key] = int(v)
    return AbilityScores(**out)


def make_build_spec(
    *,
    species_slug: str,
    class_slug: str | None = None,
    level: int | None = None,
    classes: Mapping[str, int] | None = None,
    subclass_slug: str | None = None,
    ability_scores: dict[str, int] | None = None,
    equipment: tuple[str, ...] = (),
    selected_choices: tuple[str, ...] = (),
    # П.5: недобор обязательного выбора даёт ОТКАЗ только когда лист объявлен
    background_slug: str | None = None,
    hp_mode: HpMode = "fixed",
    hp_rolls: Mapping[str, Sequence[int]] | None = None,
    ac_calc_mode: AcCalcMode | None = None,
    attuned_items: tuple[str, ...] = (),
    ability_score_method: AbilityScoreMethod | None = None,
) -> CharacterBuildSpec:
    payload: dict[str, Any] = {
        "species_slug": species_slug,
        "subclass_slug": subclass_slug,
        "ability_scores": _normalize_abilities(ability_scores or {}),
        "equipment": equipment,
        "selected_choices": selected_choices,
        "background_slug": background_slug,
        "hp_mode": hp_mode,
        "hp_rolls": {k: tuple(v) for k, v in (hp_rolls or {}).items()},
        "ac_calc_mode": ac_calc_mode,
        "attuned_items": attuned_items,
        "ability_score_method": ability_score_method,
    }
    if classes:
        payload["classes"] = dict(classes)
    if class_slug is not None:
        payload["class_slug"] = class_slug
    if level is not None:
        payload["level"] = level
    return CharacterBuildSpec(**payload)


def _progressions(
    classes: Mapping[str, int], loader: AssetLoader
) -> dict[str, tuple[SpellcastingProgression, int]]:
    out: dict[str, tuple[SpellcastingProgression, int]] = {}
    for slug, lvl in classes.items():
        cls = loader.get_class(slug)
        if cls is None:
            raise ValueError(f"unknown class: {slug!r}")
        out[slug] = (cast(SpellcastingProgression, str(cls.spellcasting.progression)), lvl)
    return out


def derive_multiclass_slots(
    classes: Mapping[str, int], *, loader: AssetLoader | None = None
) -> dict[int, int]:
    """Multiclass Spellcasting-feature slots for a ``{class_slug: level}`` map.

    Reads each class's ``spellcasting.progression`` through ``loader`` (default:
    the configured lib loader), applies the SRD per-class rounding and looks
    the total up in the Multiclass Spellcaster table. A single-class map returns
    exactly ``derive_spell_slots(...)`` for that class.
    """
    if loader is None:
        from dnd5e_engine.lib_loader import get_lib_loader

        loader = get_lib_loader()
    return slots_for_caster_level(multiclass_caster_level(_progressions(classes, loader)))


def derive_multiclass_pact_slots(
    classes: Mapping[str, int], *, loader: AssetLoader | None = None
) -> dict[int, int]:
    """Pact Magic pool for the ``pact``-progression class levels in ``classes`` (``{}`` if none)."""
    if loader is None:
        from dnd5e_engine.lib_loader import get_lib_loader

        loader = get_lib_loader()
    pact_levels = [lvl for prog, lvl in _progressions(classes, loader).values() if prog == "pact"]
    return derive_pact_slots(sum(pact_levels)) if pact_levels else {}


_log = logging.getLogger(__name__)

_LeveledSource = tuple[Class | Subclass | Species | None, int]

# SRD 5.2 Epic Boon feats: "Epic Boon Feat (Prerequisite: Level 19+)". Two boons
# carry no level prerequisite in the dataset, so the floor comes from the
# category.
_EPIC_BOON_LEVEL: Final = 19
_MAGIC_INITIATE: Final = "magic-initiate"


class DerivedSheet(BaseModel):
    """Everything ``derive_sheet`` derives from a ``CharacterBuildSpec``.

    Vocabularies match the engine inputs a host feeds next: ability modifiers
    keyed by the long ability name (the ``AbilityScores`` fields), save
    proficiencies by 3-letter ``Ability`` code and skills by long-form slug —
    as ``PartyMemberSpec`` and ``CheckSpec`` take them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ability_scores: AbilityScores
    ability_modifiers: dict[AbilityName, int]
    proficiency_bonus: int
    base_speed: int
    movement_modes: CombatantMovementModes
    ignored_difficult_terrain: tuple[str, ...] = ()
    # Languages an item teaches (Demon Armor -> abyssal).
    languages: tuple[str, ...] = ()
    # SRD 5.2 Ioun Stone of Awareness: "Advantage on Initiative rolls." 1 =
    # advantage, -1 = disadvantage, 0 = neither.
    initiative_advantage: int = 0
    senses: CombatantSenses
    damage_resistances: tuple[str, ...]
    damage_immunities: tuple[str, ...]
    condition_immunities: tuple[str, ...]
    save_proficiencies: frozenset[Ability]
    skill_proficiencies: frozenset[Skill]
    skill_expertise: frozenset[Skill]
    tool_proficiencies: tuple[str, ...] = ()
    weapon_proficiencies: frozenset[str]
    # SRD 5.2 §Weapon Mastery: a class Mastery feature grants a LIMITED choice of
    # weapon kinds ("choose three", fighter level 1), not every mastery printed
    # on every weapon. Without this field the engine applied a weapon's mastery
    # to whoever swung it, so a fighter who never chose anything still knocked
    # targets prone-prone with Sap. An empty set is the honest default: a sheet
    # that names no choice earns no mastery, matching the printed rules.
    weapon_masteries: frozenset[str] = frozenset()
    armor_training: frozenset[ArmorTraining]
    passive_perception: int
    jack_of_all_trades: bool
    reliable_talent: bool
    stealth_disadvantage: bool
    extra_attack_count: int
    # Требование 3: лист сам сообщает, по какой редакции посчитан.
    rules_edition: str = "5.2"
    # Требование 5: стартовое снаряжение предыстории, разрешённое в слаги
    # корпуса (`(slug, count)`), монеты отдельно. У классов в корпусе записей
    # нет (только `wealth`) — это ограничение источника, см.
    # `starting_equipment.py`.
    starting_equipment: tuple[tuple[str, int], ...] = ()
    wealth_gp: int = 0
    features: tuple[str, ...]
    # SRD 5.2 feature-choice pool picks (Sorcerer Metamagic, ...). Kept apart
    # from `features` because the corpus lists them in a POOL, never in a
    # `granted_features` table, so combat's repertoire gate needs them named.
    chosen_features: tuple[str, ...] = ()
    feats: tuple[str, ...]
    # SRD 5.2 Magic Initiate: each spell the ``magic-initiate:`` tokens chose →
    # the ability that casts it, and the level 1 spells cast once per Long Rest
    # without a spell slot.
    spell_abilities: dict[str, SpellcastingAbility] = Field(default_factory=dict)
    # SRD 5.2 Spellcasting — заговоры и подготовленные заклинания класса.
    # Квоты читаются из ScaleValue корпуса (Cantrips Known / Max Prepared
    # Spells), а не задаются в коде: правила должны приходить из источника.
    cantrips: tuple[str, ...] = ()
    prepared_spells: tuple[str, ...] = ()
    slotless_casts: tuple[str, ...] = ()
    spell_slots: dict[int, int]
    pact_slots: dict[int, int]
    hp_max: int
    hit_dice: dict[int, int]
    ac: int
    ac_calc_mode: AcCalcMode


def _class_docs(classes: Mapping[str, int], loader: AssetLoader) -> dict[str, Class]:
    docs: dict[str, Class] = {}
    for slug in classes:
        cls = loader.get_class(slug)
        if cls is None:
            raise ValueError(f"unknown class: {slug!r}")
        docs[slug] = cls
    return docs


def _species(slug: str, loader: AssetLoader) -> Species:
    species = loader.get_species(slug)
    if species is None:
        raise ValueError(f"unknown species: {slug!r}")
    return species


def _subclass(
    spec: CharacterBuildSpec, class_docs: Mapping[str, Class], loader: AssetLoader
) -> tuple[Subclass | None, int]:
    """The subclass and its owning class's level. SRD 5.2 grants a subclass at
    the class's ``Subclass`` advancement level, so naming one earlier is not a
    legal build; a multiclass subclass may belong to any class taken."""
    if spec.subclass_slug is None:
        return None, 0
    sub = loader.get_subclass(spec.subclass_slug)
    if sub is None:
        raise ValueError(f"unknown subclass: {spec.subclass_slug!r}")
    owner = sub.class_identifier
    if owner not in spec.classes:
        raise ValueError(
            f"subclass {spec.subclass_slug!r} is not a subclass of any of {sorted(spec.classes)}"
        )
    gate = subclass_gate_level(class_docs[owner])
    level = spec.classes[owner]
    if gate is not None and level < gate:
        raise ValueError(
            f"subclass {spec.subclass_slug!r} needs {owner} level {gate}; the build has {level}"
        )
    return sub, level


def _always_on_changes(features: Sequence[str], loader: AssetLoader) -> list[PassiveEffectChange]:
    """Changes of every always-on passive effect (``transfer`` and not
    ``disabled``) on the character's features. An unresolved slug is logged and
    skipped: a dataset gap must not block a sheet."""
    changes: list[PassiveEffectChange] = []
    for slug in features:
        feature = loader.get_feature(slug)
        if feature is None:
            _log.warning(
                "derive_sheet: granted feature slug did not resolve to a canonical "
                "feature; skipping",
                extra={"granted_feature_slug": slug},
            )
            continue
        for passive in feature.passive_effects:
            if passive.transfer and not passive.disabled:
                changes.extend(passive.changes)
    return changes


_ABILITY_SCORE_KEY_PREFIX = "system.abilities."
_AC_BONUS_KEY = "system.attributes.ac.bonus"
_AC_FORMULA_KEY = "system.attributes.ac.formula"
_HP_BONUS_KEY_PREFIX = "system.attributes.hp.bonuses"
# Foundry change modes the item corpus actually uses for ability scores:
# 2 = add ("+2 to your Dexterity"), 4 = upgrade ("your score is 19").
_ITEM_MODE_ADD = 2
_ITEM_MODE_UPGRADE = 4


def _switched_on_by_an_activity(item: Any, passive: Any) -> bool:
    """True iff the item's own activity switches ``passive`` on.

    SRD 5.2 Boots of Speed: "While you wear these boots, you can take a Bonus
    Action to click the boots' heels together. If you do, the boots double
    your Speed." The pack ships the doubling as an ordinary enabled effect,
    but the wearer only gets it by using the "Click Heels" activity -- which
    names this passive by id in its ``effects[]`` refs. Wearing the boots
    alone must not double the sheet's speed.
    """
    passive_id = getattr(passive, "id", None)
    if not passive_id:
        return False
    return any(
        ref.id == passive_id
        for activity in getattr(item, "activities", ()) or ()
        for ref in getattr(activity, "effects", ()) or ()
    )


def _item_always_on_changes(
    spec: CharacterBuildSpec, loader: AssetLoader
) -> list[PassiveEffectChange]:
    """Changes of every always-on passive effect carried by the character's own
    equipment. SRD 5.2 section Magic Items: a magic item's benefit applies while
    the item is worn or held, and for an item that requires attunement, only
    while the creature is attuned to it. A dataset gap must not block a sheet,
    so an unresolved slug is skipped exactly like an unresolved feature slug.

    Only transfer-and-not-disabled effects count, the same filter
    _always_on_changes applies to features. The Foundry packs also ship
    enchantment TEMPLATE effects on magic armour (adamantine-armor carries one);
    those mutate the ITEM document rather than the wearer and are marked
    disabled, so they are excluded here by the same condition.
    """
    attuned = frozenset(spec.attuned_items)
    # A slug listed twice names ONE item, not two: count each worn object once so
    # a duplicated equipment entry cannot double its bonus. (_worn already
    # refuses two suits of armour or two shields; arbitrary items reach here.)
    changes: list[PassiveEffectChange] = []
    for slug in dict.fromkeys(spec.equipment):
        item = loader.get_item(slug)
        if item is None:
            continue
        # An attunement-gated item lends nothing until the creature attunes.
        if getattr(item, "requires_attunement", False) and slug not in attuned:
            continue
        # A WEAPON is held, not worn. SRD 5.2 gives no weapon an always-on hit
        # point bonus; the Foundry packs carry system.attributes.hp.bonuses.level
        # on berserker-axe as part of its enchantment template, which would hand
        # the wielder +1 HP per level. Holding a weapon does not raise hit points,
        # so that key is dropped at the seam rather than modelled.
        weapon = getattr(item, "item_kind", None) == "weapon"
        active = [
            passive
            for passive in getattr(item, "passive_effects", ()) or ()
            # An effect the item's ACTIVITY switches on is not on while the
            # item is merely worn (Boots of Speed: click the heels).
            if passive.transfer
            and not passive.disabled
            and not _switched_on_by_an_activity(item, passive)
        ]
        selected = _pick_item_variant(
            active,
            document_name=getattr(item, "name", ""),
            chosen_name=spec.item_variants.get(slug),
        )
        for passive in selected:
            changes.extend(
                change
                for change in passive.changes
                if not (weapon and change.key.startswith(_HP_BONUS_KEY_PREFIX))
            )
    return changes


def _pick_item_variant(
    active: Sequence[Any], *, document_name: str, chosen_name: str | None = None
) -> list[Any]:
    """The sub-effects of ONE item that actually apply to its bearer.

    The Foundry packs express a magic item's choices as several always-on
    effects on the same document: Armor of Resistance ships ten "(Acid)",
    "(Cold)", ... effects, the War Mage wands ship "+1", "+2" and "+3", the
    dragon scale mails ship one entry per dragon. Carrying the item does not
    grant all of them -- the GM or the table picks one -- so applying every
    sibling would hand out ten resistances for one ring.

    Siblings are recognised by sharing an identical key/mode set: those are
    alternatives for the same slot. Effects with DIFFERENT keys are distinct
    parts of one item (Robe of the Archmagi: its armour formula AND its war
    mage bonus) and all of them apply.

    The item document itself does not say which alternative was chosen -- the
    packs mark every sibling equally active. Without a host-side choice the
    engine takes the FIRST alternative, which is what an unconfigured item
    shows in Foundry once one option is selected, and never more than one.
    """
    groups: dict[tuple[tuple[str, int], ...], list[Any]] = {}
    for passive in active:
        signature = tuple(sorted((c.key, c.mode) for c in passive.changes))
        groups.setdefault(signature, []).append(passive)
    picked: list[Any] = []
    for signature, siblings in groups.items():
        if len(siblings) == 1:
            picked.extend(siblings)
            continue
        chosen = siblings[0]
        if chosen_name is not None:
            named = [s for s in siblings if getattr(s, "name", None) == chosen_name]
            if not named:
                raise ValueError(
                    f"item_variants[{document_name!r}] names {chosen_name!r}, which is "
                    f"not one of its alternatives: {[getattr(s, 'name', None) for s in siblings]}"
                )
            chosen = named[0]
        _log.info(
            "item %r offers %d alternative effects for %s; applying %r (the pack "
            "does not record the choice)",
            document_name,
            len(siblings),
            [key for key, _mode in signature],
            getattr(chosen, "name", None),
        )
        picked.append(chosen)
    return picked


def _hit_points(
    spec: CharacterBuildSpec,
    die_sizes: Mapping[str, int],
    con_modifier: int,
    changes: Sequence[PassiveEffectChange],
) -> int:
    if spec.hp_mode == "fixed" and spec.hp_rolls:
        raise ValueError("hp_rolls given with hp_mode='fixed'; set hp_mode='rolled' to use them")
    rolls = spec.hp_rolls if spec.hp_mode == "rolled" else None
    return hit_points_max(spec.classes, die_sizes, con_modifier, rolls=rolls) + hit_point_bonus(
        changes, total_level=spec.level, classes=spec.classes
    )


def _background(slug: str | None, loader: AssetLoader) -> Background | None:
    if slug is None:
        return None
    background = loader.get_background(slug)
    if background is None:
        raise ValueError(f"unknown background: {slug!r}")
    return background


def _background_feats(background: Background | None, loader: AssetLoader) -> list[str]:
    """The background's feat — SRD 5.2: "A background gives your character a
    specified Origin feat"."""
    if background is None or not background.starting_feat_slug:
        return []
    if loader.get_feat(background.starting_feat_slug) is None:
        raise ValueError(
            f"background {background.slug!r} grants unknown feat {background.starting_feat_slug!r}"
        )
    return [background.starting_feat_slug]


def _skills(
    grants: ProficiencyGrants, background: Background | None, choices: ParsedChoices
) -> tuple[frozenset[Skill], frozenset[Skill]]:
    skills = set(grants.skills) | set(choices.skills)
    if background is not None:
        skills |= {
            SKILL_CODE_TO_SLUG[c] for c in background.skill_proficiencies if c in SKILL_CODE_TO_SLUG
        }
    for skill in choices.expertise:
        if skill not in skills:
            raise ValueError(f"Expertise in {skill} needs proficiency in {skill}")
    return frozenset(skills), frozenset(choices.expertise)


def _trait_languages(advancement: object, level: int) -> tuple[str, ...]:
    """SRD 5.2 class Traits that GRANT languages outright.

    A Druid's Trait advancement at level 1 carries
    configuration.grants = ["languages:exotic:druidic"]. The engine read
    languages only from item effects, so Druidic never reached the sheet.

    Three spellings occur in the corpus:
      "languages:exotic:druidic"  -> a concrete language (grant it)
      "languages:exotic:*"       -> any exotic language (the player picks)
      "languages:*"              -> any language (the player picks)
    The two-part form is an OPEN CHOICE, not an unparsed grant.
    """
    out: list[str] = []
    for entry in advancement or ():
        if getattr(entry, "type", None) != "Trait":
            continue
        if (getattr(entry, "level", 0) or 0) > level:
            continue
        configuration = getattr(entry, "configuration", None)
        if isinstance(configuration, dict):
            grants = configuration.get("grants") or ()
        else:
            grants = getattr(configuration, "grants", ()) or ()
        for grant in grants:
            parts = str(grant).strip().lower().split(":")
            if len(parts) < 2 or parts[0] != "languages":
                continue
            name = parts[-1].strip()
            if not name or name == "*":
                continue
            if name not in out:
                out.append(name)
    return tuple(out)


def _expertise_quota(advancement: object, level: int) -> int:
    """SRD 5.2 §Expertise — how many skills a class upgrades at ``level``.

    The corpus carries the grant as a ``Trait`` whose configuration holds
    ``"mode": "expertise"`` with ``choices: [{count: 2, pool: ["skills:*"]}]``.
    The engine read the Chosen Expertise tokens but never compared them with
    this quota, and never wrote them to the sheet, so every Rogue showed
    ``expertise == ()`` no matter what the player picked.

    Counts accumulate: a Rogue has 2 at level 1 and 2 more at level 6.
    """
    total = 0
    for entry in advancement or ():
        if getattr(entry, "type", None) != "Trait":
            continue
        if (getattr(entry, "level", 0) or 0) > level:
            continue
        configuration = getattr(entry, "configuration", None)
        mode = (
            configuration.get("mode")
            if isinstance(configuration, dict)
            else getattr(configuration, "mode", None)
        )
        if mode != "expertise":
            continue
        choices = (
            configuration.get("choices") if isinstance(configuration, dict)
            else getattr(configuration, "choices", None)
        ) or ()
        for choice in choices:
            count = (
                choice.get("count") if isinstance(choice, dict)
                else getattr(choice, "count", 0)
            )
            total += int(count or 0)
    return total


def _background_languages(background: Background | None) -> tuple[str, ...]:
    """SRD 5.2 Backgrounds — the Languages a background grants outright.

    ``canonical/backgrounds/*.json`` carries ``languages`` (``["common"]``); the
    engine read languages only from item effects (``passive_stats``), so a
    background grant never reached the sheet and every build showed an empty
    language list. The corpus is the source: nothing is inferred or translated.
    """
    if background is None:
        return ()
    raw = getattr(background, "languages", ()) or ()
    out: list[str] = []
    for value in raw:
        slug = str(value).strip().lower()
        if slug and slug not in out:
            out.append(slug)
    return tuple(out)


def _background_tools(background: Background | None) -> tuple[str, ...]:
    """SRD 5.2 Backgrounds — Tool Proficiencies as the corpus spells them.

    The codes are the corpus's own (``game:*`` = any Gaming Set, ``art:calligrapher``,
    ``thief``). They are passed through verbatim rather than mapped: the choice a
    ``*`` leaves open belongs to the player, and inventing a concrete tool would
    decide it for them.
    """
    if background is None:
        return ()
    raw = getattr(background, "tool_proficiencies", ()) or ()
    out: list[str] = []
    for value in raw:
        slug = str(value).strip().lower()
        if slug and slug not in out:
            out.append(slug)
    return tuple(out)


def _choice_options(level_sources: Sequence[_LeveledSource]) -> dict[str, str]:
    """Every option of every feature-choice pool the build has reached (a pool
    opens at its first schedule level with a non-zero count) → its ref type."""
    options: dict[str, str] = {}
    for source, level in level_sources:
        if source is None:
            continue
        for choice in source.feature_choices:
            if any(step.count > 0 and step.level <= level for step in choice.schedule):
                for option in choice.pool:
                    options.setdefault(option.slug, option.ref_type)
    return options


def _check_choice_quotas(
    picks: Sequence[str],
    level_sources: Sequence[_LeveledSource],
    options: Mapping[str, str],
    *, strict: bool = False,
) -> None:
    """Refuse a pick set that over-fills or under-fills a feature-choice pool.

    `_split_picks` below only asks whether a slug belongs to SOME reached
    pool. A pool also carries a per-level `count` (Foundry
    `ItemChoice.choices` / Trait `choices[].count`) that BOUNDS how many of
    its options a character may take — and a character who reaches the pool
    owes at least one. Reading only pool membership let a Goliath take all
    SIX Giant Ancestry benefits at once (the corpus says `count: 1`) and let
    any character skip an obligatory choice outright, producing a
    lawful-looking sheet whose mandatory abilities simply do not exist (§5).

    `count` is the HIGHEST single level step, not a sum: a pool that opens
    twice (Rogue Expertise at 1 and 6) is a fresh choice each time.
    `count == 0` disables the pool and is skipped.

    A quota belongs to a SOURCE, and the source here is the (document, level)
    pair the pool was read from — a Class, a Subclass or a Species. One slug
    can sit in more than one source's pool: the Fighter's Fighting Style
    (`count: 1`) and the Champion's own Fighting Style (`count: 1`, at level
    7) list the same four styles, and so does the Human's Origin feat pool.
    Charging every pick to every pool that names it read the Champion's style
    as an over-fill of the Fighter pool (defect 54). So each source is
    settled on its own: of the picks it names, the ones no other reached
    source names are taken first, then the shared ones, smallest obligation
    first — a source whose pool mirrors another never eats the other's slot.
    """
    pools: list[tuple[str, tuple[Choice, ...], int]] = []
    for source, level in level_sources:
        if source is None:
            continue
        reached = tuple(
            choice for choice in source.feature_choices
            if any(step.count > 0 and step.level <= level for step in choice.schedule)
        )
        if not reached:
            continue
        # SUM, not the highest single step: SRD 5.2 Sorcerer Metamagic grants
        # 2 options at level 2, 2 more at 10 and 2 more at 17, so a Sorcerer
        # 10 legitimately holds FOUR. Taking the max let the sheet hold only
        # two and refused the third — a lawful character could not be built.
        for choice in reached:
            own_quota = sum(
                step.count for step in choice.schedule
                if step.count > 0 and step.level <= level
            )
            pools.append((str(source.slug), (choice,), own_quota))

    def names(own: tuple[Choice, ...]) -> set[str]:
        return {option.slug for choice in own for option in choice.pool}

    pool_names = [names(own) for _, own, _ in pools]
    # A slug is contested when more than one DISTINCT source names it: two
    # pools of the SAME source naming one slug are one obligation seen twice,
    # not a contest (Fighter's own pool, repeated entry by entry).
    source_of = [slug for slug, _, _ in pools]
    holders: dict[str, set[str]] = {}
    for name, group in zip(source_of, pool_names):
        for slug in group:
            holders.setdefault(slug, set()).add(name)

    remaining = list(picks)
    # Smallest obligation first: the tightest pool claims what it names before
    # a roomier pool that could also use the same slug.
    order = sorted(range(len(pools)), key=lambda i: pools[i][2])
    for index in order:
        slug_name, own, quota = pools[index]
        named = [s for s in remaining if s in pool_names[index]]
        exclusive = [s for s in named if holders[s] == {slug_name}]
        shared = [s for s in named if holders[s] != {slug_name}]
        # Over-fill is judged on the picks ONLY this source can claim, before
        # any truncation: slicing to `quota` first made a fifth Metamagic or a
        # sixth Giant Ancestry benefit vanish silently instead of being
        # refused (§5). A pick a sibling pool also names belongs to whichever
        # pool takes it (the Champion's level-7 Fighting Style reuses the
        # Fighter's four styles), so counting it here would refuse a lawful
        # build (defect 54 again).
        if len(exclusive) > quota:
            raise ValueError(
                f'choice {slug_name!r} takes at most {quota} of '
                f'{sorted(pool_names[index])}; got {len(exclusive)}: {sorted(exclusive)}'
            )
        taken = [*exclusive, *shared][:quota]
        for slug in taken:
            remaining.remove(slug)
        if (
            strict
            and len(taken) < quota
            and not any(o.optional for choice in own for o in choice.pool)
        ):
            raise ValueError(
                f'choice {slug_name!r} requires {quota} of {sorted(pool_names[index])}; '
                f'got {len(taken)}. Name the missing pick in selected_choices'
            )


def _split_picks(picks: Sequence[str], options: Mapping[str, str]) -> tuple[list[str], list[str]]:
    features: list[str] = []
    feats: list[str] = []
    for slug in picks:
        ref_type = options.get(slug)
        if ref_type is None:
            raise ValueError(
                f"selected_choices pick {slug!r} is not an option of any feature choice "
                "this build has reached"
            )
        (feats if ref_type == "feat" else features).append(slug)
    return features, feats


def _asi_slot(
    kind: str,
    class_slug: str,
    level: int,
    spec: CharacterBuildSpec,
    class_docs: Mapping[str, Class],
    used: set[tuple[str, int]],
) -> AdvancementEntry:
    token = f"{kind}:{class_slug}:{level}"
    if class_slug not in spec.classes:
        raise ValueError(
            f"{token}: {class_slug!r} is not one of the build's classes {sorted(spec.classes)}"
        )
    if level > spec.classes[class_slug]:
        raise ValueError(
            f"{token} needs {class_slug} level {level}; the build has {spec.classes[class_slug]}"
        )
    entry = ability_score_improvement(class_docs[class_slug], level)
    if entry is None:
        raise ValueError(
            f"{token}: there is no Ability Score Improvement at {class_slug} level {level}"
        )
    if (class_slug, level) in used:
        raise ValueError(
            f"the Ability Score Improvement at {class_slug} level {level} is already used"
        )
    used.add((class_slug, level))
    return entry


def _ability_scores(
    spec: CharacterBuildSpec,
    choices: ParsedChoices,
    class_docs: Mapping[str, Class],
    background: Background | None,
    used: set[tuple[str, int]],
    item_changes: Sequence[PassiveEffectChange] = (),
) -> dict[AbilityName, int]:
    """Base scores, the background adjustment, each ASI in token order, then
    the worn items' own ability bonuses and floors.

    Item changes come last because SRD 5.2 states them against the FINISHED
    score: Gauntlets of Ogre Power set the score to 19 ("this value or the
    current one, whichever is higher"), so a character who reached 20 by
    levelling keeps 20, and the Ioun Stone's "+2" adds to the final number.
    """
    scores = cast(dict[AbilityName, int], spec.ability_scores.model_dump())
    if spec.ability_score_method is not None:
        validate_ability_score_method(scores, spec.ability_score_method)
    if choices.background is not None:
        if background is None:
            raise ValueError("a background: choice needs background_slug")
        options = background.ability_options
        source = f"background {background.slug!r}"
        validate_increase_budget(
            choices.background,
            allowed={ABILITY_NAME_BY_CODE[code] for code in options.options},
            points=options.points,
            cap=options.cap,
            source=source,
        )
        scores = apply_ability_increases(scores, choices.background, source=source)
    all_names = set(ABILITY_NAME_BY_CODE.values())
    for pick in choices.asis:
        config = _asi_slot("asi", pick.class_slug, pick.level, spec, class_docs, used).configuration
        locked = {
            ABILITY_NAME_BY_CODE[c] for c in config.get("locked") or () if c in ABILITY_NAME_BY_CODE
        }
        source = f"asi:{pick.class_slug}:{pick.level}"
        validate_increase_budget(
            pick.increases,
            allowed=all_names - locked,
            points=int(config.get("points") or 0),
            cap=int(config.get("cap") or 2),
            source=source,
        )
        scores = apply_ability_increases(scores, pick.increases, source=source)
    floors, bonuses = _item_ability_floors(item_changes)
    for name, amount in bonuses.items():
        scores[name] = scores.get(name, 0) + amount
    for name, floor in floors.items():
        if scores.get(name, 0) < floor:
            scores[name] = floor
    return scores


def _item_ability_floors(
    changes: Sequence[PassiveEffectChange],
) -> tuple[dict[AbilityName, int], dict[AbilityName, int]]:
    """Ability floors and flat bonuses carried by worn equipment.

    SRD 5.2 prints two shapes, both Foundry-native:

    * "your Strength is 19" (Gauntlets of Ogre Power, Amulet of Health, Belt of
      Giant Strength) is change mode 4 (upgrade): "this value or the current
      one, whichever is higher". A floor is NOT a bonus -- a creature already
      above it keeps its own score;
    * "+2 to your Dexterity" (the Ioun Stone family) is mode 2 (add).

    Returns (floors, bonuses). The caller applies the bonuses first and the
    floors last, because a floor caps nothing and everything else stacks on
    top of the final score. A non-literal value is ignored, the same policy
    interpret_passive_stats applies to keys it cannot resolve.
    """
    floors: dict[AbilityName, int] = {}
    bonuses: dict[AbilityName, int] = {}
    for change in changes:
        key = change.key
        if not key.startswith(_ABILITY_SCORE_KEY_PREFIX):
            continue
        # The Foundry key spells the ability AND its sub-path ("con.value"),
        # so only the leading segment names the ability.
        code = key[len(_ABILITY_SCORE_KEY_PREFIX) :].split(".")[0]
        name = ABILITY_NAME_BY_CODE.get(code)
        if name is None:
            continue
        literal = _literal_int(change.value)
        if literal is None:
            continue
        if change.mode == _ITEM_MODE_UPGRADE:
            floors[name] = max(floors.get(name, 0), literal)
        elif change.mode == _ITEM_MODE_ADD:
            bonuses[name] = bonuses.get(name, 0) + literal
    return floors, bonuses


def _literal_int(value: str) -> int | None:
    """The integer a change value spells out, or None when it is not a literal."""
    text = str(value).strip().strip(chr(34)).strip()
    if text.startswith("+"):
        text = text[1:]
    try:
        return int(text)
    except ValueError:
        return None


_REQUIREMENT_RE: Final = re.compile(
    r"^(?P<names>[A-Za-z]+(?: or [A-Za-z]+)*) (?P<need>\d+)\+$"
)

_REQUIREMENT_ABILITY: Final = {
    "strength": "strength", "dexterity": "dexterity", "constitution": "constitution",
    "intelligence": "intelligence", "wisdom": "wisdom", "charisma": "charisma",
}


def _check_requirement_prose(
    slug: str,
    text: str,
    scores: Mapping[str, int],
    pick: FeatPick,
) -> None:
    """SRD 5.2 feat prerequisites carried as PROSE rather than a field.

    Grappler reads ``"Strength or Dexterity 13+"``; the corpus stores that as
    ``FeatPrerequisite.requirement`` free text, and nothing used to read it — so
    a character with Strength 10 and Dexterity 10 could take Grappler. That is
    the requirement 5 defect this function closes.

    Only forms this function can decide mechanically are accepted. Any other
    text is NOT silently ignored: the build is refused and names the prose,
    because a free-text prerequisite the engine cannot evaluate is exactly the
    "looks legal but is not enforced" sheet requirement 5 forbids.
    """
    text = (text or "").strip()
    if not text:
        return
    match = _REQUIREMENT_RE.match(text)
    if match is None:
        raise ValueError(
            f"feat {slug!r} has prerequisite {text!r} this engine cannot evaluate; "
            "refusing rather than granting it unchecked"
        )
    names = [n.strip().lower() for n in match.group("names").split(" or ")]
    need = int(match.group("need"))
    have = {
        name: scores[_REQUIREMENT_ABILITY[name]]
        for name in names
        if name in _REQUIREMENT_ABILITY
    }
    if not have:
        raise ValueError(
            f"feat {slug!r} has prerequisite {text!r} this engine cannot evaluate; "
            "refusing rather than granting it unchecked"
        )
    if max(have.values()) < need:
        detail = ", ".join(f"{n} {v}" for n, v in sorted(have.items()))
        raise ValueError(
            f"feat {slug!r} needs {need} in one of {sorted(have)}; got {detail} "
            f"(feat:{pick.class_slug}:{pick.level})"
        )

def _asi_level_feats(
    spec: CharacterBuildSpec,
    choices: ParsedChoices,
    class_docs: Mapping[str, Class],
    used: set[tuple[str, int]],
    owned: set[str],
    loader: AssetLoader,
    scores: Mapping[str, int],
) -> list[str]:
    feats: list[str] = []
    for pick in choices.feats:
        _asi_slot("feat", pick.class_slug, pick.level, spec, class_docs, used)
        if pick.feat_slug == "ability-score-improvement":
            raise ValueError(
                f"feat:{pick.class_slug}:{pick.level}:ability-score-improvement: use an "
                "asi:<class>:<level>:<ability>+<n> token to take Ability Score Improvement, "
                "not a feat: token"
            )
        feat = loader.get_feat(pick.feat_slug)
        if feat is None:
            raise ValueError(f"unknown feat: {pick.feat_slug!r}")
        # The highest character level the build can have had when it reached
        # this class level: every level it took afterwards was in another class.
        taken_at = spec.level - (spec.classes[pick.class_slug] - pick.level)
        if feat.category is FeatCategory.EPIC_BOON and taken_at < _EPIC_BOON_LEVEL:
            raise ValueError(
                f"feat {feat.slug!r} is an Epic Boon and needs character level "
                f"{_EPIC_BOON_LEVEL}; feat:{pick.class_slug}:{pick.level} is taken at "
                f"character level {taken_at} at most"
            )
        for prerequisite in feat.prerequisites:
            if prerequisite.level is not None and taken_at < prerequisite.level:
                raise ValueError(
                    f"feat {feat.slug!r} needs character level {prerequisite.level}; "
                    f"feat:{pick.class_slug}:{pick.level} is taken at character level "
                    f"{taken_at} at most"
                )
            missing = sorted(set(prerequisite.feats) - owned)
            if missing:
                raise ValueError(f"feat {feat.slug!r} needs {missing}")
            _check_requirement_prose(
                feat.slug, prerequisite.requirement, scores, pick,
            )
        feats.append(feat.slug)
    return feats


_ABILITY_NAME_BY_CODE_FOR_MC: Final = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}


def _check_multiclass_abilities(
    classes: Mapping[str, int],
    scores: Mapping[str, int],
    loader: AssetLoader,
    starting_class: str | None = None,
) -> None:
    """SRD 5.2 Multiclassing: to take a level in a second class you need 13 in
    each of its primary abilities -- all of them when the class lists several with
    ``all=True`` (Paladin: Strength and Charisma), any one of them otherwise
    (Fighter: Strength or Dexterity).

    Only the classes you multiclass INTO are gated; the class taken at level 1 is
    the starting class and imposes no ability floor. A single-class build is
    never checked, so the rule cannot fire on the ordinary path.

    ``starting_class`` names the class taken at level 1 and is REQUIRED to be
    authoritative: taking the first key of ``classes`` made the verdict depend on
    dict insertion order, so the same scores were accepted as a Fighter-first
    build and refused as a Wizard-first one. When it is omitted the first key is
    used only as a fallback for callers that predate the parameter.
    """
    if len(classes) < 2:
        return
    start = starting_class or next(iter(classes))
    for slug in classes:
        if slug == start:
            continue
        doc = loader.get_class(slug)
        if doc is None:
            raise ValueError(f"unknown class: {slug!r}")
        primary = getattr(doc, "primary_ability", None)
        codes = tuple(getattr(primary, "value", ()) or ())
        if not codes:
            continue
        needs_all = bool(getattr(primary, "all", False))
        names = [_ABILITY_NAME_BY_CODE_FOR_MC[c] for c in codes if c in _ABILITY_NAME_BY_CODE_FOR_MC]
        if not names:
            continue
        passed = [n for n in names if scores.get(n, 0) >= 13]
        if needs_all and len(passed) != len(names):
            short = sorted(set(names) - set(passed))
            raise ValueError(
                f"multiclass into {slug!r} needs 13 in {', '.join(names)}; "
                f"short on {', '.join(short)}"
            )
        if not needs_all and not passed:
            raise ValueError(
                f"multiclass into {slug!r} needs 13 in one of {', '.join(names)}"
            )


def _class_spell_quotas(
    class_docs: Mapping[str, Class], level: int, loader: AssetLoader
) -> dict[str, tuple[int, int]]:
    """{класс: (заговоров, подготовленных)} из ScaleValue корпуса.

    Квоты НЕ задаются в коде: `canonical/classes/<slug>.json` несёт
    `ScaleValue "Cantrips Known"` и `"Max Prepared Spells"`, и именно они
    считаются правилом. Класс без заклинаний в результат не попадает.
    """
    quotas: dict[str, tuple[int, int]] = {}
    for slug in class_docs:
        try:
            values = build_scale_values(
                class_slug=slug, subclass_slug=None, species_slug=None,
                level=level, loader=loader,
            )
        except Exception:
            continue
        cantrips = values.get(f"{slug}.cantrips-known")
        prepared = values.get(f"{slug}.max-prepared")
        if isinstance(cantrips, int) or isinstance(prepared, int):
            quotas[slug] = (
                int(cantrips) if isinstance(cantrips, int) else 0,
                int(prepared) if isinstance(prepared, int) else 0,
            )
    return quotas


def _class_spells(
    picks: Sequence[str],
    quotas: Mapping[str, tuple[int, int]],
    *,
    strict: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Заговоры и подготовленные заклинания из токенов `cantrip:` / `spell:`.

    Токен адресует класс через `@<class>`; без него — первый заклинающий
    класс сборки. Квота берётся из корпуса. Превышение отвергается всегда,
    недобор — только на завершённом листе (черновик строится по шагам).
    """
    cantrips = tuple(s for s in picks if s.startswith("cantrip:"))
    spells = tuple(s for s in picks if s.startswith("spell:"))
    for label, chosen in (("заговор", cantrips), ("заклинание", spells)):
        cap_index = 0 if label == "заговор" else 1
        by_class: dict[str, int] = {}
        for token in chosen:
            body = token.split(":", 1)[1]
            slug, _, klass = body.partition("@")
            if klass:
                if klass not in quotas:
                    raise ValueError(
                        f"{label} {slug!r}: у класса {klass!r} нет квоты"
                        f" заклинаний; доступны {sorted(quotas)}"
                    )
                by_class[klass] = by_class.get(klass, 0) + 1
            else:
                first = next(iter(quotas), None)
                if first is None:
                    raise ValueError(
                        f"{label} {slug!r} взят, но ни один класс сборки"
                        " не заклинает"
                    )
                by_class[first] = by_class.get(first, 0) + 1
        for klass, count in by_class.items():
            cap = quotas[klass][cap_index]
            if count > cap:
                raise ValueError(
                    f"{klass}: {label}ов взято {count}, а правила корпуса"
                    f" дают {cap}"
                )
        total = sum(by_class.values())
        granted = sum(q[cap_index] for q in quotas.values())
        if strict and total < granted:
            raise ValueError(
                f"{label}ов взято {total}, а правила дают {granted}."
                " Назови недостающие выборы (cantrip:/spell:)"
            )
    return cantrips, spells




# SRD 5.2 §Spellcasting — способность заклинаний ВЛАДЕЛЬЦА заклинания.
#
# Выбор токена `spell:x@wizard` уже проверялся квотой класса, но сам слаг
# до листа не доходил: `cantrips`/`prepared_spells` оставались токенами,
# `spell_abilities` наполнялся только Magic Initiate, и `build_party_member`
# (он строит `spells_known` из `sheet.spell_abilities`) выдавал заклинающему
# персонажу ПУСТОЙ список. Следствие: либо каст по корпусу без всякого
# ограничения, либо (после гейта `spell_not_available`) заклинающий не мог
# кастовать НИЧЕГО. Список читается из источника, а не из кода.
def _spell_pick_slug(token: str) -> str:
    """Слаг из токена выбора: `spell:magic-missile@wizard` -> `magic-missile`."""
    return token.split(":", 1)[1].partition("@")[0]


def _class_spell_abilities(
    picks: Sequence[str],
    class_docs: Mapping[str, Class],
    loader: AssetLoader,
) -> dict[str, SpellcastingAbility]:
    """Каждое выбранное заклинание получает способность СВОЕГО владельца.

    Класс владельца — суффикс `@<class>` токена; без него заклинание
    принадлежит первому заклинающему классу сборки, ровно как в
    `_class_spells`. Неизвестный слаг способности не сочиняет.

    «Первый» — это порядок `classes`, а НЕ алфавит: `sorted()` давал
    волшебник/жрецу `cleric` как класс по умолчанию, и все волшебничьи
    заклинания объявлялись мудростью (`wis`) вместо интеллекта (`int`).
    `class_docs` — упорядоченный словарь сборки, поэтому `next(iter(...))`
    и есть порядок взятия классов.
    """
    default = next(
        (
            slug
            for slug in class_docs
            if class_docs[slug].spellcasting.ability
        ),
        None,
    )
    abilities: dict[str, SpellcastingAbility] = {}
    for token in picks:
        body = token.split(":", 1)[1]
        slug, _, klass = body.partition("@")
        owner = class_docs.get(klass) if klass else (
            class_docs.get(default) if default is not None else None
        )
        if owner is None:
            continue
        ability = owner.spellcasting.ability
        if not ability:
            continue
        if loader.get_spell(slug) is None:
            continue
        abilities.setdefault(slug, ability)
    return abilities


def _spell_slugs_from_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    return tuple(_spell_pick_slug(token) for token in tokens)

def _check_repeats(feats: Sequence[str], loader: AssetLoader) -> None:
    """SRD 5.2: "A feat can be taken only once unless its description states
    otherwise in a "Repeatable" subsection" (``Feat.repeatable``)."""
    for slug in sorted({slug for slug in feats if feats.count(slug) > 1}):
        feat = loader.get_feat(slug)
        if feat is None or not feat.repeatable:
            raise ValueError(
                f"feat {slug!r} is not repeatable; the build takes it {feats.count(slug)} times"
            )


def _magic_initiate_spells(
    picks: Sequence[MagicInitiatePick], feats: Sequence[str], loader: AssetLoader
) -> tuple[dict[str, SpellcastingAbility], tuple[str, ...]]:
    """SRD 5.2 Magic Initiate: "You learn two cantrips of your choice ...
    Intelligence, Wisdom, or Charisma is your spellcasting ability for this
    feat's spells"; "Choose a level 1 spell ... You can cast it once without a
    spell slot". One ``magic-initiate:`` token per Magic Initiate the build
    takes; one without a token has no spells chosen yet. Returns each chosen
    spell's ability and the level 1 spells cast without a slot. Which list a
    spell is on is not checked: no corpus entry carries spell lists."""
    taken = feats.count(_MAGIC_INITIATE)
    if len(picks) > taken:
        raise ValueError(
            f"selected_choices has {len(picks)} magic-initiate: tokens but the build "
            f"takes Magic Initiate {taken} times"
        )
    abilities: dict[str, SpellcastingAbility] = {}
    for pick in picks:
        for slug, level in ((pick.cantrips[0], 0), (pick.cantrips[1], 0), (pick.spell, 1)):
            spell = loader.get_spell(slug)
            if spell is None:
                raise ValueError(f"unknown spell: {slug!r}")
            if spell.level != level:
                want = "a cantrip" if level == 0 else "a level 1 spell"
                got = "a cantrip" if spell.level == 0 else f"a level {spell.level} spell"
                raise ValueError(f"Magic Initiate: {slug!r} is {got}, not {want}")
            abilities[slug] = pick.ability
    return abilities, tuple(pick.spell for pick in picks)


@dataclass(frozen=True)
class _Worn:
    body: Armor | None
    body_bonus: int
    shield: Armor | None
    shield_bonus: int


# ---------------------------------------------------------------------------
# Attunement restrictions.
#
# SRD 5.2 §Attunement: a magic item's "Requires Attunement by ..." line names
# who may attune to it. The corpus keeps that line verbatim in
# Item.attunement_constraint, so the check PARSES the line instead of trusting
# a hand-kept table: a re-import that rewords an entry must not silently switch
# the restriction off, and a token the parser cannot model must block rather
# than pass.
# ---------------------------------------------------------------------------

# The SRD 5.2 classes by the corpus slug (the only legal "by a <Class>" tokens).
_SRD_CLASS_SLUGS: Final[frozenset[str]] = frozenset(
    {
        "barbarian",
        "bard",
        "cleric",
        "druid",
        "fighter",
        "monk",
        "paladin",
        "ranger",
        "rogue",
        "sorcerer",
        "warlock",
        "wizard",
    }
)

# SRD 5.2 species names as the corpus prints them (Species.name before the 2024
# "Elf, High" comma). A "by a Dwarf" line names a species.
_SPECIES_NAMES: Final[frozenset[str]] = frozenset(
    {
        "dragonborn",
        "dwarf",
        "elf",
        "gnome",
        "goliath",
        "halfling",
        "human",
        "orc",
        "tiefling",
    }
)

# Spellcasting progressions that grant the Spellcasting or Pact Magic feature --
# the two named features the SRD's "Spellcaster" refers to.
_CASTER_PROGRESSIONS: Final[frozenset[str]] = frozenset(
    {"full", "half", "third", "pact", "artificer"}
)

_ATTUNEMENT_PREFIX = "by a "

# Where a Foundry @UUID[...]{Label} link was cut off during import. The corpus's
# Dwarven Thrower stores exactly
# "by a Dwarf or a Creature Attuned to a @UUID[Compendium".
_UUID_CUT = re.compile(r"@UUID\[[^\]]*\]?")
# What that cut-off link collapses to. It names an item the corpus no longer identifies, so it can never match an equipment name.
_UUID_CUT_PLACEHOLDER = "[item]"

# Clause separator: ", ", ", or ", " or ".
_ATTUNEMENT_SPLIT = re.compile(r",\s*(?:or\s+)?|\s+or\s+", re.IGNORECASE)


@dataclass(frozen=True)
class _AttunementRestriction:
    """One parsed Item.attunement_constraint.

    Clauses are OR-ed together (the SRD prints one list per item). unparsed
    holds a clause the parser does not model -- it never grants access, so an
    unrecognised word blocks the build instead of being ignored. An item named
    in item_names (the corpus's "@UUID" case, whose label the import lost) is
    satisfied only when that item's name appears among the build's other
    equipment.
    """

    text: str
    class_slugs: tuple[str, ...] = ()
    spellcaster: bool = False
    species: tuple[str, ...] = ()
    item_names: tuple[str, ...] = ()
    unparsed: str | None = None


def parse_attunement_constraint(item_slug: str, text: str) -> _AttunementRestriction:
    """Parse a "by a ..." attunement line into its modelled clauses.

    Shapes the SRD 5.2 corpus prints, all handled here:

    * a single class -- "by a Wizard", "by a Paladin", "by a Druid";
    * a class list -- "by a Cleric, Druid, or Paladin", "by a Bard, Cleric, or
      Druid", "by a Sorcerer, Warlock, or Wizard", "by a Bard, Cleric, Druid,
      Sorcerer, Warlock, or Wizard", "by a Druid, Sorcerer, Warlock, or Wizard",
      "by a Cleric or Paladin";
    * any spellcasting class -- "by a Spellcaster";
    * a species -- "by a Dwarf";
    * a species or another item -- "by a Dwarf or a Creature Attuned to a Belt
      of Dwarvenkind". The corpus stores only "by a Dwarf or a Creature
      Attuned to a @UUID[Compendium": the referenced Belt's label was cut off in
      import, so the clause is kept as "another named item in equipment" and is
      never guessed from the UUID fragment.

    Raises ValueError for a line that does not start with "by a ..." -- the
    corpus has no such entry, so it is a translation bug, not a rule.
    """
    body = text.strip()
    # "[item]" rather than "<item>": the placeholder must not swallow the "a"
    # article a "Creature Attuned to a @UUID[...]" clause leaves in front of it.
    collapsed = _UUID_CUT.sub("[item]", body)
    lowered = body.lower()
    if lowered.startswith(_ATTUNEMENT_PREFIX):
        body = collapsed.strip()[len(_ATTUNEMENT_PREFIX) :]
    elif lowered.startswith("by "):
        body = collapsed.strip()[3:]
    else:
        raise ValueError(
            f"attunement constraint for {item_slug!r} ({text!r}) is not a 'by a ...' line"
        )
    classes: list[str] = []
    species: list[str] = []
    item_names: list[str] = []
    unparsed: str | None = None
    for part in _ATTUNEMENT_SPLIT.split(body):
        clause = part.strip(" .")
        if not clause:
            continue
        token = clause.lower()
        for article in ("a ", "an ", "the "):
            if token.startswith(article):
                token = token[len(article) :]
                break
        if token == "spellcaster":
            continue  # recorded on the restriction itself
        if token.startswith("creature attuned to "):
            # The reference the clause names, not the article-stripped token:
            # clause reads "a Creature Attuned to a Belt of ..." / "... to a <item>".
            referenced = clause[len(clause) - len(token) + len("creature attuned to ") :]
            for article in ("a ", "an "):
                if referenced.lower().startswith(article):
                    referenced = referenced[len(article) :]
                    break
            item_names.append(referenced.strip(" :<>. "))
            continue
        if token in _SRD_CLASS_SLUGS:
            classes.append(token)
        elif token in _SPECIES_NAMES:
            species.append(token)
        else:
            unparsed = f"{unparsed}; {clause}" if unparsed else clause
    return _AttunementRestriction(
        text=collapsed.strip(),
        class_slugs=tuple(dict.fromkeys(classes)),
        spellcaster="spellcaster" in collapsed.lower(),
        species=tuple(dict.fromkeys(species)),
        item_names=tuple(item_names),
        unparsed=unparsed,
    )


def _spellcasting_classes(spec: CharacterBuildSpec, loader: AssetLoader) -> tuple[str, ...]:
    """Classes whose SRD 5.2 progression casts spells (Spellcasting or Pact Magic)."""
    casting: list[str] = []
    for slug in spec.classes:
        cls = loader.get_class(slug)
        if cls is not None and str(cls.spellcasting.progression) in _CASTER_PROGRESSIONS:
            casting.append(slug)
    return tuple(casting)


def _species_bases(slug: str, loader: AssetLoader) -> tuple[str, ...]:
    """The names a build's species answers to: the printed name and, for a 2024
    lineage leaf ("Elf, High"), the base name a "by an Elf" line would use."""
    species = loader.get_species(slug)
    if species is None:
        return ()
    name = str(species.name)
    base = name.split(",", 1)[0].strip()
    return tuple(dict.fromkeys(part.lower() for part in (name, base) if part))


def _article(word: str) -> str:
    """The indefinite article a species/class name takes, so a refusal reads
    as English rather than as a template."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _check_attunement_constraint(
    slug: str,
    item: Any,
    spec: CharacterBuildSpec,
    loader: AssetLoader,
    equipment_names: Mapping[str, str] | None = None,
) -> None:
    """Refuse an attunement the item's "Requires Attunement by ..." line forbids.

    ``equipment_names`` maps the build's OTHER equipment slugs to their printed
    names; ``_attuned`` passes it so a "by a Dwarf or a Creature Attuned to a
    Belt of Dwarvenkind" clause can find the referenced item. Omitted, that
    clause simply finds nothing (it can only ever grant access, never deny it).
    """
    raw = getattr(item, "attunement_constraint", None)
    if not raw:
        return
    restriction = parse_attunement_constraint(slug, str(raw))
    has_clause = bool(
        restriction.class_slugs
        or restriction.spellcaster
        or restriction.species
        or restriction.item_names
    )
    held = {name.lower() for name in (equipment_names or {}).values()}
    named = tuple(
        name for name in restriction.item_names if name and name.lower() != _UUID_CUT_PLACEHOLDER
    )
    allowed = False
    reasons: list[str] = []
    if restriction.class_slugs:
        wanted = " or ".join(sorted(restriction.class_slugs))
        reasons.append(f"{_article(wanted)} {wanted}" + ("" if set(spec.classes) & set(restriction.class_slugs)
                                         else " -- this build is " + ", ".join(sorted(spec.classes))))
        if set(spec.classes) & set(restriction.class_slugs):
            allowed = True
    if restriction.spellcaster:
        casting = _spellcasting_classes(spec, loader)
        reasons.append(
            (
                f"a spellcasting class ({', '.join(casting)})"
                if casting
                else "a spellcasting class -- this build casts no spells"
            )
        )
        if casting:
            allowed = True
    if restriction.species:
        wanted = " or ".join(sorted(restriction.species))
        reasons.append(
            f"{_article(wanted)} {wanted}"
            if set(_species_bases(spec.species_slug, loader)) & set(restriction.species)
            else f"{_article(wanted)} {wanted} -- this build is {spec.species_slug!r}"
        )
        if set(_species_bases(spec.species_slug, loader)) & set(restriction.species):
            allowed = True
    if restriction.item_names:
        reasons.append(
            "another attuned item ("
            + (", ".join(named) if named else "name lost in import, @UUID reference")
            + ") in equipment"
        )
        if any(name.lower() in held for name in named):
            allowed = True
    if restriction.unparsed:
        reasons.append(f"unmodelled clause {restriction.unparsed!r}")
    if restriction.unparsed is None and allowed:
        return
    if not has_clause and restriction.unparsed is None:
        raise ValueError(
            f"attunement constraint for {slug!r} ({raw!r}) names no class, species or item"
        )
    raise ValueError(
        f"{slug!r} requires attunement {restriction.text!r}; the build does not "
        f"qualify (needs {'; '.join(reasons)})"
    )


def _attuned(
    spec: CharacterBuildSpec, loader: AssetLoader, changes: Sequence[PassiveEffectChange]
) -> frozenset[str]:
    """The items the build actually attunes.

    SRD 5.2 §Attunement: the maximum is three (class features may raise it via
    attunement_limit), an item must be carried in equipment and must "Require
    Attunement", and its printed "Requires Attunement by ..." line restricts who
    may attune. The last is checked against the build's classes, its
    spellcasting and its species (_check_attunement_constraint).
    """
    attuned = spec.attuned_items
    if len(set(attuned)) != len(attuned):
        raise ValueError(f"attuned_items repeats an item: {sorted(attuned)}")
    limit = attunement_limit(changes)
    if len(attuned) > limit:
        raise ValueError(
            f"attuned_items has {len(attuned)} items; SRD 5.2 allows no more than {limit}"
        )
    equipment_names = {
        slug: str(doc.name)
        for slug in spec.equipment
        if (doc := loader.get_item(slug)) is not None and slug not in attuned
    }
    for slug in attuned:
        if slug not in spec.equipment:
            raise ValueError(f"attuned item {slug!r} is not in equipment")
        item = loader.get_item(slug)
        if item is None:
            raise ValueError(f"unknown item: {slug!r}")
        if not item.requires_attunement:
            raise ValueError(f"{slug!r} does not require attunement")
        _check_attunement_constraint(slug, item, spec, loader)
    return frozenset(attuned)



def _worn(
    spec: CharacterBuildSpec,
    loader: AssetLoader,
    training: frozenset[ArmorTraining],
    changes: Sequence[PassiveEffectChange],
) -> _Worn:
    """Worn body armor and wielded Shield. SRD 5.2: "A creature can wear only one
    suit of armor at a time and wield only one Shield at a time"; "You gain the
    Armor Class benefit of a Shield only if you have training with it". A magic
    bonus counts once the item is attuned, when it requires attunement."""
    attuned = _attuned(spec, loader, changes)
    body: list[Armor] = []
    shields: list[Armor] = []
    for slug in spec.equipment:
        armor = loader.get_armor(slug)
        if armor is not None:
            (shields if armor.armor_category == "shield" else body).append(armor)
    if len(body) > 1:
        raise ValueError(f"equipment wears more than one suit of armor: {[a.slug for a in body]}")
    if len(shields) > 1:
        raise ValueError(f"equipment wields more than one Shield: {[a.slug for a in shields]}")

    def bonus(armor: Armor) -> int:
        active = not armor.requires_attunement or armor.slug in attuned
        return armor.magical_bonus if active else 0

    worn_body = body[0] if body else None
    shield = shields[0] if shields else None
    shield_bonus = shield.base_ac + bonus(shield) if shield and "shield" in training else 0
    return _Worn(worn_body, bonus(worn_body) if worn_body else 0, shield, shield_bonus)


def _ac_mode(
    explicit: AcCalcMode | None,
    changes: Sequence[PassiveEffectChange],
    modifiers: Mapping[AbilityName, int],
    worn: _Worn,
    item_bonus: int = 0,
    custom_formula: str | None = None,
) -> AcCalcMode:
    wearing, shielded = worn.body is not None, worn.shield is not None
    if explicit is not None:
        if not ac_mode_eligible(explicit, wearing_armor=wearing, wielding_shield=shielded):
            raise ValueError(
                f"ac_calc_mode {explicit!r} does not apply with the worn equipment "
                f"({[a.slug for a in (worn.body, worn.shield) if a]})"
            )
        return explicit
    eligible = sorted(
        mode
        for mode in ac_modes_from_changes(changes)
        if ac_mode_eligible(mode, wearing_armor=wearing, wielding_shield=shielded)
    )
    return max(
        eligible,
        key=lambda mode: armor_class(
            mode,
            modifiers,
            body_armor=worn.body,
            body_armor_bonus=worn.body_bonus,
            shield_bonus=worn.shield_bonus,
            item_bonus=item_bonus,
            custom_formula=custom_formula,
        ),
    )


def available_choices(spec: CharacterBuildSpec, *, loader: AssetLoader) -> dict[str, str]:
    """Every option the build can still pick from, keyed by slug → ref type.

    SRD 5.2 §Character Creation / §Level Advancement: the features that hand the
    player a choice (a Fighting Style, a weapon mastery, an Eldritch Invocation)
    only offer a pool once the character has reached the level that opens it.
    A host that renders a builder needs that list BEFORE it commits anything, so
    this answers the question without building a sheet.

    Purely a view: it reads the corpus through ``loader`` and changes nothing —
    no resource is spent and no randomness is drawn (§11: «просмотр вариантов не
    расходует ресурсы и не меняет случайность»). The pool is opened by the same
    ``_choice_options`` the sheet derivation uses, so the list a player is shown
    and the list their picks are validated against cannot disagree.

    ABILITY SCORE IMPROVEMENTS and FEATS are NOT in this map: they are
    ``AbilityScoreImprovement`` advancement records, not feature-choice pools,
    and they are validated through the ``asi:``/``feat:`` token stream instead.
    """
    class_docs = _class_docs(spec.classes, loader)
    species = _species(spec.species_slug, loader)
    subclass, subclass_level = _subclass(spec, class_docs, loader)
    level_sources: list[_LeveledSource] = [
        *((class_docs[slug], level) for slug, level in spec.classes.items()),
        (subclass, subclass_level),
        (species, spec.level),
    ]
    return _choice_options(level_sources)

def derive_sheet(spec: CharacterBuildSpec, *, loader: AssetLoader) -> DerivedSheet:
    """Derive the character sheet a ``CharacterBuildSpec`` describes (SRD 5.2
    Character Creation, Level Advancement and Multiclassing).

    Pure apart from ``loader`` reads; draws no dice. Raises ``ValueError`` for an
    unknown slug or a build the SRD forbids.
    """
    choices = parse_selected_choices(spec.selected_choices)
    class_docs = _class_docs(spec.classes, loader)
    species = _species(spec.species_slug, loader)
    subclass, subclass_level = _subclass(spec, class_docs, loader)
    background = _background(spec.background_slug, loader)
    level_sources: list[_LeveledSource] = [
        *((class_docs[slug], level) for slug, level in spec.classes.items()),
        (subclass, subclass_level),
        (species, spec.level),
    ]
    # Weapon mastery is a CHOSEN kind (SRD 5.2), not a property of the weapon:
    # a Fighter picks three weapons at level 1. Those picks travel in the same
    # selected_choices stream as feats and features, so split them out first;
    # treating them as features meant every hero earned no mastery at all.
    masteries = tuple(slug for slug in choices.picks if loader.get_weapon(slug) is not None)
    ordinary = tuple(slug for slug in choices.picks if slug not in masteries)
    _check_choice_quotas(
        ordinary, level_sources, _choice_options(level_sources),
        strict=spec.require_complete_choices,
    )
    # cantrip:/spell: — не пики из пула способностей, их сверяет
    # _class_spells ниже; _split_picks о них знать не должен.
    spells_only = tuple(
        s for s in ordinary if s.startswith(("cantrip:", "spell:"))
    )
    ordinary = tuple(s for s in ordinary if s not in spells_only)
    picked_features, picked_feats = _split_picks(ordinary, _choice_options(level_sources))
    quotas = _class_spell_quotas(class_docs, spec.level, loader)
    picks_with_spells = (*ordinary, *spells_only)
    cantrips, prepared_spells = _class_spells(
        picks_with_spells, quotas, strict=spec.require_complete_choices,
    )
    features = list(dict.fromkeys([*leveled_feature_slugs(level_sources), *picked_features]))
    _granted = leveled_feature_slugs(level_sources)
    chosen_features = tuple(dict.fromkeys(p for p in picked_features if p not in _granted))
    # A character's passive changes come from two places: the always-on effects of
    # its features and those of its own worn/attuned equipment (SRD 5.2 Magic
    # Items). Both feed the same downstream interpretation of AC, training,
    # resistances and senses.
    changes = _always_on_changes(features, loader) + _item_always_on_changes(spec, loader)
    used_asi_slots: set[tuple[str, int]] = set()
    scores = _ability_scores(spec, choices, class_docs, background, used_asi_slots, changes)
    origin_feats = [*_background_feats(background, loader), *picked_feats]
    feats = [
        *origin_feats,
        *_asi_level_feats(
            spec, choices, class_docs, used_asi_slots, {*features, *origin_feats}, loader,
            scores,
        ),
    ]
    _check_repeats(feats, loader)
    # Требование 5: стартовый класс берётся из явного решения игрока.
    # Резервный порядок — `spec.class_slug` (одноклассовые листы), затем
    # первый ключ `classes`; последнее делает вердикт зависимым от порядка
    # ключей, поэтому поле `starting_class` имеет приоритет.
    _check_multiclass_abilities(
        spec.classes, scores, loader,
        spec.starting_class or spec.class_slug or None,
    )
    spell_abilities, slotless_casts = _magic_initiate_spells(choices.magic_initiate, feats, loader)
    # Токены выбора уже проверены квотами; здесь они получают ВЛАДЕЛЬЦА, без
    # которого лист не может сказать, чем персонаж заклинает свои заклинания.
    for slug, ability in _class_spell_abilities(
        (*cantrips, *prepared_spells), class_docs, loader
    ).items():
        spell_abilities.setdefault(slug, ability)
    modifiers = {name: ability_modifier(score) for name, score in scores.items()}
    pb = proficiency_bonus(spec.level)
    grants = proficiency_grants(
        [
            *(
                (class_docs[slug], level, "primary" if index == 0 else "secondary")
                for index, (slug, level) in enumerate(spec.classes.items())
            ),
            (subclass, subclass_level, None),
            (species, spec.level, None),
        ]
    )
    training = grants.armor | armor_training_from_changes(changes)
    worn = _worn(spec, loader, training, changes)
    # A worn magic item's flat "bonus to Armor Class" (Cloak of Protection, Ring
    # of Protection, Bracers of Defense, Robe of the Archmagi) is mode 2 on
    # system.attributes.ac.bonus. It stacks on whatever the AC mode computed.
    ac_item_bonus = sum(
        literal
        for change in changes
        if change.key == _AC_BONUS_KEY
        and change.mode == _ITEM_MODE_ADD
        and (literal := _literal_int(change.value)) is not None
    )
    # Foundry's "custom" AC mode carries its formula in a companion change: the
    # Robe of the Archmagi sets ac.calc=custom with "15 + @abilities.dex.mod".
    ac_custom_formula = next(
        (
            str(change.value).strip().strip('"')
            for change in changes
            if change.key == _AC_FORMULA_KEY
        ),
        None,
    )
    mode = _ac_mode(
        spec.ac_calc_mode, changes, modifiers, worn, ac_item_bonus, ac_custom_formula
    )
    skills, expertise = _skills(grants, background, choices)
    # SRD 5.2 §Expertise: the corpus states HOW MANY skills a class upgrades at
    # a given level (Rogue 1 -> 2, Rogue 6 -> 4, Bard 10 -> 4). Without this
    # check a build could claim Expertise in four skills at level 1, and the
    # quota the book publishes was never consulted (требование 5: каждый
    # предоставляемый выбор либо работает, либо имеет объяснимый запрет).
    _quota = 0
    for _slug, _cls in class_docs.items():
        _lv = spec.classes.get(_slug, 0)
        _quota += _expertise_quota(getattr(_cls, "advancement", ()), _lv)
    if len(expertise) > _quota:
        raise ValueError(
            f"Expertise in {len(expertise)} skills exceeds the {_quota} this "
            "build may choose at these levels"
        )
    if expertise and _quota and not expertise <= skills:
        raise ValueError("Expertise must be in skills the build is proficient with")
    jack = has_flag(changes, "flags.dnd5e.jackOfAllTrades")
    # Disciplined Survivor (Monk 14): "Your physical and mental discipline
    # grant you proficiency in all saving throws."
    save_proficiencies = (
        frozenset(ABILITY_NAME_BY_CODE)
        if has_flag(changes, "flags.dnd5e.diamondSoul")
        else grants.saves
    )
    die_sizes = {slug: hit_die_size(str(cls.hit_die)) for slug, cls in class_docs.items()}
    walk = species.movement.walk or 30
    # A chosen resistance travels in the same dr:/di: spelling the species
    # grants use, so the one interpreter that already knows that spelling
    # applies it (Draconic Sorcery Elemental Affinity picks one type).
    chosen_grants = tuple(f"dr:{slug}" for slug in choices.resistances) + tuple(
        f"di:{slug}" for slug in choices.immunities
    )
    passive = interpret_passive_stats(
        changes=changes,
        trait_grants=(*species.trait_grants, *chosen_grants),
        species_senses=species.senses,
        species_base_speed=walk,
    )
    if passive.skipped_keys:
        _log.debug(
            "derive_sheet: skipped non-allowlisted passive keys",
            extra={"skipped_keys": passive.skipped_keys},
        )
    return DerivedSheet(
        ability_scores=AbilityScores(**scores),
        ability_modifiers=modifiers,
        proficiency_bonus=pb,
        # Boots of Speed double the walk speed (MULTIPLY on it); the flat
        # bonus adds first, then the armour penalty applies -- a factor
        # never makes heavy armour lighter.
        base_speed=(
            (walk + passive.walk_speed_bonus) * passive.walk_speed_multiplier
            - armor_speed_penalty(worn.body, scores["strength"])
        ),
        movement_modes=passive.movement_modes,
        ignored_difficult_terrain=passive.ignored_difficult_terrain,
        # Backgrounds grant Languages outright (corpus `languages`); item effects
        # contribute theirs through `passive`. Merged, first occurrence wins.
        # Three sources the corpus keeps apart: item effects (passive.languages),
        # the background (backgrounds/*.json languages), and a class Trait
        # (advancement[type=Trait].configuration.grants, "languages:exotic:druidic").
        languages=tuple(
            dict.fromkeys(
                passive.languages
                + _background_languages(background)
                + tuple(
                    name
                    for slug, lvl in spec.classes.items()
                    for name in _trait_languages(getattr(class_docs[slug], "advancement", ()), lvl)
                )
            )
        ),
        initiative_advantage=passive.initiative_advantage,
        senses=passive.senses,
        damage_resistances=passive.resistances,
        damage_immunities=passive.immunities,
        condition_immunities=passive.condition_immunities,
        save_proficiencies=save_proficiencies,
        skill_proficiencies=skills,
        skill_expertise=expertise,
        # SRD 5.2 Backgrounds: Tool Proficiencies, in the corpus's own codes.
        # A "game:*" / "art:*" leaves WHICH tool to the player; the engine must
        # not pick one for them.
        tool_proficiencies=_background_tools(background),
        weapon_proficiencies=grants.weapons | weapon_proficiencies_from_changes(changes),
        # The weapons whose mastery THIS character chose. Proficiency says what
        # they can swing; mastery says which special property they may invoke.
        weapon_masteries=frozenset(masteries),
        armor_training=training,
        passive_perception=passive_perception(
            scores["wisdom"],
            "perception" in skills,
            pb,
            expertise="perception" in expertise,
            jack_of_all_trades=jack,
        ),
        jack_of_all_trades=jack,
        reliable_talent=has_flag(changes, "flags.dnd5e.reliableTalent"),
        stealth_disadvantage=bool(worn.body and worn.body.stealth_disadvantage),
        extra_attack_count=extra_attack_count(features),
        rules_edition=spec.rules_edition,
        features=tuple(features),
        chosen_features=chosen_features,
        feats=tuple(feats),
        spell_abilities=spell_abilities,
        cantrips=_spell_slugs_from_tokens(cantrips),
        prepared_spells=_spell_slugs_from_tokens(prepared_spells),
        slotless_casts=slotless_casts,
        spell_slots=derive_multiclass_slots(spec.classes, loader=loader),
    starting_equipment=_background_equipment(spec.background_slug, loader),
    wealth_gp=_background_wealth(spec.background_slug, loader),
        pact_slots=derive_multiclass_pact_slots(spec.classes, loader=loader),
        hp_max=_hit_points(spec, die_sizes, modifiers["constitution"], changes),
        hit_dice=hit_dice_pool(spec.classes, die_sizes),
        ac=armor_class(
            mode,
            modifiers,
            body_armor=worn.body,
            body_armor_bonus=worn.body_bonus,
            shield_bonus=worn.shield_bonus,
            item_bonus=ac_item_bonus,
            custom_formula=ac_custom_formula,
        )
        + defense_ac_bonus(feats, wearing_armor=worn.body is not None),
        ac_calc_mode=mode,
    )


def _background_equipment(
    background_slug: str | None, loader: AssetLoader,
) -> tuple[tuple[str, int], ...]:
    """Стартовое снаряжение предыстории как `((slug, count), ...)`.

    Разрешение ключей Foundry — в `starting_equipment.resolve_starting_key`.
    Предыстории без записи дают пустой набор: движок не выдумывает предметы
    там, где источник их не даёт (п.2).
    """
    if not background_slug:
        return ()
    from dnd5e_engine.starting_equipment import resolve_background_equipment

    loadout = resolve_background_equipment(background_slug)
    return tuple((item.slug, item.count) for item in loadout.items)


def _background_wealth(background_slug: str | None, loader: AssetLoader) -> int:
    """Золото предыстории из её записи `wealth`; 0, если записи нет."""
    if not background_slug:
        return 0
    from dnd5e_engine.starting_equipment import resolve_background_equipment

    return resolve_background_equipment(background_slug).wealth_gp


__all__ = [
    "AbilityScores",
    "CharacterBuildSpec",
    "CombatInstance",
    "DerivedSheet",
    "derive_multiclass_pact_slots",
    "derive_multiclass_slots",
    "derive_sheet",
    "derive_spell_slots",
    "make_build_spec",
]
