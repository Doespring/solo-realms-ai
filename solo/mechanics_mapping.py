"""Map source mechanical elements onto canonical Nat20 entities and operations.

The book states its rules in the edition it was written for. The player runs the
edition the table agreed on. Those are two facts, not one, so this module never
rewrites one into the other silently:

* a source phrase is matched to a canonical slug through an explicit table of
  reviewed aliases, never through fuzzy name similarity alone;
* a match is only used when the canonical entity really resolves in the pinned
  corpus, so the identifier is checked and not merely looked similar;
* the original wording, the resolved target and the reason are kept together, so
  a reader can always see what was replaced and why;
* anything without a defensible counterpart keeps its source wording and is
  reported as requiring adaptation instead of being executed as if it existed.

An unsupported element never blocks the adventure: it is recorded, the rest of the
scene stays playable, and the loss is stated in the launch limitations.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Strict

#: Kinds of mechanical element the mapping layer can speak about.
ELEMENT_KINDS = ('monster', 'check', 'item', 'spell', 'condition', 'action')

#: Reviewed aliases: source wording (casefolded) -> canonical Nat20 slug.
#: A slug here is a claim to be verified against the pinned corpus, never a
#: guess from spelling. Transliteration differences are listed deliberately.
MONSTER_ALIASES = {
    'кобольд': 'kobold-warrior',
    'кобольд-воин': 'kobold-warrior',
    'kobold': 'kobold-warrior',
    'kobold warrior': 'kobold-warrior',
    'культист': 'cultist',
    'cultist': 'cultist',
    'фанатик культа': 'cultist-fanatic',
    'cultist fanatic': 'cultist-fanatic',
    'стражник': 'guard',
    'охранник': 'guard',
    'guard': 'guard',
    'бандит': 'bandit',
    'bandit': 'bandit',
    'разбойник': 'bandit',
    'гоблин-воин': 'goblin-warrior',
    'goblin warrior': 'goblin-warrior',
    'послушник': 'priest-acolyte',
    'аколит': 'priest-acolyte',
    'acolyte': 'priest-acolyte',
    'жрец': 'priest',
    'priest': 'priest',
    'маг': 'mage',
    'mage': 'mage',
    'ветеран': 'warrior-veteran',
    'veteran': 'warrior-veteran',
    'разведчик': 'scout',
    'scout': 'scout',
    'дворянин': 'noble',
    'noble': 'noble',
    'простолюдин': 'commoner',
    'commoner': 'commoner',
    'шпион': 'spy',
    'spy': 'spy',
    'убийца': 'assassin',
    'assassin': 'assassin',
}

#: Source wording -> Nat20 skill/ability check id. The check id must resolve in
#: the runtime's trusted check registry, so this table cannot invent one.
CHECK_ALIASES = {
    'сила': 'str',
    'ловкость': 'dex',
    'телосложение': 'con',
    'интеллект': 'int',
    'мудрость': 'wis',
    'харизма': 'cha',
    'атлетика': 'athletics',
    'акробатика': 'acrobatics',
    'скрытность': 'stealth',
    'скрытности': 'stealth',
    'восприятие': 'perception',
    'восприятия': 'perception',
    'восприятию': 'perception',
    'проницательность': 'insight',
    'запугивание': 'intimidation',
    'запугивания': 'intimidation',
    'обман': 'deception',
    'обмана': 'deception',
    'убеждение': 'persuasion',
    'убеждения': 'persuasion',
    'выживание': 'survival',
    'медицина': 'medicine',
    'природа': 'nature',
    'история': 'history',
    'магия': 'arcana',
    'религия': 'religion',
    'расследование': 'investigation',
    'обращение с животными': 'animal_handling',
    'выступление': 'performance',
    'ловкость рук': 'sleight_of_hand',
}


class Substitution(Strict):
    """One source element and what it was resolved to, with the reason why."""

    kind: Literal['monster', 'check', 'item', 'spell', 'condition', 'action']
    #: Wording exactly as the book states it.
    source: str = Field(min_length=1, max_length=200)
    #: Page the source wording was verified on, when known.
    page: int | None = Field(default=None, ge=1, le=2000)
    #: Stable canonical identifier, or empty when nothing matched.
    target: str = Field(default='', max_length=120)
    #: How the target was established, so a reader can audit the claim.
    basis: Literal['alias', 'exact', 'adapter', 'none'] = 'none'
    #: Why this target, or why there is none.
    reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode='after')
    def consistent(self):
        if self.basis == 'none' and self.target:
            raise ValueError('Без основания не может быть цели замены')
        if self.basis != 'none' and not self.target:
            raise ValueError('У замены должно быть устойчивое имя цели')
        return self


class Adaptation(Strict):
    """A source element that has no defensible canonical counterpart."""

    kind: Literal['monster', 'check', 'item', 'spell', 'condition', 'action', 'rule']
    source: str = Field(min_length=1, max_length=200)
    page: int | None = Field(default=None, ge=1, le=2000)
    #: What the table must decide, in plain words.
    needs: str = Field(min_length=1, max_length=400)


class MechanicsMapping(Strict):
    """The whole mapping of one source module onto the active mechanics."""

    #: Edition and version the source text was written for.
    source_ruleset: str = Field(max_length=80)
    source_version: int = Field(ge=1)
    #: Edition and version actually used to resolve characters, checks, combat.
    mechanics_ruleset: str = Field(max_length=80)
    mechanics_version: int = Field(ge=1)
    substitutions: list[Substitution] = Field(default_factory=list, max_length=500)
    adaptations: list[Adaptation] = Field(default_factory=list, max_length=500)
    #: Difficulty was not preserved exactly; stated once, never promised.
    balance_note: str = Field(
        default='Точное сохранение баланса не обещается: замена противника меняет сложность встречи.',
        max_length=400,
    )

    @model_validator(mode='after')
    def unique_source(self):
        seen = set()
        for row in self.substitutions:
            key = (row.kind, row.source.casefold())
            if key in seen:
                raise ValueError('Повтор исходного элемента сопоставления')
            seen.add(key)
        return self

    @property
    def resolved(self):
        return [row for row in self.substitutions if row.basis != 'none']

    @property
    def requires_adaptation(self):
        return list(self.adaptations)

    def public(self):
        """A player-safe projection: no hidden text, only what was replaced."""
        return dict(
            source_ruleset=self.source_ruleset,
            mechanics_ruleset=self.mechanics_ruleset,
            replaced=[dict(kind=r.kind, source=r.source, target=r.target, reason=r.reason)
                      for r in self.resolved],
            needs_adaptation=[dict(kind=a.kind, source=a.source, needs=a.needs)
                              for a in self.adaptations],
            balance_note=self.balance_note,
        )

#: Directories of the pinned data set, one per entity kind. Nothing else is
#: consulted: a slug counts only when the shipped corpus really contains it.
_CORPUS_DIRS = {
    'monsters': 'monsters',
    'items': 'items',
    'spells': 'spells',
    'conditions': 'conditions',
    'classes': 'classes',
    'species': 'species',
    'feats': 'feats',
}


def _corpus_has(kind, slug):
    """True only when the pinned corpus really resolves this identifier."""
    from .nat20_prototype import DATA
    if kind == 'checks':
        # Checks are resolved from the runtime's own trusted skill registry, so a
        # plausible-sounding id cannot be invented here.
        from dnd5e_engine.rules.skills import SKILL_ABILITIES
        return slug in set(SKILL_ABILITIES) | {'str', 'dex', 'con', 'int', 'wis', 'cha'}
    folder = _CORPUS_DIRS.get(kind)
    if not folder:
        return False
    return (DATA / folder / (slug + '.json')).is_file()


def resolve_monster(wording, page=None, *, words=()):
    """Map a source monster phrase, or record that it needs adaptation."""
    source = ' '.join(str(wording).split())
    if not source:
        raise ValueError('Пустое название противника')
    slug = MONSTER_ALIASES.get(source.casefold())
    if slug is None:
        # A page counts its creatures: 'three kobolds guard the far arch'. The
        # plural is the same monster, so the singular is looked up before the
        # wording is declared unknown. Only a plain plural ending is folded, and
        # the folded form must be a registered alias, so no lookalike is invented.
        folded=source.casefold()
        for suffix,trim in (('es',2),('s',1)):
            if folded.endswith(suffix) and len(folded)-trim>=4:
                candidate=MONSTER_ALIASES.get(folded[:-trim])
                if candidate:
                    slug=candidate
                    if _corpus_has('monsters', slug):
                        return Substitution(kind='monster', source=source, page=page, target=slug, basis='alias',
                            reason='Проверенное имя противника сведено к канонической записи Nat20.')
                    break
    if slug and _corpus_has('monsters', slug):
        return Substitution(kind='monster', source=source, page=page, target=slug, basis='alias',
                            reason='Проверенное имя противника сведено к канонической записи Nat20.')
    if slug:
        return Substitution(kind='monster', source=source, page=page,
                            reason='Подтверждённый синоним указывает на '+slug+', но записи в закреплённом наборе нет.')
    # Spelling similarity is not evidence: a lookalike never becomes a slug.
    return Substitution(kind='monster', source=source, page=page,
                        reason='Точного соответствия в закреплённом наборе Nat20 нет; противник требует адаптации.')


def resolve_check(wording, page=None):
    """Map a source check phrase onto a trusted check identifier."""
    source = ' '.join(str(wording).split())
    if not source:
        raise ValueError('Пустое название проверки')
    key = source.casefold()
    target = CHECK_ALIASES.get(key)
    if target is None:
        # The book writes checks as "Ability (Skill)", e.g. "Ловкость (Ловкость Рук)".
        # The skill inside the brackets is what the table actually rolls, so it wins
        # over the bare ability.
        import re
        inside = re.findall(r'\(([^)]*)\)', key)
        for group in inside:
            phrase = ' '.join(group.split())
            if phrase in CHECK_ALIASES:
                target = CHECK_ALIASES[phrase]
                break
        if target is None:
            for phrase in inside:
                for token in phrase.split():
                    if token in CHECK_ALIASES and CHECK_ALIASES[token] not in ('str', 'dex', 'con', 'int', 'wis', 'cha'):
                        target = CHECK_ALIASES[token]
                        break
                if target:
                    break
    if target and _corpus_has('checks', target):
        return Substitution(kind='check', source=source, page=page, target=target, basis='alias',
                            reason='Проверка источника вынесена на зарегистрированную проверку Nat20.')
    return Substitution(kind='check', source=source, page=page,
                        reason='Такой проверки нет в доверенном списке Nat20; требуется решение стола.')


def adapt(kind, source, needs, page=None):
    """Record an element the table must decide, never executed as if present."""
    return Adaptation(kind=kind, source=' '.join(str(source).split()), page=page,
                      needs=' '.join(str(needs).split()))


def build(*, source_ruleset, source_version, mechanics_ruleset, mechanics_version,
          substitutions=(), adaptations=()):
    """Assemble one reviewed mapping. Editions stay two separate facts."""
    subs = [s for s in substitutions if s is not None]
    adds = [a for a in adaptations if a is not None]
    mapping = MechanicsMapping(
        source_ruleset=source_ruleset,
        source_version=int(source_version),
        mechanics_ruleset=mechanics_ruleset,
        mechanics_version=int(mechanics_version),
        substitutions=subs,
        adaptations=adds,
    )
    # A source element with no target belongs to the adaptation list, so a reader
    # never has to cross-check two places to learn what is missing.
    for row in list(mapping.substitutions):
        if row.basis == 'none':
            mapping.substitutions.remove(row)
            if not any(a.kind == row.kind and a.source == row.source for a in mapping.adaptations):
                mapping.adaptations.append(
                    Adaptation(kind=row.kind, source=row.source, page=row.page,
                               needs=row.reason))
    return mapping
