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

# The engine names a decision by its machine kind. The player is asked in the
# words the sheet uses, so a refusal says *what* to choose instead of naming
# "choices" in general and leaving the table to guess.
CHOICE_TITLES = {
    "subclass": "подкласс",
    "asi": "повышение характеристик",
    "feat": "черта",
    "skill": "навык",
    "expertise": "компетентность",
    "feature": "классовая особенность",
    "spell": "заклинание",
    "mastery": "приём оружия",
    "language": "язык",
    "tool": "инструмент",
    "background": "предыстория",
    "increase": "прибавка характеристик",
}
# A choice is resolved by the loader that owns its records; the class of records
# follows the kind, and an unknown kind falls back to a plain feature.
CHOICE_GETTERS = {"subclass": "get_subclass", "feat": "get_feat", "spell": "get_spell"}

_CYRILLIC = __import__("re").compile(r"[А-Яа-яЁё]")


def _choice_name(loader, kind, slug):
    """The option's own name, or its slug when the corpus has no record."""
    try:
        record = getattr(loader, CHOICE_GETTERS.get(kind, "get_feature"))(slug)
    except Exception:
        record = None
    return str(getattr(record, "name", "") or slug)


def owed_choices_question(build, loader, fallback):
    """What this level still owes the player, said so it can be answered.

    The engine's own line ("advancement choices still owed: subclass x1 at
    class:fighter level 3") is a log line: it names no option and reaches the
    table in English. This turns the same facts into the question the player
    has to answer, with the options the corpus offers. Options printed only in
    Latin are not listed by name - the chat filter that keeps engine prose from
    the table would drop the whole sentence - the count is given instead, and
    the screen shows them all.
    """
    try:
        from dnd5e_engine.rules.advancement import choice_options

        rows = [row for row in choice_options(build, loader=loader) if row.get("missing")]
    except Exception:
        return fallback
    if not rows:
        return fallback
    parts = []
    for row in rows:
        kind = str(row.get("kind") or "")
        title = CHOICE_TITLES.get(kind, "выбор")
        options = [_choice_name(loader, kind, slug) for slug in (row.get("pool") or ())]
        named = [name for name in options if _CYRILLIC.search(name)]
        if named:
            shown = named[:6]
            said = ": " + ", ".join(shown)
            if len(options) > len(shown):
                said += " (всего вариантов " + str(len(options)) + ")"
        elif options:
            said = " (вариантов на экране героя: " + str(len(options)) + ")"
        else:
            said = ""
        parts.append(str(int(row.get("missing") or 1)) + "× " + title + said)
    return ("Повышение до уровня " + str(int(build.level)) + " требует выбора игрока — "
            + "; ".join(parts)
            + ". Уровень не поднят: выберите это на экране героя и подтвердите повышение снова.")


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
    earned = int(milestone) if milestone is not None else level_for_experience(int(state.get("experience", 0)))
    # One confirmed advancement raises exactly one level, milestone or not.
    # Experience or a finished chapter can cover several levels, but every level
    # asks its own questions (a subclass, a feat, an ability increase), and a
    # jump would demand decisions the player was never offered on the level that
    # asks them. A caller that names a higher destination gets the earned level
    # capped to one step, never the level it named: the screen's offer and this
    # commit therefore describe the same single transition.
    if milestone is not None:
        earned_text = "рубеж кампании даёт уровень " + str(earned)
    else:
        earned_text = ("опыт " + str(int(state.get("experience", 0)))
                       + " соответствует уровню " + str(earned))
    target = min(earned, build.level + 1)
    if target <= build.level:
        raise ValueError(
            "Нет доступного повышения: " + earned_text + ", герой уже уровня "
            + str(build.level) + ". Повышение идёт по одному уровню за подтверждение."
        )
    if target > 20:
        raise ValueError("Уровень выше 20 невозможен.")
    # One confirmation, one class level: ``advance`` applies exactly the next
    # level's transition, so the committed hero is one step above the one the
    # player saw and never a destination the caller named. The loop is gone on
    # purpose - a jump would demand decisions on levels nobody was shown.
    advanced = advance(build, class_slug=class_slug, loader=loader)
    payload_build = advanced.model_dump(mode="json")
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
    try:
        validate_choices(after, loader=loader)
    except ValueError as error:
        # Options the player has not chosen are not an engine fault: they are the
        # question the level asks. Anything else validate_choices refuses stays
        # as written - a broken pick must not be dressed up as a pending choice.
        if "advancement choices still owed" not in str(error):
            raise
        raise ValueError(owed_choices_question(after, loader, str(error))) from None
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
    # r214: the owner decided companions stand at the hero's level (BG3 rule).
    # Each companion is advanced by the engine, not copied: its own class,
    # its own sheet, its own hit die. An advancement the rules make the player
    # decide is refused with the question, and the whole level-up is not
    # committed - a half-levelled party is worse than none.
    from .nat20_party import advance_to, record as party_record

    companions = []
    for item in state.get('party') or []:
        if item.get('role') != 'companion':
            continue
        companion = dict(item)
        companion_build = CharacterBuildSpec.model_validate(companion['build'])
        if companion_build.level < after.level:
            choices = (payload.get('party_choices') or {}).get(companion['id'])
            try:
                advanced = advance_to(companion_build, after.level, loader, choices)
            except ValueError as error:
                raise ValueError('Уровень героя не поднят: ' + str(error)) from None
            fresh = party_record(advanced, companion['id'], companion['name'], loader,
                                 zone_id=(companion.get('member') or {}).get('zone_id') or '1,1',
                                 spells=tuple((companion.get('member') or {}).get('spells_known') or ()))
            old_member = companion.get('member') or {}
            fresh['member'] = _carry_slots(old_member, companion.get('sheet') or {}, fresh['member'])
            fresh['member']['hp_max'] = int(fresh['sheet']['hp_max'])
            fresh['member']['hp_current'] = min(int(old_member.get('hp_current') or fresh['member']['hp_max']),
                                                int(fresh['member']['hp_max']))
            dice = dict(fresh['hit_dice'])
            dice['dice_remaining'] = (int((companion.get('hit_dice') or {}).get('dice_remaining', 0))
                                      + int(advanced.level) - int(companion_build.level))
            fresh['hit_dice'] = dice
            fresh['exhaustion'] = int(companion.get('exhaustion') or 0)
            companion = fresh
        companions.append(companion)
    state['party'] = companions
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