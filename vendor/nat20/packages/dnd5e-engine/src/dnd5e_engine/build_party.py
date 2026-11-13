"""Pure resolution of a CharacterBuildSpec into a complete PartyMemberSpec.

Character values (abilities, HP, AC, speed, proficiencies, senses, resistances)
come from derive_sheet; combat-instance values (identity, and anything the host
pinned on CombatInstance) override it. See build_party_member.

A worn magic item also lends its passive effects to the FIGHT, which the sheet does
not carry: the to-hit bonus of a magic weapon, the saving-throw bonus of a Cloak of
Protection. project_item_effects translates those into ordinary ActiveEffect
documents the host feeds to start_combat like any other effect -- not a second
mechanism, the one the engine already reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from dnd5e_srd_data.loader import AssetLoader

from dnd5e_engine.build_spec import CharacterBuildSpec, CombatInstance, derive_sheet
from dnd5e_engine.build_spec import _pick_item_variant
from dnd5e_engine.specs import PartyMemberSpec
from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange

# Foundry CONST.ACTIVE_EFFECT_MODES -> this engine's ActiveEffectChange vocabulary.
_ITEM_MODES: Final[dict[int, str]] = {
    0: "custom",
    1: "multiply",
    2: "add",
    3: "downgrade",
    4: "upgrade",
    5: "override",
}

# Keys that rewrite the ITEM document (its own name, image, price, rarity) rather
# than the creature wearing it. They never describe a modifier on the bearer.
_ITEM_DOCUMENT_PREFIXES: Final[tuple[str, ...]] = (
    "name",
    "img",
    "system.properties",
    "system.rarity",
    "system.price",
    "system.description",
    "system.quantity",
    "system.attunement",
    "system.identified",
)

def build_party_member(
    build_spec: CharacterBuildSpec, instance: CombatInstance, *, loader: AssetLoader
) -> PartyMemberSpec:
    """Resolve a build spec plus a combat instance into a ``PartyMemberSpec``.

    Every character value comes from ``derive_sheet`` unless the host pinned
    it on ``instance``: ``hp_max`` / ``hp_current`` / ``ac`` / ``attack_bonus``
    / ``base_speed`` whenever they are not ``None`` (decided by an ordinary
    ``is None`` check, so a ``CombatInstance`` rebuilt from
    ``CombatInstance(**inst.model_dump())`` still derives whatever it left
    unset). An unpinned ``attack_bonus`` stays unset on the built spec, so
    the engine computes each weapon's to-hit bonus. ``instance.initiative``
    passes through as given: ``None`` has ``start_combat`` roll it from the
    derived Dexterity, with Alert's bonus. The spells Magic Initiate
    chose join ``instance.spells_known``, with the ability each is cast with and
    the level 1 spells cast once without a slot. Raises ``ValueError`` for an
    invalid build (see ``derive_sheet``).
    """
    sheet = derive_sheet(build_spec, loader=loader)
    hp_max = sheet.hp_max if instance.hp_max is None else instance.hp_max
    scores = sheet.ability_scores
    member = PartyMemberSpec(
        entity_id=instance.entity_id,
        name=instance.name,
        initiative=instance.initiative,
        hp_current=hp_max if instance.hp_current is None else instance.hp_current,
        hp_max=hp_max,
        ac=sheet.ac if instance.ac is None else instance.ac,
        strength=scores.strength,
        dexterity=scores.dexterity,
        constitution=scores.constitution,
        intelligence=scores.intelligence,
        wisdom=scores.wisdom,
        charisma=scores.charisma,
        zone_id=instance.zone_id,
        spell_slots=dict(instance.spell_slots) or dict(sheet.spell_slots),
        pact_slots=dict(instance.pact_slots) or dict(sheet.pact_slots),
        # `instance.spells_known` — ЯВНЫЙ выбор хоста (подготовленные из
        # `definition.prepared_spells`, выбор Magic Initiate). Лист уже несёт
        # заклинания, которые дают ПРАВИЛА (токены cantrip:/spell: сборки),
        # и объединять их здесь — значит спорить с решением хоста: волшебник,
        # прокачанный до уровня 1, кастовал бы заговоры, которых он не
        # выбирал, а мультиклассирующий боец получил бы весь список класса.
        # Пустой явный список означает «книгу заклинаний не передали»; тогда
        # действуют правила сборки (сборка без заклинаний остаётся пустой).
        spells_known=list(
            instance.spells_known or tuple(sheet.spell_abilities)
        ),
        spell_abilities=dict(sheet.spell_abilities),
        slotless_casts=sheet.slotless_casts,
        concentration_effect_id=instance.concentration_effect_id,
        character_level=build_spec.level,
        class_slug=build_spec.class_slug,
        classes=dict(build_spec.classes),
        subclass_slug=build_spec.subclass_slug,
        species_slug=build_spec.species_slug,
        base_speed=sheet.base_speed if instance.base_speed is None else instance.base_speed,
        equipment=build_spec.equipment,
        # SRD 5.2 §Attunement -- carried into the fight so an item activation
        # can be gated on it (boots nobody attuned to must not double Speed).
        attuned_items=tuple(build_spec.attuned_items),
        feats=sheet.feats,
        # Picks the player made from a feature-choice pool (Sorcerer
        # Metamagic, ...). The sheet already carries them in `features`,
        # mixed with the ones the class GRANTS statically; only the picked
        # remainder needs carrying, because the repertoire gate walks the
        # owner docs for the granted set anyway.
        chosen_features=tuple(sheet.chosen_features),
        damage_resistances=list(sheet.damage_resistances),
        damage_immunities=list(sheet.damage_immunities),
        condition_immunities=list(sheet.condition_immunities),
        senses=sheet.senses,
        movement_modes=sheet.movement_modes,
        ignored_difficult_terrain=sheet.ignored_difficult_terrain,
        languages=sheet.languages,
        initiative_advantage=sheet.initiative_advantage,
        save_proficiencies=tuple(sorted(sheet.save_proficiencies)),
        skill_proficiencies=tuple(sorted(sheet.skill_proficiencies)),
        skill_expertise=tuple(sorted(sheet.skill_expertise)),
        weapon_proficiencies=tuple(sorted(sheet.weapon_proficiencies)),
        weapon_masteries=tuple(sorted(sheet.weapon_masteries)),
    )
    if instance.attack_bonus is not None:
        # model_copy marks the field as set (pydantic 2.13), which keeps the C15
        # sentinel reading it as the host's verbatim to-hit bonus.
        member = member.model_copy(update={"attack_bonus": instance.attack_bonus})
    return member


__all__ = [
    "build_party_member",
]


_FOUNDRY_ATTACK_KEYS: Final[dict[str, str]] = {
    "system.bonuses.mwak.attack": "attack.roll.bonus",
    "system.bonuses.rwak.attack": "attack.roll.bonus",
    "system.bonuses.msak.attack": "attack.roll.bonus",
    "system.bonuses.rsak.attack": "attack.roll.bonus",
}


def _combat_key(key: str) -> str | None:
    """The engine combat key for a corpus key, or None when the sheet owns it."""
    if key in _FOUNDRY_ATTACK_KEYS:
        return _FOUNDRY_ATTACK_KEYS[key]
    if key == "system.bonuses.abilities.save":
        return "save.bonus"
    # Resisting some damage types while being VULNERABLE to others is what Armor
    # of Vulnerability IS: two resistances and one weakness on the same effect.
    # The sheet takes its share (``derive_sheet`` -> ``damage_resistances``), so
    # only the weakness travels here -- and it has nowhere else to go:
    # ``Combatant.damage_vulnerabilities`` is fed from the monster stat block.
    if key == "system.traits.dv.value":
        return "vulnerabilities"
    if key == "system.attributes.ac.bonus":
        # The sheet already folds a worn, ATTUNED item AC bonus into
        # sheet.ac (measured: Cloak of Protection 12 -> 13). Projecting it
        # here would count it twice, so it stays with derive_sheet.
        return None
    if key.startswith(_ITEM_DOCUMENT_PREFIXES):
        return None
    return None


# SRD 5.2 armours whose rules ride a Foundry ITEM PROPERTY rather than a
# change on an effect. Adamantine Armor ships as an enchantment template whose
# effect is marked ``disabled`` -- Foundry uses it to REWRITE the target armour
# (adding the ``ada`` property) and then discards the template. Reading the
# template changes would therefore see nothing at all, which is exactly the
# flag-only trap the task warns about. The property is what the worn armour
# actually carries, so the property is what the engine reads.
_ITEM_PROPERTY_RULES: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "ada": (("critical.hit.immune", "override"),),
}


def _item_property_effects(item: Any, slug: str, *, entity_id: str) -> list[ActiveEffect]:
    """Rules an item carries through its Foundry ``system.properties`` list."""
    effects: list[ActiveEffect] = []
    for code, changes in _ITEM_PROPERTY_RULES.items():
        if code not in _item_properties(item):
            continue
        effects.append(
            ActiveEffect(
                id=f"item:{slug}:property:{code}",
                name=str(getattr(item, "name", slug)),
                origin=f"item:{slug}:property:{code}",
                target_id=entity_id,
                transfer=True,
                disabled=False,
                changes=[
                    ActiveEffectChange(key=key, mode=mode, value=1)
                    for key, mode in changes
                ],
            )
        )
    return effects


def _activated_by_an_activity(item: Any, passive: Any) -> bool:
    """True iff the item's own activity is what switches ``passive`` on.

    SRD 5.2 Boots of Speed: "While you wear these boots, you can take a Bonus
    Action to click the boots' heels together. If you do, the boots double
    your Speed." The doubling is not on while worn -- the activity applies
    it, and the activity's ``effects[]`` refs name this passive by id. The
    pack ships it as an ordinary enabled transferred effect, so wearing the
    item must NOT fold it in; a ``use_item`` on that activity does.
    """
    passive_id = getattr(passive, "id", None)
    if not passive_id:
        return False
    return any(
        ref.id == passive_id
        for activity in getattr(item, "activities", ()) or ()
        for ref in getattr(activity, "effects", ()) or ()
    )

def _item_properties(item: Any) -> frozenset[str]:
    """The item Foundry property codes (``mgc``, ``ada``, ...).

    The loader drops ``system.properties`` when it builds the schema, so the
    codes are recovered from the effect that sets them -- the same source
    Foundry rewrites the armour from.
    """
    codes: set[str] = set()
    for passive in getattr(item, "passive_effects", ()) or ():
        for change in getattr(passive, "changes", ()) or ():
            if change.key == "system.properties" and isinstance(change.value, str):
                codes.add(change.value.strip())
    return frozenset(codes)


def _pick_alternative(active: Sequence[Any]) -> list[Any]:
    """One effect per alternative slot, exactly as derive_sheet picks them.

    The packs ship one always-on effect per gemstone, dragon colour or wand
    strength, all marked active; carrying the item grants ONE of them. Siblings
    share an identical key/mode set; effects with different keys are distinct
    parts of one item and all apply. Mirrors build_spec._pick_item_variant so
    the sheet and the fight never disagree about which alternative is worn.
    """
    groups: dict[tuple[tuple[str, int], ...], list[Any]] = {}
    for passive in active:
        signature = tuple(sorted((c.key, c.mode) for c in passive.changes))
        groups.setdefault(signature, []).append(passive)
    return [siblings[0] for siblings in groups.values()]


def _project_change(change: Any) -> ActiveEffectChange | None:
    """One corpus change as an engine change, or None when it does not apply here."""
    key = _combat_key(change.key)
    if key is None:
        return None
    mode = _ITEM_MODES.get(int(change.mode))
    if mode is None:
        return None
    raw = change.value
    value: bool | int | str = raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            value = int(text)
        except ValueError:
            value = text
    return ActiveEffectChange(key=key, mode=mode, value=value)


def project_item_effects(
    build_spec: CharacterBuildSpec, *, loader: AssetLoader, entity_id: str
) -> list[ActiveEffect]:
    """Translate a build's worn-and-attuned item effects into ActiveEffects.

    Only effects the SHEET cannot carry are projected: a magic weapon's to-hit
    bonus, a Cloak of Protection's saving-throw bonus. Everything a sheet value
    already reflects (AC, ability scores, resistances, senses, speed) stays with
    derive_sheet, so nothing is counted twice.

    The rules deciding WHICH effects apply are the same the sheet follows: an
    item that requires attunement lends nothing until the build attunes to it, a
    disabled effect (an enchantment template that rewrites the item itself) never
    reaches the bearer, and a slug listed twice is still one worn object.
    """
    if not build_spec.equipment:
        return []
    attuned = frozenset(build_spec.attuned_items)
    effects: list[ActiveEffect] = []
    for slug in dict.fromkeys(build_spec.equipment):
        item = loader.get_item(slug)
        if item is None:
            continue
        if getattr(item, "requires_attunement", False) and slug not in attuned:
            continue
        active = [
            passive
            for passive in getattr(item, "passive_effects", ()) or ()
            # An effect an ACTIVITY switches on is not carried by wearing
            # the item (Boots of Speed: click the heels first).
            if passive.transfer
            and not passive.disabled
            and not _activated_by_an_activity(item, passive)
        ]
        effects.extend(_item_property_effects(item, slug, entity_id=entity_id))
        selected = _pick_item_variant(
            active,
            document_name=str(getattr(item, "name", slug)),
            chosen_name=build_spec.item_variants.get(slug),
        )
        for passive in selected:
            changes = [
                projected
                for change in passive.changes
                if (projected := _project_change(change)) is not None
            ]
            if not changes:
                continue
            label = str(getattr(passive, "name", slug))
            effects.append(
                ActiveEffect(
                    id=f"item:{slug}:{label}",
                    name=label,
                    origin=f"item:{slug}:{label}",
                    target_id=entity_id,
                    transfer=True,
                    changes=changes,
                )
            )
    return effects
