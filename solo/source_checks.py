"""Checks a place carries from its material, with their provenance.

A check printed by the book must survive ``import_package`` and must say where
its DC came from. ``WorldLocation.checks`` is additive and strict, so anything
this module stores has to be declared there — a bare key in the location dict
is silently dropped by ``place.model_dump()`` (``solo/world.py:282``) and the
player never learns that the check existed.

Two shapes live here:

* ``SourceCheck`` — the storage shape kept in ``state['locations'][i]['checks']``
  (``Step``-compatible fields plus provenance, because ``Step`` is ``Strict``);
* helpers translating it to what the Nat20 definition wants
  (``PrototypeCheck`` + ``CampaignSource``) and to a display label.

Why ``ability`` is stored as the long name
------------------------------------------
``command_engine.py:183`` splats a stored check into
``Step(kind='check', **value)`` and ``Step.ability`` is
``Literal['strength', ...]``. Storing the host's short code (``'wis'``, the
form ``SceneCheck.ability`` uses) validated in this module and then raised a
hard pydantic error on the legacy path — a check the book printed would kill
the turn it was meant to open. Long names validate on both paths;
``CheckSpec`` normalizes either form for the Nat20 path.

Why a source skill check may also name its ability
--------------------------------------------------
The book writes "Внимательность (Сл 14)" — a skill *and* the ability it rides
on. Requiring exactly one of the two rejected that. Storage keeps both; the
Nat20 registry entry drops the ability for skill checks because
``PrototypeCheck.target`` demands exactly one.
"""
from typing import Literal
import re
from pydantic import Field, model_validator
from .contracts import Strict
from .preparation_contracts import SourceQuote

Ability = Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']
CheckAnchor = Literal['location', 'npc', 'object', 'route']
ORIGINS = ('source', 'master')

# Player-facing names. The engine's labels module has no ability/skill table,
# so the display names live here rather than behind an import that does not
# resolve (`dnd5e_engine.labels.SKILL_LABELS` does not exist).
SKILL_LABELS = {
    'acrobatics': 'Акробатика', 'animal_handling': 'Обращение с животными',
    'arcana': 'Магия', 'athletics': 'Атлетика', 'deception': 'Обман',
    'history': 'История', 'insight': 'Проницательность', 'intimidation': 'Запугивание',
    'investigation': 'Расследование', 'medicine': 'Медицина', 'nature': 'Природа',
    'perception': 'Внимательность', 'performance': 'Выступление',
    'persuasion': 'Убеждение', 'religion': 'Религия', 'sleight_of_hand': 'Ловкость рук',
    'stealth': 'Скрытность', 'survival': 'Выживание',
}
ABILITY_LABELS = {
    'strength': 'Сила', 'dexterity': 'Ловкость', 'constitution': 'Телосложение',
    'intelligence': 'Интеллект', 'wisdom': 'Мудрость', 'charisma': 'Харизма',
}
_SHORT = {'str': 'strength', 'dex': 'dexterity', 'con': 'constitution',
          'int': 'intelligence', 'wis': 'wisdom', 'cha': 'charisma'}
# The engine's own spelling of an ability: SceneCheck.ability is a Literal of
# three-letter codes, so a long name from storage has to come back to it.
_SHORT_NAMES = {name: code for code, name in _SHORT.items()}


def full_ability(value):
    """'wis' or 'wisdom' -> 'wisdom'; anything else is an error, not a guess."""
    if not value:
        return None
    key = value.lower()
    full = _SHORT.get(key, key)
    if full not in ABILITY_LABELS:
        raise ValueError(f'Неизвестная характеристика: {value!r}')
    return full


class CheckAnchorRef(Strict):
    kind: CheckAnchor
    id: str = Field(min_length=1, max_length=120)


class SourceCheck(Strict):
    """One check of one place, with the origin of its difficulty."""

    # Declared as `str`, not `Ability`: pydantic rejects a Literal before the
    # validator runs, so the short code the hosts use ('dex') would fail
    # even though `full_ability` knows it. Normalization happens in
    # `provenance`; an unknown value still raises there.
    ability: str | None = None
    skill: str = Field(default='', max_length=40)
    dc: int = Field(ge=1, le=40)
    advantage: bool = False
    disadvantage: bool = False
    # 'source' = the book printed this DC; 'master' = a human set it. There is
    # no third value: an engine default is never stored here.
    origin: Literal['source', 'master']
    source: SourceQuote | None = None
    trigger: str = Field(default='', max_length=240)
    anchor: CheckAnchorRef | None = None

    @model_validator(mode='after')
    def provenance(self):
        if not self.skill and not self.ability:
            raise ValueError('Проверка требует навык или характеристику')
        # A book DC without the page and the quote is not provenance: it is a
        # number pretending to be one.
        if self.origin == 'source' and self.source is None:
            raise ValueError("origin='source' требует source (файл, страница, цитата)")
        if self.origin == 'master' and self.source is not None:
            raise ValueError("origin='master' не несёт книжную ссылку")
        # Normalize once, here: every reader (legacy Step, Nat20 CheckSpec)
        # then sees the same string.
        if self.ability:
            object.__setattr__(self, 'ability', full_ability(self.ability))
        return self


def locate_checks(state, name):
    """Return ``(place, checks)`` for the named place, or ``(None, {})``.

    Reads through the declared field when present and falls back to the raw
    dict, so a save written before the field existed keeps working.
    """
    for place in state.get('locations') or []:
        if place.get('name') == name:
            return place, dict(place.get('checks') or {})
    return None, {}


def label(check, book=''):
    """Player-facing label: the DC and its origin are always both visible."""
    value = check if isinstance(check, SourceCheck) else SourceCheck.model_validate(check)
    if value.skill:
        name = SKILL_LABELS.get(value.skill, value.skill)
    else:
        name = ABILITY_LABELS.get(value.ability, value.ability)
    if value.origin == 'source' and value.source is not None:
        where = f'{book}, стр. {value.source.page}' if book else f'стр. {value.source.page}'
        return f'{name} (Сл {value.dc}, книга: {where})'
    return f'{name} (Сл {value.dc}, мастер)'


def campaign_source(check):
    """Convert provenance to the ``CampaignSource`` the Nat20 registry expects."""
    from .nat20_campaign import CampaignSource
    value = check if isinstance(check, SourceCheck) else SourceCheck.model_validate(check)
    if value.origin == 'source' and value.source is not None:
        return CampaignSource(kind='authored_scenario', id='source:' + value.source.document[:12],
                              version='page-' + str(value.source.page), note=value.source.quote[:800])
    return CampaignSource(kind='authored_scenario', id='master', version='1',
                          note='Сл задана Мастером, не книгой' if not value.trigger else value.trigger[:800])


def to_prototype_check(check_id, check):
    """``SourceCheck`` → the trusted-scene registry entry (``kind``/``skill``/``ability``).

    A skill check refuses an explicit ``ability`` (``PrototypeCheck.target``
    requires exactly one of the two), so the stored ability is dropped there —
    the engine derives it from ``SKILL_ABILITIES`` anyway.

    An ability check travels back as the engine's own three-letter code: storage
    keeps the long name (``Step.ability`` demands it), while ``SceneCheck.ability``
    is ``Literal['str', ...]`` and would reject the long form the moment the entry
    reached the Nat20 registry.
    """
    value = check if isinstance(check, SourceCheck) else SourceCheck.model_validate(check)
    # The id says which kind of d20 test it is; a printed saving throw has to
    # reach the engine as a save (its own proficiency and bonus bucket), not as
    # a bare ability check that would roll a different number.
    flavour = str(check_id).split(':')[2] if str(check_id).count(':') >= 2 else ''
    kind = 'saving_throw' if flavour == 'saving_throw' else ('skill' if value.skill else 'ability')
    return dict(kind=kind, skill=value.skill or None,
                ability=None if value.skill else _SHORT_NAMES.get(value.ability or '', value.ability),
                dc=value.dc,
                advantage=value.advantage, disadvantage=value.disadvantage,
                source=campaign_source(value).model_dump(mode='json'))


def current_checks(state, name=None):
    """The checks the hero's own scene carries, keyed by their registry id."""
    return locate_checks(state, name or state.get('location') or '')[1]


def registry(state, name=None):
    """The current scene's checks in the shape the Nat20 trusted registry takes.

    This is the bridge the chain was missing: the book prints a DC, the place
    stores it, and the engine may only roll against a check id it was told about.
    Without these entries ``available_checks`` listed the host's default skills
    (with no DC) and the engine refused the book's own key as unregistered.
    """
    return {check_id: to_prototype_check(check_id, check)
            for check_id, check in current_checks(state, name).items()}


_CHECK_PHRASE = re.compile(r'^\s*(?:провер\w*|брос\w*|кида\w*)\b')


def match(state, text, name=None):
    """«Проверить: Скрытность» → the id of the check the current scene printed.

    The player names a check the way the book does (its skill or ability), the
    engine knows it by an opaque id. A phrase that fits two checks of the same
    skill at different DCs resolves to nothing: the table is asked instead of a
    die being rolled against a guessed difficulty.
    """
    words = _flat(text)
    if not words or not _CHECK_PHRASE.match(words):
        return None
    known = current_checks(state, name)
    # The full id, typed or copied from a label, names exactly one check.
    typed = {token.casefold() for token in str(text or '').split()}
    exact = [check_id for check_id in known if check_id.casefold() in typed]
    if len(exact) == 1:
        return exact[0]
    found = []
    for check_id, raw in known.items():
        value = raw if isinstance(raw, SourceCheck) else SourceCheck.model_validate(raw)
        # Only the thing the check actually tests names it: a skill check is not
        # its ability, so «проверяю Ловкость» must not roll Скрытность.
        names = {_flat(check_id), _flat(value.skill), _flat(SKILL_LABELS.get(value.skill, ''))}
        if not value.skill:
            names |= {_flat(value.ability), _flat(ABILITY_LABELS.get(value.ability, ''))}
        if any(name and (name == words or re.search(r'(?:^|\s)' + re.escape(name) + r'(?:\s|$)', words))
               for name in names):
            found.append(check_id)
    return found[0] if len(found) == 1 else None


def _flat(text):
    """Casefolded words only: a label and a sentence compare by their words."""
    return ' '.join(re.sub(r'[^\w\s]+', ' ', str(text or '').casefold()).split())


def step_fields(check):
    """The ``step_fields`` a legacy ``Step(kind='check', **value)`` accepts.

    Provenance stays out: ``Step`` is strict and would reject it.
    """
    value = check if isinstance(check, SourceCheck) else SourceCheck.model_validate(check)
    return dict(ability=value.ability, dc=value.dc, skill=value.skill,
                advantage=value.advantage, disadvantage=value.disadvantage,
                proficient=False)
