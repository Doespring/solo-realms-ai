"""SRD 5.2 advancement: what a level obliges a player to choose, and when.

``derive_sheet`` applies whatever ``selected_choices`` it is handed. It checks
that a bare pick belongs to a feature-choice pool the build has reached, and
that an Ability Score Improvement token lands on a real slot. It never asks
whether the build made *every* choice its levels oblige, and it never checks a
skill pick against the skill pool that offered it. A Fighter 1 with no skill
picks, a Fighter 4 with no Ability Score Improvement, a level-3 Rogue with no
subclass and a Wizard who picked Athletics all derive a sheet today.

This module is the missing half. It reads the same canonical advancement data
``derive_sheet`` reads — ``Class.advancement`` / ``Class.feature_choices`` and
the species, subclass and background equivalents — and answers four questions
in one place, so a host never re-implements the arithmetic:

* ``required_choices`` — every decision the build's levels ask for;
* ``pending_choices`` — which of those the build has not spent yet;
* ``validate_choices`` — raise ``ChoiceError`` for a build that owes a pick or
  overspent one;
* ``choice_options`` — the same decisions with resolved option lists, which is
  what a character-creation screen renders.

``level_for_experience`` carries the SRD 5.2 Character Advancement table, which
the pinned Foundry import does not ship (Foundry keeps it in system
configuration, not in a compendium document), so campaign progression has one
native source too.

Scope note: weapon-mastery, language and tool pools are reported as *advisory*
choices. The pinned corpus has no tool items at all and ``DerivedSheet`` has no
field for a weapon mastery, a language or a tool proficiency, so demanding such
a pick would require a choice that provably changes nothing. Everything a sheet
can prove — skills, expertise, Ability Score Improvement or feat, subclass, and
feature pools such as a fighting style or a Divine Order — is blocking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from dnd5e_srd_data.loader import AssetLoader
from dnd5e_srd_data.schema.advancement import AdvancementType
from dnd5e_srd_data.schema.class_ import Class, Subclass
from dnd5e_srd_data.schema.species import Species

from dnd5e_engine.rules.character import (
    ABILITY_NAME_BY_CODE,
    ability_score_improvement,
    subclass_gate_level,
)
from dnd5e_engine.rules.choices import ParsedChoices, parse_selected_choices
from dnd5e_engine.rules.skills import SKILL_CODE_TO_SLUG

#: SRD 5.2 "Character Advancement": experience needed for each level. Index 0 is
#: level 1, so ``XP_THRESHOLDS[3]`` is the 2 700 that makes level 4.
XP_THRESHOLDS: tuple[int, ...] = (
    0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
    85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
)

#: Pool token prefix → the kind of pick it asks for. A ``Trait`` with
#: ``mode="mastery"`` is the same thing written the other way.
_POOL_KIND: Mapping[str, str] = {
    "skills": "skill",
    "weapon": "mastery",
    "languages": "language",
    "tool": "tool",
}

#: Kinds a derived sheet can prove, so a missing pick blocks the build.
BLOCKING_KINDS: frozenset[str] = frozenset(
    {"background", "increase", "skill", "expertise", "asi", "feature", "subclass"}
)

#: Kinds nothing in the sheet can prove; reported, never blocking.
ADVISORY_KINDS: frozenset[str] = frozenset({"mastery", "language", "tool"})


class ChoiceError(ValueError):
    """A build that owes an advancement pick, or overspent one."""

    def __init__(self, message: str, *, pending: Sequence["Pending"] = ()) -> None:
        super().__init__(message)
        self.pending: tuple[Pending, ...] = tuple(pending)


@dataclass(frozen=True)
class Choice:
    """One advancement decision the canonical data asks for at one level."""

    kind: str
    source: str
    level: int
    count: int
    pool: tuple[str, ...] = ()
    mode: str = "default"
    title: str = ""
    points: int = 0
    cap: int = 0
    locked: tuple[str, ...] = ()
    replacement: bool = False
    hint: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.kind}:{self.level}:{self.title}"

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_KINDS


@dataclass(frozen=True)
class Pending:
    """A ``Choice`` and how many of its picks are still owed."""

    choice: Choice
    missing: int


def _kind_of(mode: str, pool: Sequence[str]) -> str:
    if mode == "expertise":
        return "expertise"
    if mode == "mastery":
        return "mastery"
    for token in pool:
        kind = _POOL_KIND.get(str(token).split(":", 1)[0])
        if kind is not None:
            return kind
    return "feature"


def _expand_skills(token: str) -> tuple[str, ...]:
    """``skills:*`` → every skill slug; ``skills:acr`` → ``acrobatics``."""
    _, _, rest = str(token).partition(":")
    if rest in ("", "*"):
        return tuple(dict.fromkeys(SKILL_CODE_TO_SLUG.values()))
    slug = SKILL_CODE_TO_SLUG.get(rest)
    return (slug,) if slug is not None else ()


def _resolve_pool(tokens: Sequence[str], loader: AssetLoader | None) -> tuple[str, ...]:
    """Canonical option slugs for a pool, or the raw tokens when unresolvable.

    Skill pools resolve through ``SKILL_CODE_TO_SLUG``. Weapon-mastery pools
    resolve through the corpus ``weapon_category`` so a screen can list real
    weapons. Language and tool pools have no corpus entries at all, so their
    wildcard tokens are returned unchanged rather than invented.
    """
    out: list[str] = []
    for token in tokens:
        token = str(token)
        kind, _, rest = token.partition(":")
        if kind == "skills":
            out.extend(_expand_skills(token))
        elif kind == "weapon" and loader is not None:
            out.extend(_weapons(rest, loader))
        else:
            out.append(token)
    return tuple(dict.fromkeys(out))


def _weapons(rest: str, loader: AssetLoader) -> list[str]:
    """Weapon slugs for a ``weapon:`` token, by ``weapon_category``."""
    if rest in ("sim", "sim:*"):
        wanted = ("simple_melee", "simple_ranged")
    elif rest in ("mar", "mar:*"):
        wanted = ("martial_melee", "martial_ranged")
    else:
        wanted = ("simple_melee", "simple_ranged", "martial_melee", "martial_ranged")
    found: list[str] = []
    for slug in loader.list_slugs("items"):
        weapon = loader.get_weapon(slug)
        if weapon is not None and str(weapon.weapon_category) in wanted:
            found.append(slug)
    return found


def _role(index: int) -> str:
    return "primary" if index == 0 else "secondary"


def _traits(
    source: str, doc, reached: int, role: str | None, loader: AssetLoader | None
) -> list[Choice]:
    """``Trait`` advancement rows reached at or below ``reached``.

    ``class_restriction`` is Foundry's multiclass switch: ``primary`` rows
    belong to the class taken at level 1, ``secondary`` rows to a class
    multiclassed into, and an empty restriction applies to everyone. Counting
    both rows of a single-class Rogue would demand five skills where the SRD
    grants four.
    """
    out: list[Choice] = []
    slug = str(getattr(doc, "slug", ""))
    for entry in getattr(doc, "advancement", ()) or ():
        if entry.type != AdvancementType.TRAIT or entry.level > reached:
            continue
        if entry.class_restriction and entry.class_restriction != role:
            continue
        config = entry.configuration or {}
        mode = str(config.get("mode") or "default")
        for raw in config.get("choices") or ():
            count = int(raw.get("count") or 0)
            tokens = tuple(str(token) for token in (raw.get("pool") or ()))
            if count <= 0 or not tokens:
                continue
            out.append(
                Choice(
                    kind=_kind_of(mode, tokens),
                    source=f"{source}:{slug}",
                    level=entry.level,
                    count=count,
                    pool=_resolve_pool(tokens, loader),
                    mode=mode,
                    title=str(entry.title or ""),
                    replacement=bool(config.get("allowReplacements")),
                    hint=str(entry.hint or ""),
                )
            )
    return out


def _feature_choices(source: str, doc, reached: int) -> list[Choice]:
    """``feature_choices`` pools and their per-level schedule.

    This is the typed carrier ``derive_sheet`` itself uses to accept a bare
    pick (``build_spec._choice_options``), so a fighting style, a Divine Order,
    a Goliath ancestry or a Warlock invocation is counted here and nowhere
    else. The parallel ``ItemChoice`` advancement rows describe the same pools
    in Foundry's item shape and are deliberately not double-counted.
    """
    out: list[Choice] = []
    slug = str(getattr(doc, "slug", ""))
    name = str(getattr(doc, "name", slug))
    for group in getattr(doc, "feature_choices", ()) or ():
        pool = tuple(option.slug for option in group.pool)
        if not pool:
            continue
        for step in group.schedule:
            if step.count <= 0 or step.level > reached:
                continue
            out.append(
                Choice(
                    kind="feature",
                    source=f"{source}:{slug}",
                    level=step.level,
                    count=step.count,
                    pool=pool,
                    # Foundry leaves ``restriction_subtype`` empty for most
                    # pools (Divine Order, Primal Order), so the fallback names
                    # the document and level rather than inventing a rule term.
                    title=str(group.restriction_subtype or f"{name} choice, level {step.level}"),
                    replacement=bool(step.replacement),
                )
            )
    return out


def _asi_choices(cls: Class, reached: int) -> list[Choice]:
    out: list[Choice] = []
    for entry in cls.advancement:
        if entry.type != AdvancementType.ABILITY_SCORE_IMPROVEMENT or entry.level > reached:
            continue
        slot = ability_score_improvement(cls, entry.level)
        if slot is None:
            continue  # A fixed grant (Monk 20 Body and Mind), not a player choice.
        config = slot.configuration or {}
        locked = tuple(str(code) for code in config.get("locked") or ())
        # Foundry records the abilities a slot may NOT raise; the pool a screen
        # offers is the complement, resolved to ability names.
        pool = tuple(
            name for code, name in ABILITY_NAME_BY_CODE.items() if code not in locked
        )
        out.append(
            Choice(
                kind="asi",
                source=f"class:{cls.slug}",
                level=slot.level,
                count=1,
                pool=pool,
                points=int(config.get("points") or 0),
                cap=int(config.get("cap") or 2),
                locked=locked,
                title=str(slot.title or "Ability Score Improvement"),
            )
        )
    return out

def required_choices(
    *,
    classes: Mapping[str, int],
    loader: AssetLoader,
    species_slug: str | None = None,
    subclass_slug: str | None = None,
    background_slug: str | None = None,
) -> tuple[Choice, ...]:
    """Every advancement decision the given levels ask for.

    A multiclass build reads each class at its OWN level (SRD 5.2
    Multiclassing: "When you gain a new level in a class, you get its features
    for that level"), so a Fighter 1 / Wizard 4 owes the Fighter's level-1
    choices only, and only the first class's ``primary`` rows.
    """
    out: list[Choice] = []
    for index, (slug, level) in enumerate(classes.items()):
        cls = loader.get_class(slug)
        if cls is None:
            raise ValueError(f"unknown class: {slug!r}")
        reached = int(level)
        out.extend(_traits("class", cls, reached, _role(index), loader))
        out.extend(_feature_choices("class", cls, reached))
        out.extend(_asi_choices(cls, reached))
        gate = subclass_gate_level(cls)
        if gate is not None and reached >= gate:
            out.append(
                Choice(
                    kind="subclass",
                    source=f"class:{slug}",
                    level=gate,
                    count=1,
                    pool=tuple(
                        str(row["slug"]) for row in subclass_options(slug, reached, loader=loader)
                    ),
                    title="Subclass",
                )
            )
    if subclass_slug is not None:
        subclass = loader.get_subclass(subclass_slug)
        if subclass is None:
            raise ValueError(f"unknown subclass: {subclass_slug!r}")
        owned = int(classes.get(subclass.class_identifier, 0))
        out.extend(_traits("subclass", subclass, owned, None, loader))
        out.extend(_feature_choices("subclass", subclass, owned))
    if species_slug is not None:
        species = loader.get_species(species_slug)
        if species is None:
            raise ValueError(f"unknown species: {species_slug!r}")
        out.extend(_traits("species", species, 0, None, loader))
        out.extend(_feature_choices("species", species, 0))
    # SRD 5.2 Character Creation: a background is chosen, not optional — it
    # supplies the +2/+1 ability increase and the Origin feat. Foundry keeps the
    # eligible abilities in ``ability_options``, so the pool is corpus data, not
    # a list written here.
    out.append(
        Choice(
            kind="background",
            source="background",
            level=0,
            count=1,
            pool=tuple(sorted(loader.list_slugs("backgrounds"))),
            title="Background",
        )
    )
    if background_slug is not None:
        background = loader.get_background(background_slug)
        if background is None:
            raise ValueError(f"unknown background: {background_slug!r}")
        options = background.ability_options
        # The +2/+1 the background grants is a second, separate decision with its
        # own budget: Foundry records the eligible abilities, the cap per ability
        # and the total points, so the screen renders the real pool.
        out.append(
            Choice(
                kind="increase",
                source=f"background:{background_slug}",
                level=0,
                count=1,
                pool=tuple(
                    sorted(ABILITY_NAME_BY_CODE[code] for code in options.options)
                ),
                title=str(background.name),
                points=int(options.points),
                cap=int(options.cap),
            )
        )
    return tuple(
        sorted(out, key=lambda c: (c.source, c.level, c.kind, c.title, c.count))
    )


def _allocate(counts: Sequence[int], spent: int) -> list[int]:
    """Hand ``spent`` picks to the earliest choices first; return what each owes."""
    remaining = spent
    missing: list[int] = []
    for count in counts:
        used = min(remaining, count)
        remaining -= used
        missing.append(count - used)
    return missing


def _match_features(
    picks: Sequence[str],
    choices: Sequence[Choice],
    *,
    resistances: Sequence[str] = (),
    immunities: Sequence[str] = (),
) -> tuple[list[Pending], list[str]]:
    """Match bare picks to the feature pools that offer them.

    Returns what each pool still owes and the picks no due pool offers — the
    latter are the overspend ``validate_choices`` refuses. A pool that opened at
    a lower level keeps taking picks before a later pool of the same family, so a
    Warlock's four invocations are one running budget.
    """
    # A chosen resistance travels in its own tuple (dr:<type>), not in picks,
    # but it fills a pool written exactly that way. Fold it back in so the
    # pick counts and the build stops owing it.
    taken = list(picks) + [f"dr:{slug}" for slug in resistances] + [f"di:{slug}" for slug in immunities]
    pending: list[Pending] = []
    for choice in choices:
        matched = 0
        for slug in list(taken):
            if slug in choice.pool and matched < choice.count:
                taken.remove(slug)
                matched += 1
        if matched < choice.count:
            pending.append(Pending(choice, choice.count - matched))
    return pending, taken


def pending_choices(spec, *, loader: AssetLoader) -> tuple[Pending, ...]:
    """The advancement decisions ``spec`` still owes, blocking and advisory."""
    choices = parse_selected_choices(spec.selected_choices)
    required = required_choices(
        classes=spec.classes,
        loader=loader,
        species_slug=spec.species_slug,
        subclass_slug=spec.subclass_slug,
        background_slug=getattr(spec, "background_slug", None),
    )
    out: list[Pending] = []
    by_kind: dict[str, list[Choice]] = {}
    for choice in required:
        by_kind.setdefault(choice.kind, []).append(choice)

    for kind, spent in (
        ("skill", len(choices.skills)),
        ("expertise", len(choices.expertise)),
    ):
        due = by_kind.get(kind, [])
        for choice, missing in zip(due, _allocate([c.count for c in due], spent)):
            if missing > 0:
                out.append(Pending(choice, missing))

    feature_like = [
        *by_kind.get("feature", ()),
        *by_kind.get("mastery", ()),
        *by_kind.get("language", ()),
        *by_kind.get("tool", ()),
    ]
    feature_like.sort(key=lambda c: (c.level, c.source, c.title))
    owed, _ = _match_features(
        choices.picks, feature_like,
        resistances=choices.resistances, immunities=choices.immunities,
    )
    out.extend(owed)

    spent_slots = {(pick.class_slug, pick.level) for pick in choices.asis}
    spent_slots |= {(pick.class_slug, pick.level) for pick in choices.feats}
    for choice in by_kind.get("asi", ()):
        if (choice.source.split(":", 1)[1], choice.level) not in spent_slots:
            out.append(Pending(choice, 1))

    if spec.subclass_slug is None:
        for choice in by_kind.get("subclass", ()):
            out.append(Pending(choice, 1))
    if getattr(spec, "background_slug", None) is None:
        for choice in by_kind.get("background", ()):
            out.append(Pending(choice, 1))
    elif choices.background is None:
        for choice in by_kind.get("increase", ()):
            out.append(Pending(choice, 1))
    return tuple(out)


def _describe(pending: Sequence[Pending]) -> str:
    return "; ".join(
        f"{item.choice.kind} x{item.missing} at {item.choice.source} level {item.choice.level}"
        for item in pending
        if item.choice.blocking
    )


def _skill_pools(required: Sequence[Choice]) -> tuple[str, ...]:
    pool: list[str] = []
    for choice in required:
        if choice.kind == "skill":
            pool.extend(choice.pool)
    return tuple(dict.fromkeys(pool))


def validate_choices(spec, *, loader: AssetLoader) -> None:
    """Raise ``ChoiceError`` unless the build spent exactly what its levels allow.

    Overspending is refused as loudly as underspending: more skill picks than the
    levels grant, a skill outside every pool that offered one, a feature pick
    beyond a pool's count, or an Ability Score Improvement token for a slot the
    class does not have.
    """
    choices = parse_selected_choices(spec.selected_choices)
    cls_docs = {}
    for slug in spec.classes:
        doc = loader.get_class(slug)
        if doc is None:
            raise ChoiceError(f"unknown class: {slug!r}")
        cls_docs[slug] = doc
    required = required_choices(
        classes=spec.classes,
        loader=loader,
        species_slug=spec.species_slug,
        subclass_slug=spec.subclass_slug,
        background_slug=getattr(spec, "background_slug", None),
    )
    due_skills = sum(c.count for c in required if c.kind == "skill")
    if len(choices.skills) > due_skills:
        raise ChoiceError(
            f"selected_choices has {len(choices.skills)} skill picks; these levels allow "
            f"{due_skills}"
        )
    offered = _skill_pools(required)
    for skill in choices.skills:
        if skill not in offered:
            raise ChoiceError(
                f"skill pick {skill!r} is not in any skill pool these levels offer "
                f"({len(offered)} options)"
            )
    due_expertise = sum(c.count for c in required if c.kind == "expertise")
    if len(choices.expertise) > due_expertise:
        raise ChoiceError(
            f"selected_choices has {len(choices.expertise)} expertise picks; these levels "
            f"allow {due_expertise}"
        )
    due_slots = {(c.source.split(":", 1)[1], c.level) for c in required if c.kind == "asi"}
    for pick in (*choices.asis, *choices.feats):
        if (pick.class_slug, pick.level) not in due_slots:
            raise ChoiceError(
                "selected_choices uses the Ability Score Improvement at "
                f"{pick.class_slug} level {pick.level}, which this build does not have"
            )
    feature_like = [c for c in required if c.kind in {"feature", "mastery", "language", "tool"}]
    feature_like.sort(key=lambda c: (c.level, c.source, c.title))
    _, extra = _match_features(
        choices.picks, feature_like,
        resistances=choices.resistances, immunities=choices.immunities,
    )
    if extra:
        raise ChoiceError(
            f"selected_choices has {len(extra)} pick(s) beyond the feature choices these "
            f"levels grant: {sorted(extra)}"
        )
    # A subclass is legal only on its own class and only from that class's gate
    # level. derive_sheet refuses both, but this module is the single entry a
    # host is told to call, so it refuses them itself instead of trusting that
    # the caller derived a sheet afterwards.
    if spec.subclass_slug is not None:
        subclass = loader.get_subclass(spec.subclass_slug)
        if subclass is None:
            raise ChoiceError(f"unknown subclass: {spec.subclass_slug!r}")
        owner = str(subclass.class_identifier)
        if owner not in spec.classes:
            raise ChoiceError(
                f"subclass {spec.subclass_slug!r} belongs to {owner}, which this build "
                f"does not have {sorted(spec.classes)}"
            )
        gate = subclass_gate_level(cls_docs[owner])
        owned = int(spec.classes[owner])
        if gate is not None and owned < gate:
            raise ChoiceError(
                f"subclass {spec.subclass_slug!r} opens at {owner} level {gate}; "
                f"the build has {owned}"
            )
    pending = pending_choices(spec, loader=loader)
    blocking = [item for item in pending if item.choice.blocking]
    if blocking:
        raise ChoiceError(
            f"advancement choices still owed: {_describe(blocking)}", pending=blocking
        )


def choice_options(
    spec, *, loader: AssetLoader, include_advisory: bool = False
) -> tuple[dict[str, object], ...]:
    """``required_choices`` as plain rows a character screen can render.

    Each row carries what the decision is (``kind``), who asks for it
    (``source``), the level it opens at, how many picks it wants, the canonical
    options, and how many picks are still owed. ``blocking`` says whether the
    build can be finished without it.
    """
    required = required_choices(
        classes=spec.classes,
        loader=loader,
        species_slug=spec.species_slug,
        subclass_slug=spec.subclass_slug,
        background_slug=getattr(spec, "background_slug", None),
    )
    if not include_advisory:
        # Mastery/language/tool pools are advisory: the sheet stores none of
        # them, so a screen that rendered them would collect picks that change
        # nothing. They stay available to a caller that wants to report them.
        required = tuple(choice for choice in required if choice.blocking)
    owed = {item.choice.key: item.missing for item in pending_choices(spec, loader=loader)}
    return tuple(
        {
            "key": choice.key,
            "kind": choice.kind,
            "source": choice.source,
            "level": choice.level,
            "title": choice.title,
            "hint": choice.hint,
            "mode": choice.mode,
            "count": choice.count,
            "missing": owed.get(choice.key, 0),
            "pool": list(choice.pool),
            "points": choice.points,
            "cap": choice.cap,
            "locked": list(choice.locked),
            "replacement": choice.replacement,
            "blocking": choice.blocking,
        }
        for choice in required
    )


def level_for_experience(experience: int) -> int:
    """The SRD 5.2 character level ``experience`` reaches (1..20)."""
    if type(experience) is not int or experience < 0:
        raise ValueError(f"experience must be a non-negative int, got {experience!r}")
    level = 1
    for index, threshold in enumerate(XP_THRESHOLDS, start=1):
        if experience >= threshold:
            level = index
    return level


def experience_for_level(level: int) -> int:
    """The experience total the SRD 5.2 table requires for ``level``."""
    if type(level) is not int or not 1 <= level <= len(XP_THRESHOLDS):
        raise ValueError(f"level must be 1..{len(XP_THRESHOLDS)}, got {level!r}")
    return XP_THRESHOLDS[level - 1]


def advance(spec, *, class_slug: str, loader: AssetLoader):
    """The same build, one class level higher, every existing pick kept.

    The advanced spec is constructed fresh rather than copied:
    ``CharacterBuildSpec`` reconciles ``classes`` with ``level`` in a
    ``before``-validator that ``model_copy`` would bypass, and a build whose
    level and class levels disagree is not a build. Nothing is invented — the
    caller then supplies the picks ``pending_choices`` reports, and
    ``validate_choices`` must pass again before the level is committed.
    """
    from dnd5e_engine.build_spec import CharacterBuildSpec

    if class_slug not in spec.classes:
        raise ValueError(
            f"{class_slug!r} is not one of the build's classes {sorted(spec.classes)}"
        )
    if spec.level >= 20:
        raise ValueError("a character cannot advance past level 20")
    classes = dict(spec.classes)
    classes[class_slug] = classes[class_slug] + 1
    payload = spec.model_dump(mode="json")
    payload["classes"] = classes
    payload["level"] = sum(classes.values())
    return CharacterBuildSpec(**payload)


def hp_gain(before, after, *, loader: AssetLoader) -> int:
    """The hit-point maximum ``advance`` added, so a host heals by that delta.

    SRD 5.2: gaining a level raises the maximum by the class's Hit Die value
    (fixed mode: half the die plus one) plus the Constitution modifier. Reading
    it from the derived sheet keeps one arithmetic, and it is a delta rather
    than a heal: the host decides whether the hero also recovers hit points.
    """
    from dnd5e_engine.build_spec import derive_sheet

    return derive_sheet(after, loader=loader).hp_max - derive_sheet(before, loader=loader).hp_max


def species_lineages(loader: AssetLoader) -> tuple[dict[str, object], ...]:
    """Species grouped for the two-step pick: base species, then lineage.

    The 2024 SRD folds the old "subrace" concept into leaf documents named
    ``Elf, High`` / ``Gnome, Forest``, so a player who picks "Elf" must still
    pick which Elf. Grouping on the name before the comma recovers that step
    from the corpus instead of a hand-written table: a species with one leaf
    has no lineage step, one with several has a mandatory one.
    """
    groups: dict[str, list[dict[str, str]]] = {}
    for slug in loader.list_slugs("species"):
        species = loader.get_species(slug)
        if species is None:
            continue
        base = str(species.name).split(",", 1)[0].strip()
        groups.setdefault(base, []).append({"slug": slug, "name": str(species.name)})
    return tuple(
        {
            "base": base,
            "name": base,
            "variants": sorted(variants, key=lambda row: row["name"]),
            "lineage_required": len(variants) > 1,
        }
        for base, variants in sorted(groups.items())
    )


def subclass_options(
    class_slug: str, level: int, *, loader: AssetLoader
) -> tuple[dict[str, object], ...]:
    """The subclasses this class may legally take at ``level``.

    SRD 5.2 opens a subclass at a class-defined level (3 for every 2024 class,
    which is why this asks the class instead of assuming it). Below the gate the
    list is empty — not filtered prose, an empty pool — so a screen cannot offer
    a subclass the build cannot take.
    """
    cls = loader.get_class(class_slug)
    if cls is None:
        raise ValueError(f"unknown class: {class_slug!r}")
    gate = subclass_gate_level(cls)
    if gate is None or level < gate:
        return ()
    out = []
    for slug in loader.list_slugs("subclasses"):
        subclass = loader.get_subclass(slug)
        if subclass is not None and str(subclass.class_identifier) == str(cls.identifier):
            out.append({"slug": slug, "name": str(subclass.name), "min_level": gate})
    return tuple(sorted(out, key=lambda row: str(row["name"])))


def subclass_gate(class_slug: str, *, loader: AssetLoader) -> int | None:
    """The class level its subclass opens at, or ``None`` when it never does."""
    cls = loader.get_class(class_slug)
    if cls is None:
        raise ValueError(f"unknown class: {class_slug!r}")
    return subclass_gate_level(cls)

__all__ = [
    "ADVISORY_KINDS",
    "BLOCKING_KINDS",
    "Choice",
    "ChoiceError",
    "Pending",
    "XP_THRESHOLDS",
    "advance",
    "choice_options",
    "experience_for_level",
    "hp_gain",
    "level_for_experience",
    "pending_choices",
    "required_choices",
    "species_lineages",
    "subclass_gate",
    "subclass_options",
    "validate_choices",
]
