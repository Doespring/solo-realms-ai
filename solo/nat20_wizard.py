"""Character-wizard contract: steps, options, obligations, derived sheet.

The wizard screen, the create endpoint and the level-up endpoint all ask this
module the same question - what may this draft choose next, and what does the
native engine already make of it - so a screen cannot offer a decision the rules
forbid and a direct HTTP call cannot skip one. Every rule answer comes from the
native modules (dnd5e_engine.rules.advancement, derive_sheet,
build_scale_values); this module only orders those answers into steps and
projects them. Nothing here recomputes a modifier, a slot or a bonus.

A draft is deliberately allowed to be incomplete: a player picks the species
before the class, and the half-filled draft must still render a sheet. So the
contract reports, per step, whether it is empty, incomplete, complete or not
applicable at all, and keeps the blocking reasons in the words the engine uses.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pathlib import Path

from .character_catalog import display_name
from .nat20_kit import equipment_slugs, grant

ROOT = Path(__file__).resolve().parents[1]

WIZARD_VERSION = 1

STEP_ORDER = (
    "species", "lineage", "class", "subclass", "background", "scores",
    "skills", "magic", "final",
)

STEP_TITLES = {
    "species": "Вид",
    "lineage": "Наследие",
    "class": "Класс",
    "subclass": "Подкласс",
    "background": "Предыстория",
    "scores": "Характеристики",
    "skills": "Навыки и особенности",
    "magic": "Магия",
    "final": "Итог и имя",
}

CHOICE_TITLES = {
    "skill": "Навыки",
    "expertise": "Компетентность",
    "feature": "Особенности",
    "asi": "Повышение характеристик",
    "increase": "Повышение от предыстории",
}

ABILITY_ORDER = (
    "strength", "dexterity", "constitution",
    "intelligence", "wisdom", "charisma",
)

ABILITY_TITLES = {
    "strength": "Сила",
    "dexterity": "Ловкость",
    "constitution": "Телосложение",
    "intelligence": "Интеллект",
    "wisdom": "Мудрость",
    "charisma": "Харизма",
}


class WizardDraft(BaseModel):
    """A partially filled character; every field may still be unanswered."""

    model_config = ConfigDict(extra="forbid")

    species_slug: str | None = None
    #: The base species while the leaf is not picked yet ("Elf"). A grouping the
    #: corpus implies, kept out of ``species_slug`` so the build never holds a
    #: slug the engine cannot resolve.
    species_base: str | None = None
    class_slug: str | None = None
    subclass_slug: str | None = None
    background_slug: str | None = None
    level: int = Field(default=1, ge=1, le=20)
    ability_score_method: Literal["standard_array", "point_buy"] | None = None
    ability_scores: dict[str, int] = Field(default_factory=dict)
    selected_choices: list[str] = Field(default_factory=list)
    #: The screen's own answers, one entry per advancement step it showed.
    #: ``selected_choices`` stays the only token list the engine reads; these
    #: are kept alongside so a reload shows each card its own picks.
    choices: dict[str, Any] = Field(default_factory=dict)
    equipment_kits: dict[str, str] = Field(default_factory=dict)
    kit_picks: dict[str, dict[str, str]] = Field(default_factory=dict)
    prepared_spells: list[str] = Field(default_factory=list, max_length=100)
    # Equipment the hero already carries, and whether this draft is an
    # advancement of a hero that exists. A level-up draft inherits its equipment
    # from the live build, so advancement never re-chooses a starting kit, never
    # grants a second copy and never blocks on the creation-only steps.
    equipment: list[str] = Field(default_factory=list, max_length=200)
    advancement: bool = False
    name: str = Field(default="", max_length=80)
    backstory: str = Field(default="", max_length=4000)

    def spec_payload(self) -> dict[str, object] | None:
        """The native build this draft describes, or None while it cannot be one.

        A spec needs a species and a class; a draft without them is a form, not a
        character, and deriving a sheet for it would invent a hero nobody chose.
        """
        if not (self.species_slug and self.class_slug):
            return None
        payload: dict[str, object] = {
            "species_slug": self.species_slug,
            "class_slug": self.class_slug,
            "level": int(self.level),
            "selected_choices": tuple(self.selected_choices),
            "equipment": tuple(equipment_slugs(self.equipment_grants()) or self.equipment),
        }
        if self.subclass_slug:
            payload["subclass_slug"] = self.subclass_slug
        if self.background_slug:
            payload["background_slug"] = self.background_slug
        if self.ability_score_method:
            payload["ability_score_method"] = self.ability_score_method
        if self.ability_scores:
            payload["ability_scores"] = dict(self.ability_scores)
        return payload

    def equipment_grants(self) -> list[dict[str, object]]:
        """The reviewed starting kits this draft asks for, resolved.

        A kit letter the corpus does not offer grants nothing and is reported by
        the equipment step instead: refusing it here would turn a wrong radio
        button into an exception the screen cannot show.
        """
        out = []
        for key, kit_key in sorted(self.equipment_kits.items()):
            kind, _, source = key.partition(":")
            # A kit left over from a class the player has since changed must not
            # hand that class's equipment to the new hero.
            if source not in (self.class_slug, self.background_slug):
                continue
            try:
                out.append(grant(kind, source, kit_key, self.kit_picks.get(key)))
            except ValueError:
                continue
        return out


def _frame():
    """The pinned release: (loader, fingerprint, profile)."""
    from .nat20_content import release
    from .nat20_prototype import RELEASE

    return release(RELEASE)


def _entry(slug, name, *, disabled_reason=None, detail=None):
    return {
        "id": slug,
        "name": name,
        "disabled_reason": disabled_reason,
        "detail": detail,
    }


def _status(*, chosen, count=1, valid):
    """Step status from what is answered: a bool for a single pick, else items."""
    if not valid:
        return "invalid"
    if not chosen:
        return "empty"
    filled = 1 if isinstance(chosen, bool) else len(chosen)
    return "incomplete" if filled < count else "complete"


def _species_steps(draft, loader):
    """Species, plus a lineage step whenever the corpus splits that species.

    The 2024 corpus files species as leaves (``Elf, High``), so a player picks
    the base first and the leaf second. The draft only ever stores a leaf slug
    in ``species_slug``; the base lives in ``species_base`` while the leaf is
    still unchosen, so the build never carries a slug the engine cannot read.
    """
    from dnd5e_engine.rules.advancement import species_lineages

    groups = species_lineages(loader)
    leaf = None
    for group in groups:
        if draft.species_slug in {variant["slug"] for variant in group["variants"]}:
            leaf = group
            break
    # A leaf already names its own group, so the lineage step never needs the
    # base stored twice.
    base = leaf
    for name in (draft.species_base, draft.species_slug):
        if not name:
            continue
        for group in groups:
            if str(group["base"]) == str(name):
                base = group
                break
        if base is not None:
            break
    known = leaf is not None or base is not None
    steps = [
        {
            "id": "species",
            "title": STEP_TITLES["species"],
            "status": (
                "invalid" if draft.species_slug and not known
                else ("complete" if leaf else "empty")
            ),
            "required": True,
            "count": 1,
            "missing": 0 if leaf else 1,
            "chosen": [str(leaf["base"])] if leaf else ([str(base["base"])] if base else []),
            "options": [
                dict(
                    _entry(
                        str(group["base"]),
                        display_name(
                            "species", english=str(group["name"]), fallback=str(group["name"])
                        ),
                    ),
                    detail=", ".join(
                        display_name("lineage", str(row["slug"]), fallback=str(row["name"]))
                        for row in group["variants"]
                    ),
                    lineage_required=bool(group["lineage_required"]),
                    variants=[
                        dict(
                            row,
                            name=display_name(
                                "lineage", str(row["slug"]), fallback=str(row["name"])
                            ),
                        )
                        for row in group["variants"]
                    ],
                )
                for group in groups
            ],
            "reasons": (
                ["Такого вида нет в корпусе: " + str(draft.species_slug)]
                if draft.species_slug and not known
                else []
            ),
            "lineage_required": bool(base and base["lineage_required"]),
        }
    ]
    # The lineage step stays visible once a leaf is chosen: the player must be
    # able to change it, and hiding it would look like the choice was lost.
    if not (base and base["lineage_required"]):
        return steps
    picked = draft.species_slug if draft.species_slug in {
        variant["slug"] for variant in base["variants"]
    } else None
    steps.append(
        {
            "id": "lineage",
            "title": STEP_TITLES["lineage"],
            "status": "complete" if picked else "empty",
            "required": True,
            "count": 1,
            "missing": 0 if picked else 1,
            "chosen": [picked] if picked else [],
            "options": [
                _entry(
                    variant["slug"],
                    display_name("lineage", str(variant["slug"]), fallback=str(variant["name"])),
                )
                for variant in base["variants"]
            ],
            "reasons": [] if picked else [
                "Выберите наследие: "
                + display_name("species", english=str(base["base"]), fallback=str(base["base"]))
                + "."
            ],
            "base": str(base["base"]),
        }
    )
    return steps

def _class_step(draft, loader):
    classes = list(loader.list_slugs("classes"))
    known = draft.class_slug in classes if draft.class_slug else False
    return {
        "id": "class",
        "title": STEP_TITLES["class"],
        "status": _status(
            chosen=bool(draft.class_slug), count=1,
            valid=known or not draft.class_slug,
        ),
        "required": True,
        "count": 1,
        "missing": 0 if known else 1,
        "chosen": [draft.class_slug] if draft.class_slug else [],
        "options": [
            _entry(
                slug,
                display_name("class", slug, fallback=str(loader.get_class(slug).name)),
            )
            for slug in classes
        ],
        "reasons": (
            []
            if known or not draft.class_slug
            else ["Такого класса нет в корпусе: " + str(draft.class_slug)]
        ),
    }


def _source_detail(slug):
    """r147: откуда подкласс — пакетный SRD или книга владельца.

    Маркер источника обязан быть виден там, где игрок выбирает подкласс, а не
    только в файле данных: иначе PHB и SRD потом не разделить по интерфейсу.
    """
    from .nat20_canonical import marker
    found = marker(slug)
    if not found:
        return None
    pages = ", ".join(str(page) for page in found["printed_pages"])
    return "PHB 2024, стр. " + pages


def _subclass_step(draft, loader):
    """Only when the draft class opens a subclass at the draft level."""
    from dnd5e_engine.rules.advancement import subclass_gate, subclass_options

    if not draft.class_slug or draft.class_slug not in loader.list_slugs("classes"):
        return None
    gate = subclass_gate(draft.class_slug, loader=loader)
    if gate is None or draft.level < gate:
        return None
    rows = subclass_options(draft.class_slug, draft.level, loader=loader)
    slugs = {row["slug"] for row in rows}
    picked = draft.subclass_slug if draft.subclass_slug in slugs else None
    reasons = []
    if draft.subclass_slug and draft.subclass_slug not in slugs:
        reasons.append(
            "Подкласс " + str(draft.subclass_slug) + " недоступен на уровне "
            + str(draft.level) + "."
        )
    if not picked and not reasons:
        reasons.append(
            "Подкласс обязателен с уровня " + str(gate) + ": выберите один из "
            + str(len(rows)) + "."
        )
    return {
        "id": "subclass",
        "title": STEP_TITLES["subclass"],
        "status": "complete" if picked else ("invalid" if reasons and draft.subclass_slug else "empty"),
        "required": True,
        "count": 1,
        "missing": 0 if picked else 1,
        "chosen": [picked] if picked else [],
        "options": [
            _entry(
                row["slug"],
                display_name("subclass", str(row["slug"]), fallback=str(row["name"])),
                detail=_source_detail(row["slug"]),
            )
            for row in rows
        ],
        "reasons": reasons,
        "opens_at_level": gate,
    }


def _background_step(draft, loader):
    rows = [loader.get_background(slug) for slug in loader.list_slugs("backgrounds")]
    slugs = {str(row.slug) for row in rows if row is not None}
    known = draft.background_slug in slugs if draft.background_slug else False
    return {
        "id": "background",
        "title": STEP_TITLES["background"],
        "status": _status(
            chosen=bool(draft.background_slug), count=1,
            valid=known or not draft.background_slug,
        ),
        "required": True,
        "count": 1,
        "missing": 0 if known else 1,
        "chosen": [draft.background_slug] if draft.background_slug else [],
        "options": [
            _entry(str(row.slug), display_name("background", str(row.slug), fallback=str(row.name)))
            for row in rows if row is not None
        ],
        "reasons": (
            []
            if known or not draft.background_slug
            else ["Такой предыстории нет в корпусе: " + str(draft.background_slug)]
        ),
    }


def _origin_bonuses(draft):
    """The origin increases, kept apart from the rolled base array.

    A background adds +2 and +1 to two scores. Those points are not part of
    the standard array: folding them back into the six numbers would spend
    them twice and hide what the player chose. The screen shows base, bonus
    and total separately, each read from the one answer that grants it.
    """
    rows = []
    for text in draft.selected_choices or []:
        value = str(text)
        if not value.startswith('background:'):
            continue
        body = value.split(':', 1)[1]
        for part in body.replace(',', ' ').split():
            name, _, amount = part.partition('+')
            if not name or not amount.isdigit():
                continue
            rows.append({'id': name, 'bonus': int(amount)})
    return rows

def _scores_step(draft, loader):
    """Scores, and the generation method that makes them verifiable."""
    from dnd5e_engine.rules.character import MAX_SCORE_FROM_INCREASES
    from dnd5e_engine.rules.dice import POINT_BUY_TOTAL, STANDARD_ARRAY

    chosen = dict(draft.ability_scores)
    reasons = []
    status = "empty"
    # A step is complete only when all six scores hold a value the array actually
    # contains. Treating a partly filled array as complete let the wizard advance
    # with five slots empty and then refuse the hero at the end: the player saw a
    # green step and a dead button, which is the soft lock. ``missing`` and the
    # duplicated/foreign values are reported here so the screen can explain it.
    unplaced = [name for name in ABILITY_ORDER if chosen.get(name) is None]
    if draft.ability_score_method is None:
        reasons.append(
            "Выберите способ генерации: стандартный набор или покупка за очки."
        )
        if chosen:
            status = "invalid"
    elif not chosen:
        reasons.append("Распределите значения по шести характеристикам.")
    elif unplaced:
        reasons.append(
            "Осталось разложить значения: " + str(len(unplaced)) + " из 6."
        )
        status = "incomplete"
    else:
        status = "complete"
    # A full set can still be wrong: a duplicated value or a number the array does
    # not contain passes "six filled slots" and only fails at the very end. The set
    # is compared here so the step that owns the mistake reports it, not the last one.
    if status == "complete" and draft.ability_score_method == "standard_array":
        from dnd5e_engine.rules.dice import STANDARD_ARRAY as _ARRAY
        left_set = sorted(int(v) for v in _ARRAY)
        got_set = sorted(int(v) for v in chosen.values() if v is not None)
        if got_set != left_set:
            status = "invalid"
            reasons.append(
                "Стандартный набор требует значения " + ", ".join(str(v) for v in left_set)
                + " без повторов; сейчас " + ", ".join(str(v) for v in got_set) + "."
            )
    # The budget is computed here, from the native tables, so the screen shows
    # what remains without re-implementing the point costs or the array in JS.
    budget = None
    low, high = 1, int(MAX_SCORE_FROM_INCREASES)
    if draft.ability_score_method == "point_buy":
        from dnd5e_engine.rules.dice import POINT_BUY_COSTS, POINT_BUY_TOTAL

        # Point buy has its own legal range, published by the native tables. A
        # value outside it has no cost at all, so summing it as zero made six
        # impossible scores look finished and the remaining budget wrong.
        costs = {int(key): int(value) for key, value in POINT_BUY_COSTS.items()}
        low, high = min(costs), max(costs)
        foreign = sorted(
            {int(value) for value in chosen.values() if int(value) not in costs}
        )
        if foreign:
            status = "invalid"
            reasons.append(
                "Покупка за очки допускает значения от " + str(low) + " до "
                + str(high) + "; сейчас " + ", ".join(str(value) for value in foreign) + "."
            )
        spent = sum(costs.get(int(value), 0) for value in chosen.values())
        budget = {
            "kind": "point_buy",
            "total": int(POINT_BUY_TOTAL),
            "spent": spent,
            "left": int(POINT_BUY_TOTAL) - spent,
            "costs": {str(key): value for key, value in sorted(costs.items())},
            "minimum": low,
            "maximum": high,
        }
    elif draft.ability_score_method == "standard_array":
        left = list(STANDARD_ARRAY)
        for value in chosen.values():
            if value in left:
                left.remove(value)
        budget = {
            "kind": "standard_array",
            "array": [int(value) for value in STANDARD_ARRAY],
            "left": [int(value) for value in left],
        }
    return {
        "id": "scores",
        "title": STEP_TITLES["scores"],
        "status": status,
        "required": True,
        "count": len(ABILITY_ORDER),
        "missing": sum(1 for name in ABILITY_ORDER if name not in chosen),
        "chosen": [name for name in ABILITY_ORDER if name in chosen],
        "options": [
            {"id": "standard_array", "name": "Стандартный набор",
             "detail": ", ".join(str(value) for value in STANDARD_ARRAY)},
            {"id": "point_buy", "name": "Покупка за очки",
             "detail": str(POINT_BUY_TOTAL) + " очков"}
        ],
        "chosen_method": draft.ability_score_method,
        "budget": budget,
        # The array is a set of values to place, not six numbers to type. The
        # screen needs to know which values are still free and which are already
        # taken, so a choice can move a value between two characteristics instead
        # of only filling blanks. "swap" states that rule once for any client.
        "swap": bool(budget and budget.get("kind") == "standard_array"),
        # The bounds belong to the chosen method: point buy starts at its own
        # floor, and the screen must not offer a number the method cannot pay for.
        "abilities": [
            {"id": name, "name": ABILITY_TITLES[name],
             "value": chosen.get(name),
             "minimum": low, "maximum": high}
            for name in ABILITY_ORDER
        ],
        "bonuses": _origin_bonuses(draft),
        "reasons": reasons,
    }


def _pool_name(kind, slug, loader):
    """A readable label for an option the native module named by slug."""
    if kind in ("skill", "expertise"):
        from .rulesets import SKILL_NAMES

        if slug in SKILL_NAMES:
            return str(SKILL_NAMES[slug])
        from dnd5e_engine.rules.skills import SKILL_DISPLAY_NAMES

        return str(SKILL_DISPLAY_NAMES.get(slug, slug))
    if kind in ("asi", "increase"):
        from .character_catalog import ABILITY_NAMES

        return str(ABILITY_NAMES.get(slug, slug))
    if kind == "feature":
        doc = loader.get_feature(slug)
        if doc is not None:
            return str(doc.name)
    if kind == "mastery":
        doc = loader.get_item(slug)
        if doc is not None:
            return str(doc.name)
    return str(slug)


def _proficient_skills(draft, loader):
    """The skills this draft is already proficient in, from the native sheet.

    SRD 5.2 expertise upgrades an existing proficiency, so the eligible pool is
    the sheet's own list, not all eighteen skills. The native module refuses an
    expertise outside it, and offering the whole table would let the screen lead
    the player into that refusal.
    """
    payload = draft.spec_payload()
    if payload is None:
        return ()
    from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet

    try:
        sheet = derive_sheet(CharacterBuildSpec(**payload), loader=loader)
    except ValueError:
        return ()
    return tuple(str(slug) for slug in sheet.skill_proficiencies)


def _choice_steps(draft, loader, required, pending, counted=True):
    """The picks the native module says this draft still owes.

    `counted` is false when the native pending list could not be computed. An
    unknown number of missing picks is not zero: marking every step complete
    would show green ticks for a draft nobody finished and let the screen
    advance past a step that still owes an answer.
    """
    owed = {item.choice.key: item.missing for item in pending}
    owned = _assign_picks(required, draft)
    proficient = set(_proficient_skills(draft, loader))
    steps = []
    for row in required:
        if row.kind not in CHOICE_TITLES:
            continue
        pool = tuple(row.pool)
        own = set(owned.get(row.key, {}).get("chosen", []))
        if row.kind == "skill" and proficient:
            # SRD 5.2 grants each skill once: a background or another pool may
            # already own one of these, and spending a second pick on it would
            # be refused by the engine. The hero's own picks stay offered so a
            # filled step still shows what the player chose.
            pool = tuple(slug for slug in pool if slug in own or slug not in proficient)
        if row.kind == "expertise" and proficient:
            # Expertise upgrades a proficiency, so only the hero's own list is
            # offered; an existing pick stays visible even if the sheet moved.
            pool = tuple(slug for slug in pool if slug in proficient or slug in own)
        missing = int(owed.get(row.key, 0))
        answered = len(own)
        if not counted:
            # The native engine could not say what is still owed, so nothing here
            # can be called complete. A step with no answer at all is empty; one
            # that has some is incomplete. Neither is green.
            missing = max(int(row.count) - answered, 1)
        steps.append(
            {
                "id": "skills",
                "title": CHOICE_TITLES[row.kind],
                "status": (
                    "unavailable" if not counted
                    else ("complete" if not missing
                          else ("empty" if missing >= row.count else "incomplete"))
                ),
                "required": bool(row.blocking),
                "count": int(row.count),
                "missing": missing,
                "chosen": owned.get(row.key, {}).get("chosen", []),
                "options": [
                    _entry(slug, _pool_name(row.kind, slug, loader))
                    for slug in pool
                ],
                "reasons": (
                    []
                    if not missing
                    else [
                        str(row.title or row.kind) + " (" + str(row.source)
                        + "): осталось выбрать " + str(missing)
                    ]
                ),
                "choice_key": row.key,
                "kind": row.kind,
                "source": row.source,
                "level": int(row.level),
                "points": int(row.points),
                "cap": int(row.cap),
                "increases": owned.get(row.key, {}).get("increases", {}),
                "answer": draft.choices.get(row.key),
                "locked": list(row.locked),
                "replacement": bool(row.replacement),
                "hint": str(row.hint or ""),
            }
        )
    # Expertise upgrades a proficiency, so the skills are asked before it even
    # though the native order sorts 'expertise' first.
    order = ("increase", "skill", "expertise", "feature", "asi")
    steps.sort(key=lambda row: order.index(row["kind"]) if row["kind"] in order else len(order))
    return steps


MAGIC_KEYS = ("max-prepared", "cantrips-known", "invocations-known")

MAGIC_NOTE = (
    "Число известных заклинаний берётся из данных классов, но корпус не хранит"
    " список заклинаний класса: доступность по классу проверяется только по"
    " уровню ячейки."
)


def _magic_limits(draft, loader):
    """The caster limits the corpus records as ScaleValue data, pre-resolved.

    SRD 5.2 publishes prepared-spell and cantrip counts as class ScaleValues, so
    the wizard reads them there instead of hardcoding a table. A limit the
    corpus does not publish at this level is simply absent, never invented.
    """
    from dnd5e_engine.activities.scale import build_scale_values

    if not draft.class_slug or draft.class_slug not in loader.list_slugs("classes"):
        return []
    values = build_scale_values(
        class_slug=draft.class_slug,
        subclass_slug=draft.subclass_slug,
        species_slug=draft.species_slug,
        level=draft.level,
        loader=loader,
    )
    out = []
    prefix = draft.class_slug + "."
    for key, value in sorted(values.items()):
        if not key.startswith(prefix):
            continue
        short = key[len(prefix):]
        if short not in MAGIC_KEYS or not isinstance(value, int):
            continue
        out.append({"id": key, "name": short, "value": int(value), "blocking": False})
    return out


def _slot_ceiling(draft, loader):
    """The highest spell level the draft actually has a slot for."""
    from dnd5e_engine import CharacterBuildSpec, derive_sheet

    payload = draft.spec_payload()
    if payload is None:
        return 0
    try:
        sheet = derive_sheet(CharacterBuildSpec(**payload), loader=loader)
    except ValueError:
        return 0
    levels = [level for level, count in dict(sheet.spell_slots or {}).items() if count]
    levels += [level for level, count in dict(sheet.pact_slots or {}).items() if count]
    return max(levels or [0])


#: Class to spell slugs, extracted from the pinned Foundry class pages. The
#: canonical spell records carry no class field, so this reviewed export is the
#: only source that answers whether a class may learn a spell at all.
CLASS_SPELL_LISTS = ROOT / "integration/nat20/playable/class-spell-lists.json"
_CLASS_LISTS = None


def class_spell_lists():
    """The reviewed class to spell map, loaded once from the pinned export."""
    global _CLASS_LISTS
    if _CLASS_LISTS is None:
        import json
        try:
            payload = json.loads(CLASS_SPELL_LISTS.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = {}
        _CLASS_LISTS = {str(k): set(v) for k, v in (payload.get("classes") or {}).items()}
    return _CLASS_LISTS

def _spell_pool(loader, *, cantrips, ceiling, klass=""):
    """Spells at one level band, limited to what this class can actually cast.

    Two gates, both answered from data: the class list says whether the class
    may learn the spell at all, and the slot ceiling says whether the build has
    a slot for it. A spell that fails either gate is not offered, so a screen
    can never propose it. An unknown class keeps the slot gate only, because a
    rule of "everyone may cast everything" is exactly what must not appear.
    """
    allowed = class_spell_lists().get(str(klass or "").casefold())
    out = []
    for slug in loader.list_slugs("spells"):
        spell = loader.get_spell(slug)
        if spell is None:
            continue
        level = int(spell.level)
        if cantrips != (level == 0) or level > ceiling:
            continue
        if allowed is not None and slug not in allowed:
            continue
        out.append(_entry(slug, str(spell.name), detail="уровень " + str(level)))
    return out


def _magic_step(draft, loader):
    if not draft.class_slug or draft.class_slug not in loader.list_slugs("classes"):
        return None
    doc = loader.get_class(draft.class_slug)
    casting = str(doc.spellcasting.progression)
    if casting == "none" and not draft.prepared_spells:
        return None
    ceiling = _slot_ceiling(draft, loader)
    klass = str(draft.class_slug or "")
    cantrips = _spell_pool(loader, cantrips=True, ceiling=ceiling, klass=klass)
    spells = _spell_pool(loader, cantrips=False, ceiling=ceiling, klass=klass)
    chosen = list(draft.prepared_spells)
    published = set(loader.list_slugs("spells"))
    unknown = [slug for slug in chosen if slug not in published]
    cantrip_slugs = {row["id"] for row in cantrips}
    picked_cantrips = [slug for slug in chosen if slug in cantrip_slugs]
    picked_spells = [slug for slug in chosen if slug not in cantrip_slugs]
    limits = _magic_limits(draft, loader)
    counts = {row["name"]: int(row["value"]) for row in limits}
    # The corpus publishes these counts, so they bound the step; a limit it does
    # not publish stays absent rather than becoming a hardcoded number.
    want_cantrips = counts.get("cantrips-known")
    want_spells = counts.get("max-prepared")
    missing = 0
    reasons = ["Такого заклинания нет в корпусе: " + slug for slug in unknown]
    if want_cantrips is not None and len(picked_cantrips) < want_cantrips:
        missing += want_cantrips - len(picked_cantrips)
        reasons.append(
            "Заговоров: выбрано " + str(len(picked_cantrips))
            + " из " + str(want_cantrips) + "."
        )
    if want_spells is not None and len(picked_spells) < want_spells:
        missing += want_spells - len(picked_spells)
        reasons.append(
            "Заклинаний: выбрано " + str(len(picked_spells))
            + " из " + str(want_spells) + "."
        )
    chosen_count = (want_cantrips or 0) + (want_spells or 0)
    if unknown:
        status = "invalid"
    elif missing and chosen:
        status = "incomplete"
    elif missing:
        status = "empty"
    else:
        status = "complete"
    return {
        "id": "magic",
        "title": STEP_TITLES["magic"],
        "status": status,
        "required": casting != "none",
        "count": chosen_count,
        "missing": missing,
        "chosen": chosen,
        "picked_cantrips": picked_cantrips,
        "picked_spells": picked_spells,
        "options": cantrips + spells,
        "cantrips": cantrips,
        "spells": spells,
        "highest_slot": ceiling,
        "limits": limits,
        "reasons": reasons,
        "note": MAGIC_NOTE,
    }


def _equipment_step(draft, loader):
    """The starting kit, granted from the reviewed table - never a catalogue.

    SRD 5.2 hands a level-1 character a class kit and a background kit, each
    with a "only gold" letter; the player chooses between those letters, not
    between items. The composition comes from solo/nat20_kit.py, which the
    corpus prose pins (tests/test_nat20_kit.py).
    """
    from .nat20_kit import kits as kit_rows

    sources = []
    if draft.class_slug:
        sources.append(("class", draft.class_slug))
    if draft.background_slug:
        sources.append(("background", draft.background_slug))
    groups = []
    chosen_all = []
    reasons = []
    for kind, source in sources:
        try:
            rows = kit_rows(kind, source)
        except ValueError as error:
            reasons.append(str(error))
            continue
        key = kind + ":" + source
        picked = draft.equipment_kits.get(key)
        if picked is not None and picked not in {row["key"] for row in rows}:
            reasons.append("Недопустимый комплект " + picked + " для " + source + ".")
            picked = None
        # The rules offer letters, not items. The first letter that actually
        # carries equipment is the one worth starting with, and it is marked so
        # the screen can recommend it instead of guessing.
        recommended = next((row["key"] for row in rows if row["items"]), rows[0]["key"])
        groups.append(
            {
                "id": key,
                "title": source,
                "required": True,
                "chosen": picked,
                "recommended": recommended,
                "options": [
                    {
                        "id": row["key"],
                        "name": row["label"],
                        "gold_gp": row["gold_gp"],
                        "items": row["items"],
                        "picks": row["picks"],
                        "notes": row["notes"],
                        "recommended": row["key"] == recommended,
                    }
                    for row in rows
                ],
                "picks": draft.kit_picks.get(key, {}),
            }
        )
        if picked:
            chosen_all.append(key + "=" + picked)
    chosen = chosen_all
    missing = sum(1 for group in groups if not group["chosen"])
    # Advancement never re-chooses a starting kit: a draft that already carries
    # equipment reports the step as satisfied instead of demanding a second set.
    carried = list(draft.equipment)
    if carried or draft.advancement:
        missing = 0
    return {
        "id": "equipment",
        "title": "Снаряжение",
        "status": (
            "complete" if not missing and (carried or groups or draft.advancement)
            else ("empty" if missing == len(groups) else "incomplete")
        ),
        "carried": carried,
        "required": not carried and not draft.advancement,
        "count": len(groups),
        "missing": missing,
        "chosen": chosen,
        "groups": groups,
        "reasons": reasons,
        "note": (
            "Комплекты выданы автоматически: игрок выбирает вариант комплекта,"
            " а не предметы из каталога."
        ),
    }


def _final_step(draft, ready_flags):
    chosen = [value for value in (draft.name.strip(),) if value]
    reasons = list(ready_flags)
    if not chosen:
        reasons.append("Введите имя героя.")
    return {
        "id": "final",
        "title": STEP_TITLES["final"],
        "status": "complete" if chosen and not ready_flags else "empty",
        "required": True,
        "count": 1,
        "missing": 0 if chosen else 1,
        "chosen": chosen,
        "options": [],
        "reasons": reasons,
        "name": draft.name,
        "backstory": draft.backstory,
    }


def _assign_picks(rows, draft):
    """Which step owns which pick, so each card shows only its own answers.

    Tokens are flat and two steps can share a pool (a class skill and a
    species skill both read ``skill:<slug>``), so the picks are dealt to the
    rows in native order and each token is spent once. The totals still come
    from ``pending_choices``; this only decides which card displays them.
    """
    tokens = [str(token) for token in draft.selected_choices]
    spent = set()
    owned = {}
    for row in rows:
        pool = set(row.pool)
        found = []
        increases = {}
        raw = []
        for index, text in enumerate(tokens):
            if index in spent:
                continue
            kind, _, rest = text.partition(":")
            if row.kind in ("increase", "asi"):
                if row.kind == "increase":
                    prefix = "background:"
                else:
                    prefix = ("asi:" + str(row.source).split(":", 1)[1]
                              + ":" + str(row.level) + ":")
                if not text.startswith(prefix):
                    continue
                body = text[len(prefix):]
                parsed = {}
                for part in body.split(","):
                    name, _, amount = part.partition("+")
                    if name and amount.isdigit():
                        parsed[name] = int(amount)
                if not parsed or not set(parsed) <= pool:
                    continue
                increases = parsed
                raw.append(text)
                spent.add(index)
                break
            if row.kind in ("skill", "expertise"):
                if kind != row.kind or rest not in pool or rest in found:
                    continue
                found.append(rest)
                raw.append(text)
                spent.add(index)
                if len(found) >= int(row.count):
                    break
                continue
            # A bare pool pick: fighting style, origin feat, invocation.
            if ":" in text or text not in pool or text in found:
                continue
            found.append(text)
            raw.append(text)
            spent.add(index)
            if len(found) >= int(row.count):
                break
        owned[row.key] = {"chosen": found, "increases": increases, "tokens": raw}
    return owned

def _feature_name(loader, slug):
    # r147: умения подклассов PHB 2024 лежат текстом в своём каталоге и не
    # имеют `Feature`-записи в пакете; без этой ветки лист показывал бы slug.
    from .nat20_canonical import feature_name
    doc = loader.get_feature(slug)
    if doc is not None:
        return str(doc.name)
    return feature_name(slug) or str(slug)


def _sheet_projection(sheet, loader):
    """The right-hand panel: what the native sheet says, nothing recomputed."""
    data = sheet.model_dump(mode="json")
    scores = dict(data.get("ability_scores") or {})
    modifiers = dict(data.get("ability_modifiers") or {})
    return {
        "ability_scores": [
            {"id": name, "name": ABILITY_TITLES[name],
             "score": scores.get(name), "modifier": modifiers.get(name)}
            for name in ABILITY_ORDER
        ],
        "hp_max": data.get("hp_max"),
        "ac": data.get("ac"),
        "ac_calc_mode": data.get("ac_calc_mode"),
        "speed": data.get("base_speed"),
        "proficiency_bonus": data.get("proficiency_bonus"),
        "hit_dice": data.get("hit_dice"),
        "save_proficiencies": list(data.get("save_proficiencies") or []),
        "skill_proficiencies": list(data.get("skill_proficiencies") or []),
        "skill_expertise": list(data.get("skill_expertise") or []),
        "weapon_proficiencies": list(data.get("weapon_proficiencies") or []),
        "armor_training": list(data.get("armor_training") or []),
        "passive_perception": data.get("passive_perception"),
        "features": [
            # r147: `source` — маркер происхождения (PHB 2024, стр. N) для
            # умений подклассов книги; у пакетных SRD-умений его нет.
            {"id": slug, "name": _feature_name(loader, slug), "source": _source_detail(slug)}
            for slug in (data.get("features") or [])
        ],
        "feats": [
            {"id": slug, "name": _feature_name(loader, slug)}
            for slug in (data.get("feats") or [])
        ],
        "spell_abilities": dict(data.get("spell_abilities") or {}),
        "spell_slots": {str(key): value for key, value in (data.get("spell_slots") or {}).items()},
        "pact_slots": {str(key): value for key, value in (data.get("pact_slots") or {}).items()},
        "senses": data.get("senses"),
    }


def _sheet(draft, loader):
    """The derived sheet for a draft that can already have one, plus its issues.

    An incomplete draft is normal mid-wizard, so an unanswerable draft returns
    no sheet and one reason rather than an exception.
    """
    from dnd5e_engine import CharacterBuildSpec, derive_sheet

    payload = draft.spec_payload()
    if payload is None:
        return None, ["Нужны вид и класс, чтобы собрать лист."]
    try:
        sheet = derive_sheet(CharacterBuildSpec(**payload), loader=loader)
    except ValueError as error:
        return None, [str(error)]
    return _sheet_projection(sheet, loader), []


def _equipment_projection(draft):
    grants = []
    issues = []
    for key, kit_key in sorted(draft.equipment_kits.items()):
        kind, _, source = key.partition(":")
        try:
            grants.append(grant(kind, source, kit_key, draft.kit_picks.get(key)))
        except ValueError as error:
            issues.append(str(error))
    total = sum(int(row["gold_gp"]) for row in grants)
    items = []
    for row in grants:
        for item in row["items"]:
            items.append(dict(item, kit=row["key"], source=row["source"]))
    carried = [{"slug": slug, "count": 1, "source": "carried"} for slug in draft.equipment]
    return {"grants": grants, "gold_gp": total, "items": items + carried}, issues


def _steps(draft, loader):
    """Every step the draft currently has, in the fixed screen order."""
    from dnd5e_engine.build_spec import CharacterBuildSpec
    from dnd5e_engine.rules.advancement import pending_choices, required_choices

    steps = _species_steps(draft, loader)
    steps.append(_class_step(draft, loader))
    subclass = _subclass_step(draft, loader)
    if subclass is not None:
        steps.append(subclass)
    steps.append(_background_step(draft, loader))
    steps.append(_scores_step(draft, loader))
    steps.append(_equipment_step(draft, loader))
    classes = {draft.class_slug: draft.level} if draft.class_slug else {}
    problems = []
    try:
        required = required_choices(
            classes=classes,
            loader=loader,
            species_slug=draft.species_slug,
            subclass_slug=draft.subclass_slug,
            background_slug=draft.background_slug,
        )
    except ValueError as error:
        # An unknown slug is the player's draft being wrong, not the server
        # failing: report it and keep rendering the step that chose it.
        required = ()
        problems.append(str(error))
    pending = ()
    counted = True
    payload = draft.spec_payload()
    if payload is not None:
        try:
            pending = pending_choices(CharacterBuildSpec(**payload), loader=loader)
        except ValueError as error:
            # The engine refused to count what is owed: the picks are unknown, not
            # settled. \`counted\` carries that into every choice step.
            problems.append(str(error))
            counted = False
    steps.extend(_choice_steps(draft, loader, required, pending, counted))
    magic = _magic_step(draft, loader)
    if magic is not None:
        steps.append(magic)
    blocking = [
        step
        for step in steps
        if step["required"] and step["status"] in ("empty", "incomplete", "invalid")
    ]
    notes = [
        str(step["title"]) + ": " + str(step["reasons"][0])
        for step in blocking
        if step["reasons"]
    ]
    steps.append(_final_step(draft, notes))
    return steps, blocking, problems


def _validate(draft, loader):
    """Native legality of the draft as it stands, as every endpoint runs it."""
    from dnd5e_engine.build_spec import CharacterBuildSpec
    from dnd5e_engine.rules.advancement import validate_choices

    payload = draft.spec_payload()
    if payload is None:
        return ["Нужны вид и класс, чтобы проверить героя."]
    try:
        spec = CharacterBuildSpec(**payload)
    except ValueError as error:
        return [str(error)]
    try:
        validate_choices(spec, loader=loader)
    except ValueError as error:
        return [str(error)]
    published = set(loader.list_slugs("spells"))
    return [
        "Такого заклинания нет в корпусе: " + slug
        for slug in draft.prepared_spells
        if slug not in published
    ]


def _token_for(row, answer):
    """One advancement token for one step answer, in the native grammar.

    The grammar (``skill:``, ``expertise:``, ``background:+2``, ``asi:``) is
    native protocol, not a rule: it belongs on this side of the boundary so
    the screen never assembles it and never drifts from the engine.
    """
    if row.kind in ("skill", "expertise"):
        return [str(row.kind) + ":" + str(slug) for slug in answer]
    if row.kind in ("increase", "asi"):
        if not isinstance(answer, dict):
            raise ValueError(
                "Повышение характеристик (" + row.key + ") ждёт значения по способностям."
            )
        parts = []
        for ability, points in answer.items():
            if str(ability) not in row.pool:
                raise ValueError(
                    str(ability) + " нельзя повысить этим выбором: допустимы "
                    + ", ".join(row.pool)
                )
            parts.append(str(ability) + "+" + str(int(points)))
        # A +2/+1 background increase is ONE answer spending three points, so the
        # budget the corpus publishes (points/cap) is what bounds it, not count.
        budget = int(row.points or 0)
        spent = 0
        for ability, points in answer.items():
            amount = int(points)
            if not 1 <= amount <= int(row.cap or 1):
                raise ValueError(
                    str(ability) + ": допустимо 1-" + str(row.cap) + ", получено " + str(amount)
                )
            spent += amount
        if budget and spent > budget:
            raise ValueError(
                "Нужно распределить " + str(budget) + " очко(в): получено " + str(spent)
            )
        if budget and spent < budget:
            # Половинчатый ответ — не ошибка, а недоделанный шаг: экран прибавляет
            # по одному очку («+» на характеристике), и до последнего клика токена
            # ещё нет. Отказом он гасил бы весь черновик, включая уже сделанные
            # выборы героя (owner, L03).
            return []
        if not budget and len(parts) != int(row.count):
            raise ValueError(
                "Выберите " + str(row.count) + " повышение(й): получено " + str(len(parts))
            )
        if row.kind == "increase":
            return ["background:" + ",".join(parts)]
        owner = str(row.source).split(":", 1)[1]
        return ["asi:" + owner + ":" + str(row.level) + ":" + ",".join(parts)]
    if not isinstance(answer, list):
        raise ValueError("Этот выбор (" + row.key + ") ждёт список вариантов.")
    return [str(slug) for slug in answer]


def resolve_answers(draft, answers):
    """The draft with ``selected_choices`` rebuilt from the screen's answers.

    The screen keeps one entry per advancement step it showed; this turns them
    into the tokens the native module reads, refusing a pick the step does not
    offer. Nothing is guessed: an answer for a step the build does not have is
    reported rather than silently dropped.
    """
    from dnd5e_engine.rules.advancement import required_choices

    loader = _frame()[0]
    draft = draft if isinstance(draft, WizardDraft) else WizardDraft.model_validate(draft)
    answers = dict(answers or {})
    if not answers:
        # A caller that speaks the native token grammar already (a saved build,
        # a level-up draft) sends no answers, and its picks are left alone.
        return draft, []
    classes = {draft.class_slug: draft.level} if draft.class_slug else {}
    rows = []
    problems = []
    if classes and draft.species_slug:
        try:
            rows = list(required_choices(
                classes=classes,
                loader=loader,
                species_slug=draft.species_slug,
                subclass_slug=draft.subclass_slug,
                background_slug=draft.background_slug,
            ))
        except ValueError as error:
            problems.append(str(error))
    tokens = []
    known = set()
    for row in rows:
        if row.kind in ("background", "subclass"):
            continue
        known.add(row.key)
        if row.key not in answers:
            continue
        value = answers[row.key]
        try:
            produced = _token_for(row, value)
        except ValueError as error:
            problems.append(str(error))
            continue
        for slug in produced:
            if slug not in row.pool and not row.pool:
                problems.append("Этот выбор не предлагает вариантов: " + row.key)
                produced = []
                break
        for slug in produced:
            if slug not in tokens:
                tokens.append(slug)
    # An answer for a step this build no longer has (the class or background
    # changed) is a leftover, not a token: it is dropped silently and the
    # screen says what it reset. Dropping cannot grant a pick the rules forbid,
    # because only tokens built from a live step are ever emitted.
    return draft.model_copy(update={"selected_choices": tokens, "choices": answers}), problems


def keep_settled_picks(previous, answers, resolved):
    """The advanced draft: the hero's settled picks plus this level's answers.

    ``resolve_answers`` rebuilds ``selected_choices`` from the answers alone, and
    a level-up answers only what the new level opens: sending that draft back
    dropped every skill, feature and increase the hero already had, the contract
    then reported the whole build owed, and the advancement screen fell back to
    «Навыки» while the new level's step disappeared. A step the player answered
    again replaces its own tokens - an ability-score token carries the level that
    owns it, so editing the increase does not stack with the earlier answer.
    """
    from dnd5e_engine.rules.advancement import required_choices

    previous = previous if isinstance(previous, WizardDraft) else WizardDraft.model_validate(previous)
    resolved = resolved if isinstance(resolved, WizardDraft) else WizardDraft.model_validate(resolved)
    if not resolved.advancement:
        return resolved
    prefixes = set()
    answers = dict(answers or {})
    if answers:
        classes = {resolved.class_slug: resolved.level} if resolved.class_slug else {}
        try:
            rows = list(required_choices(
                classes=classes,
                loader=_frame()[0],
                species_slug=resolved.species_slug,
                subclass_slug=resolved.subclass_slug,
                background_slug=resolved.background_slug,
            ))
        except ValueError:
            # The issue list already names the illegal reference; the picks are
            # still the hero's own and nothing here should throw them away.
            rows = []
        for row in rows:
            if row.key not in answers:
                continue
            if row.kind == "asi":
                prefixes.add("asi:" + str(row.source).split(":", 1)[1] + ":" + str(row.level) + ":")
            elif row.kind == "increase":
                prefixes.add("background:")
    kept = [
        str(token) for token in previous.selected_choices
        if not any(str(token).startswith(prefix) for prefix in prefixes)
    ]
    merged = list(dict.fromkeys(kept + [str(token) for token in resolved.selected_choices]))
    return resolved.model_copy(update={"selected_choices": merged})


#: Russian words for the kinds of pick a level can take away. The screen says
#: what it dropped; the keys themselves stay the engine's own tokens.
DROP_TITLES = {
    "increase": "прибавка предыстории",
    "asi": "повышение характеристик",
    "skill": "навык",
    "expertise": "компетентность",
    "feature": "особенность",
    "mastery": "мастерство оружием",
    "invocation": "воззвание",
}


def fit_level_picks(draft, answers=None):
    """The draft carrying only the picks its own level can take.

    The level is a parameter of the build, so the player may lower it after
    answering a higher one. Those picks then name a level the build does not
    have, and the engine refuses the whole draft («selected_choices uses the
    Ability Score Improvement at wizard level 8, which this build does not
    have»): the sheet disappears and the screen says «лист пока не выводится»
    instead of dropping two picks. Fitting happens here, in one place, and a
    draft that already fits comes back unchanged — raising the level never
    loses an answer the new level still asks for.

    Returns ``(draft, answers, dropped, problems)``: ``dropped`` names each pick
    the level no longer offers, so the screen can say what it took away.
    """
    from dnd5e_engine.rules.advancement import required_choices, subclass_gate

    draft = draft if isinstance(draft, WizardDraft) else WizardDraft.model_validate(draft)
    # The screen that suggested a build keeps its picks in ``choices`` and sends
    # no answers afterwards (the suggestion fills the draft, not the answer
    # sheet). Reading only the answers would leave every pick of the old level
    # in place, and the engine would refuse the whole draft.
    answers = dict(answers if answers else (draft.choices or {}))
    loader = _frame()[0]
    dropped = []
    gate = (
        subclass_gate(draft.class_slug, loader=loader)
        if draft.class_slug and draft.class_slug in loader.list_slugs("classes")
        else None
    )
    if draft.subclass_slug and gate is not None and int(draft.level) < int(gate):
        # A subclass is a field, not an answer: below its gate it is an illegal
        # reference, and the screen still asks for it once the level reaches it.
        draft = draft.model_copy(update={"subclass_slug": None})
        dropped.append({"key": "subclass", "title": STEP_TITLES["subclass"],
                        "level": int(gate)})
    # A draft that speaks the native token grammar sends no answers (a saved
    # build, a level-up draft); there is nothing to rebuild its tokens from, and
    # its picks were never made for another level.
    if not answers or not (draft.class_slug and draft.species_slug):
        return draft, answers, dropped, []
    try:
        rows = list(required_choices(
            classes={draft.class_slug: int(draft.level)},
            loader=loader,
            species_slug=draft.species_slug,
            subclass_slug=draft.subclass_slug,
            background_slug=draft.background_slug,
        ))
    except ValueError as error:
        # The engine cannot say what this level owes (an illegal reference is
        # already named by `contract`); wiping the answers would lose them.
        return draft, answers, dropped, [str(error)]
    live = {row.key for row in rows}
    kept = {key: value for key, value in answers.items() if key in live}
    for key in answers:
        if key in live:
            continue
        parts = key.split(":")
        kind = parts[2] if len(parts) > 2 else ""
        try:
            level = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            level = 0
        dropped.append({"key": key, "title": DROP_TITLES.get(kind, kind or key),
                        "level": level})
    if not kept:
        # Every answer came from a level this build does not have: the tokens
        # built from them must go too, or the engine keeps refusing the draft.
        stripped = draft.model_copy(update={"selected_choices": [], "choices": {}})
        return _fit_prepared_spells_draft(stripped, loader, dropped), kept, dropped, []
    fitted, problems = resolve_answers(draft, kept)
    return _fit_prepared_spells_draft(fitted, loader, dropped), kept, dropped, list(problems)


def _fit_prepared_spells(draft, loader):
    """The prepared spells this level can still cast, and how many it cannot.

    A spell is a pick of a level no less than an Ability Score Improvement is:
    a third-circle spell on a build whose slots stop at the second is a name on
    the sheet that nothing can pay for. The corpus publishes the ceiling (from
    the sheet's own slots) and the counts (class ScaleValues), so both bounds
    are read there, never hardcoded.
    """
    if not draft.prepared_spells:
        return [], 0
    ceiling = _slot_ceiling(draft, loader)
    try:
        limits = {row["name"]: int(row["value"]) for row in _magic_limits(draft, loader)}
    except ValueError:
        limits = {}
    want_cantrips, want_spells = limits.get("cantrips-known"), limits.get("max-prepared")
    kept, cantrips, spells, lost = [], 0, 0, 0
    for slug in draft.prepared_spells:
        spell = loader.get_spell(slug)
        if spell is None:
            lost += 1
            continue
        level = int(spell.level)
        if level and level > ceiling:
            lost += 1
            continue
        if level == 0:
            if want_cantrips is not None and cantrips >= want_cantrips:
                lost += 1
                continue
            cantrips += 1
        else:
            if want_spells is not None and spells >= want_spells:
                lost += 1
                continue
            spells += 1
        kept.append(slug)
    return kept, lost


def _fit_prepared_spells_draft(draft, loader, dropped):
    """``draft`` with the spells of the old level removed and named in ``dropped``."""
    kept, lost = _fit_prepared_spells(draft, loader)
    if not lost:
        return draft
    dropped.append({"key": "prepared_spells", "level": 0,
                    "title": "заклинания, которых этот уровень не берёт"})
    return draft.model_copy(update={"prepared_spells": kept})


def suggested_build(*, class_slug="rogue", species_slug="human", background_slug="soldier",
                    level=1, ability_scores=None, equipment=(), rounds=40):
    """A starting build the server itself accepts, for a default value.

    The advanced catalogue opens with a draft; a draft that owes skills,
    expertise, a background increase or an origin feat is refused, and rightly
    so. This answers each blocking step from the pool the contract published and
    returns the native build payload, so the default is legal without the screen
    restating a single rule.
    """
    draft, final = suggested_draft(
        class_slug=class_slug, species_slug=species_slug,
        background_slug=background_slug, level=level,
        ability_scores=ability_scores, equipment=equipment, rounds=rounds,
    )
    return dict(final["build"])


def recommend_draft(draft, *, rounds=40):
    """A legal suggestion that keeps the choices the player already made.

    The suggestion fills only what is still owed: a hero the table built by hand
    is not silently replaced. The screen shows the result and applies it only
    when the player asks, so a recommendation never confirms anything itself.
    """
    draft = draft if isinstance(draft, WizardDraft) else WizardDraft.model_validate(draft)
    species = draft.species_slug or "human"
    klass = draft.class_slug or "rogue"
    background = draft.background_slug or "soldier"
    scores = dict(draft.ability_scores) or None
    equipment = tuple(draft.equipment or ())
    try:
        filled, final = suggested_draft(
            class_slug=klass, species_slug=species, background_slug=background,
            level=int(draft.level or 1), ability_scores=scores,
            equipment=equipment, rounds=rounds,
        )
    except ValueError as error:
        return {
            "ok": False,
            "reason": str(error),
            "draft": draft.model_dump(mode="json"),
            "kept": [],
        }
    # The player's own decisions survive the suggestion: only unanswered steps
    # are filled from it.
    chosen = draft.model_dump(mode="json")
    suggested = filled.model_dump(mode="json")
    merged = {}
    kept = []
    for key, mine in chosen.items():
        theirs = suggested.get(key)
        if key in ("equipment_kits", "kit_picks"):
            # A kit is chosen per group. Keeping the player's whole dict because
            # one group was answered dropped the groups the suggestion filled,
            # and the equipment step — which the screen never shows — stayed
            # blocking, so the hero could not be confirmed at all.
            merged[key] = {**(theirs or {}), **(mine or {})}
            if mine:
                kept.append(key)
            continue
        if mine in (None, "", [], {}, 0) or key == "level":
            merged[key] = theirs if key != "level" else mine
        else:
            merged[key] = mine
            kept.append(key)
    result = WizardDraft.model_validate(merged)
    state = contract(result)
    return {
        "ok": bool(state["ready"]),
        "reason": "" if state["ready"] else "Предложение пока не закрывает все шаги.",
        "draft": result.model_dump(mode="json"),
        "kept": sorted(kept),
        "ready": bool(state["ready"]),
        "issues": list(state["issues"])[:6],
    }


def suggested_draft(*, class_slug="rogue", species_slug="human",
                    background_slug="soldier", level=1, ability_scores=None,
                    equipment=(), rounds=40):
    """The same default as a draft, so spells travel with it.

    suggested_build returns only the native spec, and prepared spells are not
    part of a spec: a caster's default has to carry them separately.
    """
    scores = dict(ability_scores or _DEFAULT_SCORES)
    draft = WizardDraft(
        species_slug=species_slug,
        class_slug=class_slug,
        background_slug=background_slug,
        level=int(level),
        ability_score_method="point_buy",
        ability_scores=scores,
        equipment=list(equipment),
    )
    answers = {}
    for _ in range(rounds):
        state = contract(draft)
        # A starting kit is granted, not picked from the catalogue: take the
        # recommended row of each group the contract published.
        kits = dict(draft.equipment_kits)
        for step in state["steps"]:
            if step["id"] != "equipment":
                continue
            for group in step.get("groups") or ():
                if group.get("recommended") and group["id"] not in kits:
                    kits[group["id"]] = group["recommended"]
        if kits != draft.equipment_kits:
            draft = draft.model_copy(update={"equipment_kits": kits})
            continue
        # Magic is not a choice_key step: it is a count over two published pools,
        # so it needs its own answer or a caster can never have a legal default.
        magic = next((row for row in state["steps"] if row["id"] == "magic"), None)
        if magic is not None and int(magic.get("missing") or 0) > 0:
            counts = {
                row["name"]: int(row["value"])
                for row in (magic.get("limits") or ())
            }
            picks = list(draft.prepared_spells)
            for group, key in (("cantrips", "cantrips-known"), ("spells", "max-prepared")):
                need = max(0, counts.get(key, 0) - len(magic.get("picked_" + group) or ()))
                for row in magic.get(group) or ():
                    if need <= 0:
                        break
                    if row.get("disabled_reason") or row["id"] in picks:
                        continue
                    picks.append(row["id"])
                    need -= 1
            if picks != draft.prepared_spells:
                draft = draft.model_copy(update={"prepared_spells": picks})
                continue
        # A subclass is not a choice_key answer: the screen picks it by writing
        # subclass_slug, and the step publishes no key of its own. A suggested
        # default must answer it the same way, or a legal hero above this gate
        # level can never be produced and the prepared world cannot be entered.
        subclass = next((row for row in state["steps"] if row["id"] == "subclass"), None)
        if subclass is not None and not draft.subclass_slug:
            first = next((row["id"] for row in (subclass.get("options") or ()) if row.get("id")), None)
            if first:
                draft = draft.model_copy(update={"subclass_slug": first})
                continue
        open_steps = [
            step for step in state["steps"]
            if step.get("choice_key") and step.get("required")
            and int(step.get("missing") or 0) > 0
        ]
        if not open_steps:
            break
        step = open_steps[0]
        answer = _answer_from_step(step, used=draft.selected_choices,
                                   scores=_sheet_scores(state))
        if answer in (None, [], {}):
            break
        answers[step["choice_key"]] = answer
        draft, _problems = resolve_answers(draft, answers)
    final = contract(draft)
    if not final["ready"] or not final["build"]:
        raise ValueError(
            "Не удалось собрать допустимый стартовый лист: "
            + "; ".join(final["issues"] or final["blocking"])
        )
    return draft, final


_DEFAULT_SCORES = dict(strength=10, dexterity=14, constitution=12,
                       intelligence=10, wisdom=12, charisma=10)


def _answer_from_step(step, used=(), scores=None):
    """One answer for one blocking step, taken from its own published options.

    Two copies of the same kind of pick (a second fighting style, a second
    invocation) cannot carry the same slug: the token list is a set, so a
    duplicate answers the first step and leaves the second owed forever — the
    suggestion loop then retried the first option until it gave up and the hero
    above first level could never be recommended. Options already spent are
    therefore skipped while a fresh one remains.

    ``scores`` is the sheet's own ability totals. An Ability Score Improvement
    stops at 20 in the engine, so three improvements on one 15 would build a
    hero the engine refuses («would raise strength to 21»): points are placed
    where the ceiling still allows them.
    """
    kind = step.get("kind")
    options = [row["id"] for row in (step.get("options") or []) if row.get("id")]
    spent = {str(item) for item in used or ()}
    fresh = [option for option in options if option not in spent] or options
    missing = int(step.get("missing") or 0)
    if kind in ("increase", "asi"):
        points = int(step.get("points") or 1)
        cap = int(step.get("cap") or 1)
        totals = {str(key): int(value) for key, value in (scores or {}).items()}
        chosen, left = {}, points
        while left > 0:
            room = [option for option in options
                    if totals.get(option, 10) < 20 and chosen.get(option, 0) < cap]
            if not room:
                break
            best = max(room, key=lambda option: (totals.get(option, 10), -options.index(option)))
            chosen[best] = chosen.get(best, 0) + 1
            totals[best] = totals.get(best, 10) + 1
            left -= 1
        return chosen
    if kind in ("skill", "feature", "mastery", "language", "tool"):
        # A second copy of the same kind of pick (a second fighting style, a
        # second invocation) needs a different slug; a skill already spent is
        # refused by the engine as well. ``expertise`` is not in this list: its
        # options are the skills the hero is already proficient in, and taking a
        # «fresh» one would offer expertise in a skill he never learned.
        if len(fresh) >= missing:
            return fresh[:missing]
        return options[:missing]
    if kind == "expertise":
        return options[:missing]
    return fresh[0] if fresh else None


def _sheet_scores(state):
    """Ability totals as the sheet derived them, for the steps that add to them."""
    rows = (state.get("sheet") or {}).get("ability_scores") or ()
    return {str(row.get("id")): int(row["score"]) for row in rows
            if row.get("id") and row.get("score") is not None}



def contract(draft, *, loader=None, release_fingerprint=None):
    """The whole wizard state for one draft: steps, sheet, kit, legality.

    The same call serves creation and level-up: level-up sends a draft whose
    class level is one higher and gets, back, the new steps plus the sheet the
    native engine derives for it, so both screens share one admissibility path.
    """
    from dnd5e_engine.build_spec import CharacterBuildSpec
    from dnd5e_engine.rules.advancement import pending_choices

    if loader is None:
        loader, fingerprint, _profile = _frame()
        release_fingerprint = release_fingerprint or fingerprint
    elif release_fingerprint is None:
        release_fingerprint = _frame()[1]
    draft = draft if isinstance(draft, WizardDraft) else WizardDraft.model_validate(draft)
    steps, blocking, problems = _steps(draft, loader)
    if draft.advancement:
        # Advancement only shows what the new level opens; creation-only steps
        # (a starting kit, the six scores) are settled and never re-asked.
        blocking = [step for step in blocking if step["id"] not in ("equipment", "scores", "species", "class", "background")]
    # Имя героя — обязательное поле создания (`NewGameInput.name`, min_length=1), но шаг
    # `final` не входил в `blocking`: договор объявлял героя готовым, кнопка
    # «Подтвердить героя» включалась, подтверждение уходило в 422, и игрок видел
    # «Не удалось обработать запрос». Отдельное поле называет причину, не меняя
    # значение `ready` для остальных проверок договора.
    confirm_issues = []
    if not draft.advancement and not draft.name.strip():
        confirm_issues.append("Впишите имя героя: без него герой не создаётся.")
    sheet, sheet_issues = _sheet(draft, loader)
    equipment, equipment_issues = _equipment_projection(draft)
    issues = _validate(draft, loader)
    pending = []
    payload = draft.spec_payload()
    if payload is not None:
        try:
            pending = [
                {
                    "key": item.choice.key,
                    "kind": item.choice.kind,
                    "missing": int(item.missing),
                    "title": item.choice.title,
                    "source": item.choice.source,
                }
                for item in pending_choices(CharacterBuildSpec(**payload), loader=loader)
            ]
        except ValueError:
            # The issue list already names the illegal reference; an empty
            # pending list keeps the screen rendering the step that caused it.
            pending = []
    return {
        "version": WIZARD_VERSION,
        "release": release_fingerprint,
        "draft": draft.model_dump(mode="json"),
        # The exact build the engine would receive, so a launch posts what the
        # player confirmed instead of re-assembling it in the browser.
        "build": draft.spec_payload(),
        "ready": not issues and not blocking,
        # То, без чего кнопку «Подтвердить героя» включать нельзя, хотя механически
        # герой законен: сейчас это имя (его требует создание кампании).
        "confirm_ready": not issues and not blocking and not confirm_issues,
        "confirm_issues": confirm_issues,
        "steps": steps,
        "blocking": [step["id"] for step in blocking],
        "pending": pending,
        "issues": list(issues) + list(problems) + list(sheet_issues) + list(equipment_issues),
        "sheet": sheet,
        # Why there is no sheet yet, in the engine's own words. The screen used to
        # answer every missing sheet with "choose species and class", which is
        # false once both are chosen and the engine refuses for another reason.
        "sheet_issues": list(sheet_issues),
        "sheet_ready": sheet is not None,
        "equipment": equipment,
        "name": draft.name,
        "backstory": draft.backstory,
    }


def level_up_draft(draft, *, loader=None):
    """The same draft one class level higher, every existing pick preserved.

    Returns the advanced draft and the decisions it opens: the native
    ``advance`` builds the new spec (a fresh construction, because
    ``CharacterBuildSpec`` reconciles level and classes in a before-validator
    that ``model_copy`` would skip) and the difference between the two pending
    lists names exactly what the extra level now demands.
    """
    from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
    from dnd5e_engine.rules.advancement import advance, pending_choices

    if loader is None:
        loader = _frame()[0]
    draft = draft if isinstance(draft, WizardDraft) else WizardDraft.model_validate(draft)
    payload = draft.spec_payload()
    if payload is None:
        raise ValueError("Нужны вид и класс, чтобы повысить уровень.")
    spec = CharacterBuildSpec(**payload)
    if not draft.class_slug:
        raise ValueError("Нужен класс, чтобы повысить уровень.")
    before = {item.choice.key: int(item.missing) for item in pending_choices(spec, loader=loader)}
    advanced = advance(spec, class_slug=draft.class_slug, loader=loader)
    after = {
        item.choice.key: int(item.missing)
        for item in pending_choices(advanced, loader=loader)
    }
    opened = [key for key in sorted(after) if after[key] - before.get(key, 0) > 0]
    was = _sheet_projection(derive_sheet(spec, loader=loader), loader)
    will = _sheet_projection(derive_sheet(advanced, loader=loader), loader)
    was_features = {row["id"] for row in was["features"]}
    was_feats = {row["id"] for row in was["feats"]}
    was_slots = dict(was["spell_slots"])
    granted_slots = {
        level: count
        for level, count in will["spell_slots"].items()
        if count != was_slots.get(level, 0)
    }
    # "было -> станет" is a projection of two native sheets, so every number the
    # player sees on the confirmation is one the engine already computed.
    return {
        "from_level": int(spec.level),
        "to_level": int(advanced.level),
        "draft": draft.model_copy(update={"level": int(advanced.level)}),
        "opened": opened,
        "pending": [
            {"key": key, "before": before.get(key, 0), "after": after[key]}
            for key in opened
        ],
        "before": was,
        "after": will,
        "hp_gain": int(will["hp_max"] or 0) - int(was["hp_max"] or 0),
        "granted_features": [
            row for row in will["features"] if row["id"] not in was_features
        ],
        "granted_feats": [
            row for row in will["feats"] if row["id"] not in was_feats
        ],
        "granted_slots": {str(level): count for level, count in granted_slots.items()},
    }


__all__ = [
    "ABILITY_ORDER",
    "ABILITY_TITLES",
    "CHOICE_TITLES",
    "STEP_ORDER",
    "STEP_TITLES",
    "WIZARD_VERSION",
    "WizardDraft",
    "contract",
    "fit_level_picks",
    "keep_settled_picks",
    "level_up_draft",
    "resolve_answers",
]
