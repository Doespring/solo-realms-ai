"""Level-up transaction: one confirmed advancement, no partial state.

The rules are entirely Nat20's: ``advance`` adds one class level,
``validate_choices`` re-checks every decision the new level creates, and
``derive_sheet`` produces the new sheet. This module only states the host
policies the prompt fixes - which level the campaign has earned, that the hero
keeps its current hit points and spent resources, and that the definition, the
sheet and the hit-die pool move together or not at all.
"""
from __future__ import annotations

from dnd5e_engine import CharacterBuildSpec, CombatInstance, build_party_member
from dnd5e_engine.rules.character import hit_die_size

from .nat20_campaign import validate_definition
from .nat20_contracts import DurableState
from .nat20_worker import plain
from .nat20_prototype import experimental


LEVEL_UP_FIELDS = ("class_slug", "selected_choices", "subclass_slug", "prepared_spells", "milestone")


def _advance_build(state, payload, loader):
    """The build one level higher, with the picks the player confirmed.

    The right to advance is the campaign progression model: the SRD 5.2 XP
    table read through ``level_for_experience``, or an explicitly assigned
    milestone supplied by the host. Nothing here invents XP, and a re-sent
    request cannot grant a level twice because the advance is measured against
    the level the committed definition already records.
    """
    from dnd5e_engine.build_spec import CharacterBuildSpec
    from dnd5e_engine.rules.advancement import advance, level_for_experience

    definition = state.get("definition")
    if not definition:
        raise ValueError("У этой игры нет построенного героя: повышать уровень нечего.")
    if state.get("combat"):
        raise ValueError(
            "Повышение уровня недоступно во время боя; завершите бой и повторите."
        )
    build = CharacterBuildSpec.model_validate(definition["build"])
    class_slug = payload.get("class_slug") or build.class_slug
    if class_slug not in build.classes:
        raise ValueError(
            "Класс " + str(class_slug) + " не входит в этого героя: "
            + ", ".join(sorted(build.classes))
        )
    milestone = payload.get("milestone")
    if milestone is None:
        # A milestone scenario fixes the level itself: asking the XP table in a
        # game that keeps no experience would refuse a level the book grants.
        # The durable definition is JSON, so its fields are read as keys;
        # getattr on a dict silently answered None and the book's own
        # milestone rule never applied.
        from .progression import Progression as _Progression
        raw_progression = (definition.get("progression") if isinstance(definition, dict)
                           else getattr(definition, "progression", None))
        progression = (_Progression.model_validate(raw_progression)
                       if raw_progression else None)
        if getattr(progression, "mode", None) == "milestone":
            episode = str((definition.get("id") if isinstance(definition, dict) else getattr(definition, "id", "")) or "")
            milestone = getattr(progression, "level_at", lambda _e: None)(episode)
    target = int(milestone) if milestone is not None else level_for_experience(int(state.get("experience", 0)))
    if target <= build.level:
        raise ValueError(
            "Нет доступного повышения: опыт " + str(int(state.get("experience", 0)))
            + " соответствует уровню " + str(target) + ", герой уже уровня " + str(build.level) + "."
        )
    if target > 20:
        raise ValueError("Уровень выше 20 невозможен.")
    # One step at a time: ``advance`` adds exactly one class level, so a jump
    # over several levels still applies the transition rules level by level.
    payload_build = build.model_dump(mode="json")
    step = build
    while step.level < target:
        step = advance(step, class_slug=class_slug, loader=loader)
        payload_build = step.model_dump(mode="json")
    payload_build["selected_choices"] = list(payload.get("selected_choices") or build.selected_choices)
    if payload.get("subclass_slug"):
        payload_build["subclass_slug"] = payload["subclass_slug"]
    return build, CharacterBuildSpec(**payload_build)


def _carry_slots(old_hero, old_sheet, member):
    """Remaining slots, re-based on the new maximum and the slots actually spent.

    The hero field stores what is LEFT, so the spell slots already used are
    ``old maximum - left``. Re-basing on that keeps the spending and lets the
    increased maximum show, which clamping the old remainder would hide.
    """
    for field in ("spell_slots", "pact_slots"):
        left = {str(key): int(value) for key, value in (old_hero.get(field) or {}).items()}
        before = {str(key): int(value) for key, value in (old_sheet.get(field) or {}).items()}
        fresh = {str(key): int(value) for key, value in (member.get(field) or {}).items()}
        carried = {}
        for level, maximum in fresh.items():
            spent = max(0, before.get(level, maximum) - left.get(level, before.get(level, maximum)))
            carried[level] = max(0, maximum - spent)
        member[field] = carried
    member["custom_counters"] = dict(old_hero.get("custom_counters") or {})
    # ``slotless_casts`` is the hero's tuple of always-available spells, not a
    # counter: the tally of castings lives in ``custom_counters``.
    member["slotless_casts"] = list(old_hero.get("slotless_casts") or ())
    return member


def level_up(payload, loader, fingerprint):
    """Apply one confirmed advancement, atomically, inside the worker.

    The result is a new DurableState: the definition now records the higher
    level, the sheet is the native re-derivation, the hero keeps its current
    hit points, and the hit-die pool grows by exactly one. A failure raises
    before anything is returned, so the caller commits no partial state.
    """
    from dnd5e_engine.build_spec import derive_sheet
    from dnd5e_engine.rules.advancement import hp_gain, validate_choices

    state = DurableState.model_validate(payload["state"]).model_dump(mode="json")
    if state["release_digest"] != fingerprint:
        raise ValueError("Pinned engine/content release changed; migration must be explicit")
    definition = validate_definition(state["definition"]) if state.get("definition") is not None else None
    if not experimental(definition):
        raise ValueError("Автоматическое повышение уровня доступно только для собранного Nat20-героя.")
    before, after = _advance_build(state, payload, loader)
    validate_choices(after, loader=loader)
    sheet = derive_sheet(after, loader=loader)
    gain = hp_gain(before, after, loader=loader)
    member = build_party_member(after, CombatInstance(entity_id="hero", name=state["hero"]["name"],
        initiative=None, zone_id=state["hero"]["zone_id"],
        spells_known=tuple(_prepared(definition, payload))), loader=loader)
    hero = member.model_dump(mode="json", exclude_unset=True)
    hero = _carry_slots(state["hero"], state["sheet"], hero)
    # No silent healing: the maximum rises by the native delta and the current
    # hit points stay where the burning hero left them.
    hero["hp_max"] = int(sheet.hp_max)
    hero["hp_current"] = int(state["hero"]["hp_current"])
    hit_dice = dict(state["hit_dice"])
    hit_dice["hit_die_size"] = hit_die_size(str(loader.get_class(class_slug_of(after)).hit_die))
    hit_dice["dice_total"] = int(after.level)
    hit_dice["dice_remaining"] = int(hit_dice.get("dice_remaining", 0)) + int(after.level) - int(before.level)
    from .nat20_content import digest as fingerprint_of
    raw = (definition if isinstance(definition, dict)
           else definition.model_dump(mode="json"))
    raw["build"] = after.model_dump(mode="json")
    raw["prepared_spells"] = _prepared(definition, payload)
    raw["features"] = list(sheet.features)
    raw["definition_sha256"] = fingerprint_of({key: value for key, value in raw.items() if key != "definition_sha256"})
    updated = validate_definition(raw)
    state.update(definition=updated.model_dump(mode="json"), sheet=plain(sheet), hero=hero, hit_dice=hit_dice)
    return {"state": DurableState.model_validate(state).model_dump(mode="json"),
            "result": {"from_level": int(before.level), "to_level": int(after.level),
                       "hp_gain": int(gain), "hp_max": int(sheet.hp_max),
                       "hp_current": int(hero["hp_current"]), "class_slug": class_slug_of(after)},
            "events": []}


def _field(definition, name):
    """One definition field, from the validated model or its JSON form."""
    if isinstance(definition, dict):
        return definition.get(name)
    return getattr(definition, name, None)


def _prepared(definition, payload):
    """The spell list after this level, keeping every spell already known."""
    kept = list(_field(definition, "prepared_spells") or [])
    for slug in payload.get("prepared_spells") or []:
        if slug not in kept:
            kept.append(slug)
    return kept


def class_slug_of(build):
    return build.class_slug