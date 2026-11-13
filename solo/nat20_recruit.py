"""An NPC the party met becomes a party member, and can leave again (r218).

The owner's decision (SPEC-COMPANIONS.md, item 6, 03.11.2026): during play some
NPCs may become companions. The campaign already knows NPCs as *people of a
place* — a name, the printed sentence about them, what they know and who may
know it. What they never had was a sheet.

Two policies live here, and only policies:

1. **What the material says, the material decides.** If the printed sentence
   names a class next to the person's own name («голиаф-воин по имени Капанук»,
   «Нетерильский маг по имени Карсус»), the class and the species are read from
   it and the exact sentence travels with the record as evidence. A word that is
   capitalised, or stands far from the name, or names a second person in the same
   sentence is not evidence: the corpus prints «Красный Волшебник» as a title,
   and carrying that onto an unrelated NPC would invent a class he does not have.
2. **What the material does not say is not invented.** With no class in the
   material the NPC still joins — the player asked for it — but on a minimal
   legal sheet, and the record says so (`from_book: False`) with the sentence the
   screen shows. His level is the hero's level, the same rule as every companion
   (r214).

Nothing here writes memory or ``known_by``: the transformation gives the NPC no
knowledge he did not have, and no event is written about anyone else.
"""
from __future__ import annotations

import re
import unicodedata

#: Why an NPC joined. The reason is data - the journal prints it, the screen
#: shows it, and a caller cannot invent a fourth code by passing free text.
JOIN_REASONS = {'rescued': 'спасён', 'hired': 'нанят', 'asked': 'сам попросился'}

#: Why he left. ``died`` and ``betrayed`` are the two ways an NPC stops being a
#: companion without merely walking away.
LEAVE_REASONS = {'left': 'ушёл', 'died': 'погиб', 'betrayed': 'предал'}

#: The twelve classes of D&D 2024. A closed list on purpose: the host must not
#: grow a class the engine cannot price, and a test pins this tuple to the
#: engine's own catalogue.
CLASS_SLUGS = ('barbarian', 'bard', 'cleric', 'druid', 'fighter', 'monk',
               'paladin', 'ranger', 'rogue', 'sorcerer', 'warlock', 'wizard')

#: Species the engine knows by slug. A word outside this list reads as no hint.
SPECIES_SLUGS = ('dragonborn', 'dwarf', 'elf-drow', 'elf-high', 'elf-wood',
                 'gnome-forest', 'gnome-rock', 'goliath', 'halfling', 'human',
                 'orc', 'tiefling-abyssal', 'tiefling-chthonic', 'tiefling-infernal')

DEFAULT_CLASS = 'fighter'
DEFAULT_SPECIES = 'human'
DEFAULT_BACKGROUND = 'soldier'

#: The sentence the screen shows for a sheet the material never described.
NOT_FROM_BOOK = 'Параметры не из книги: в материале его класс не назван, лист собран по умолчанию.'

_SENTENCE = re.compile(r'[^.!?;\n]+[.!?;]?')

#: One suggested build per (class, species, level) in this process. The catalogue
#: walk behind it costs seconds and never changes for the same three answers, and
#: an NPC joins at most once, so paying it twice is waste, not caution.
_BUILDS: dict[tuple, dict] = {}


def _fold(value):
    """Lowercase, accent-free text with one space per gap."""
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', text).casefold()


def _squash(value):
    """The original text with collapsed spaces, so offsets match ``_fold``."""
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _vocabulary():
    """Class and species words, taken from the one table the importer owns.

    ``solo/nat20_import.py`` already maps Russian sheet words to catalogue slugs
    (it reads a pasted hero sheet). Reusing it keeps one vocabulary instead of a
    second list that drifts. Background words are excluded: "солдат" is also
    "воин" there, and a background is not a class.
    """
    from .nat20_import import RU_NAMES

    classes = {word: slug for slug, words in RU_NAMES.items()
               if slug in CLASS_SLUGS for word in words}
    species = {word: slug for slug, words in RU_NAMES.items()
               if slug in SPECIES_SLUGS for word in words}
    return classes, species


def _mentions(sentence, folded, words):
    """``(start, end, slug, word, printed_level)`` for every lowercase class word.

    ``sentence`` is the original text and ``folded`` its case-folded twin with
    identical offsets, so the case of the word survives the search. A capitalised
    occurrence is a title, not a description: «Красный Волшебник» names a person by
    an epithet, and the corpus also prints that phrase in a sentence about someone
    else.
    """
    found = []
    for word, slug in sorted(words.items(), key=lambda row: -len(row[0])):
        for match in re.finditer(r'(?<![а-яёa-z])' + re.escape(word) + r'(?![а-яёa-z])', folded):
            if match.start() < len(sentence) and sentence[match.start()].isupper():
                continue
            tail = folded[match.end():match.end() + 24]
            level = re.match(r'\s+(\d{1,2})(?!\d)', tail)
            found.append((match.start(), match.end(), slug, word,
                          int(level.group(1)) if level else None))
    return found


#: The words that may stand between a person's name and the class describing him:
#: «голиаф-воин по имени Капанук», «Карсус, маг 3». Anything else — another name,
#: a verb — means the class belongs to somebody or something else in the sentence.
_CONNECTORS = frozenset({'по', 'имени', 'прозвищу', 'кличке', 'прозванию', 'иначе',
                         'зовут', 'называемый', 'по-прозвищу'})


def _gap_is_connector(gap, species_words):
    """True when only punctuation, connectors and species words stand in ``gap``."""
    for token in re.findall(r'[а-яёa-z-]+', _fold(gap)):
        if token in _CONNECTORS or token in species_words:
            continue
        return False
    return True


def read_hint(name, *texts):
    """What the printed mentions of this person say about his class and species.

    ``texts`` are the NPC's fields in order of trust: ``character``, ``biography``,
    ``known``, then each ``private_knowledge`` quote. A sentence answers only when
    the class word stands *glued* to the name — before it through «по имени» /
    punctuation, or after it through punctuation — and is the only class word
    there. «…по имени Квентин Скотт и маг Вальтер» describes two people, so it
    answers nothing: guessing which class is his would invent a sheet.

    Returns ``{from_book, class_slug, species_slug, printed_level, evidence, note}``.
    ``from_book`` is False when no class was read, and the note then says the
    sheet is the host's default instead of the material's.
    """
    classes, species = _vocabulary()
    wanted = _fold(name)
    empty = dict(from_book=False, class_slug='', species_slug='', printed_level=None,
                 evidence='', note=NOT_FROM_BOOK)
    if not wanted:
        return empty
    for raw in texts:
        for sentence in _SENTENCE.findall(_squash(raw)):
            folded = _fold(sentence)
            at = folded.find(wanted)
            if at < 0:
                continue
            end = at + len(wanted)
            glued = []
            for start, stop, slug, word, level in _mentions(sentence, folded, classes):
                before = folded[stop:at] if stop <= at else ''
                after = folded[end:start] if start >= end else ''
                if stop <= at and _gap_is_connector(before, species):
                    glued.append((start, stop, slug, level))
                elif start >= end and _gap_is_connector(after, species):
                    glued.append((start, stop, slug, level))
            if not glued:
                continue
            if len({row[2] for row in glued}) > 1:
                # Two classes glued to one name: the sentence describes a group or
                # two people, and choosing one would be a guess.
                return dict(empty, evidence=sentence, note=NOT_FROM_BOOK)
            start, stop, slug, level = glued[0]
            named = [row for row in _mentions(sentence, folded, species)
                     if min(abs(row[0] - at), abs(row[0] - stop), abs(row[1] - at)) <= 20]
            return dict(from_book=True, class_slug=slug,
                        species_slug=named[0][2] if named else DEFAULT_SPECIES,
                        printed_level=level, evidence=sentence, note='')
    return empty


def _russian(slug):
    """The Russian word the material uses for a class or species slug."""
    classes, species = _vocabulary()
    for word, value in (*species.items(), *classes.items()):
        if value == str(slug):
            return word
    return str(slug)


def describe(hint, level):
    """One line about the sheet that was built, for the record and the feed."""
    if not hint.get('from_book'):
        return NOT_FROM_BOOK
    line = 'Лист по материалу: класс ' + _russian(hint.get('class_slug')) \
        + ', вид ' + _russian(hint.get('species_slug') or DEFAULT_SPECIES)
    printed = hint.get('printed_level')
    if printed and int(printed) != int(level):
        line += '. В материале назван ' + str(printed) + ' уровень, в партии он идёт на уровне героя (' \
            + str(level) + ')'
    return line + '.'


def build_for(hint, level, *, class_slug='', species_slug=''):
    """A legal build at ``level``, from the material's class when it named one.

    ``suggested_build`` answers every blocking step from the options the editor
    publishes itself, so the sheet is legal without a second rules table here. An
    explicit ``class_slug``/``species_slug`` from the player wins over the reading.
    """
    from .nat20_wizard import suggested_build

    klass = str(class_slug or hint.get('class_slug') or DEFAULT_CLASS)
    kind = str(species_slug or hint.get('species_slug') or DEFAULT_SPECIES)
    if klass not in CLASS_SLUGS:
        raise ValueError('Неизвестный класс для спутника: ' + klass)
    if kind not in SPECIES_SLUGS:
        raise ValueError('Неизвестный вид для спутника: ' + kind)
    key = (klass, kind, int(level))
    if key not in _BUILDS:
        _BUILDS[key] = suggested_build(class_slug=klass, species_slug=kind,
                                       background_slug=DEFAULT_BACKGROUND, level=int(level))
    return dict(_BUILDS[key])


def join(state, spec, loader, fingerprint):
    """Turn the NPC behind ``spec`` into a party member. Worker-side.

    The party record is ``nat20_party.record``: an engine sheet and an engine
    combat projection, exactly like a companion created in the editor. What this
    adds is the provenance of the meeting - which NPC of which place, why he
    joined, and what the material said about his class - so the screen can show
    where he came from and the journal can say why.
    """
    from .nat20_party import add_companion
    from .nat20_contracts import DurableState

    origin = dict(spec.get('origin') or {})
    reason = str(spec.get('reason') or '')
    if reason not in JOIN_REASONS:
        raise ValueError('Неизвестная причина вступления: ' + (reason or 'пусто')
                         + '. Известные: ' + ', '.join(sorted(JOIN_REASONS)))
    if not str(origin.get('npc_id') or '').strip():
        raise ValueError('Спутник-НСП должен ссылаться на NPC материала.')
    result = add_companion(state, dict(id=spec.get('id'), name=spec['name'],
                                       build=spec['build'], choices=spec.get('choices') or {},
                                       traits=spec.get('traits') or (), relation=spec.get('relation') or 0,
                                       zone_id=spec.get('zone_id') or '1,1'), loader, fingerprint)
    party = [dict(item) for item in result['state']['party']]
    member = next(item for item in party if item.get('id') == result['member']['id'])
    member['origin'] = dict(origin, reason=reason, reason_text=JOIN_REASONS[reason])
    data = result['state']
    data['party'] = party
    validated = DurableState.model_validate(data).model_dump(mode='json')
    events = [dict(type='npc_joined_party', target_id=member['id'], name=member['name'],
                   reason=reason, reason_text=JOIN_REASONS[reason],
                   npc_id=origin.get('npc_id'), place=origin.get('place') or '',
                   from_book=bool(origin.get('from_book')))]
    return {'state': validated, 'member': member, 'events': events}


def leave(state, spec):
    """Take one member out of the party and say why. Worker-side.

    The hero never leaves his own party this way. A fight is not interrupted by a
    departure: turn order does not shrink mid-encounter, the same rule that stops
    a companion joining during one. Leaving is not dying - the host decides what
    happens to the person in his place afterwards, and this module keeps only the
    party.
    """
    from .nat20_contracts import DurableState

    member_id = str(spec.get('member') or '')
    reason = str(spec.get('reason') or 'left')
    if reason not in LEAVE_REASONS:
        raise ValueError('Неизвестная причина ухода: ' + reason
                         + '. Известные: ' + ', '.join(sorted(LEAVE_REASONS)))
    if member_id == 'hero':
        raise ValueError('Главный герой не выбывает из своей партии.')
    if state.get('combat'):
        raise ValueError('Спутник не выбывает посреди боя: закончите бой.')
    party = [dict(item) for item in state.get('party') or []]
    member = next((item for item in party if item.get('id') == member_id), None)
    if member is None:
        raise ValueError('Такого члена партии нет: ' + (member_id or 'пусто'))
    data = dict(state)
    data['party'] = [item for item in party if item is not member]
    validated = DurableState.model_validate(data).model_dump(mode='json')
    origin = dict(member.get('origin') or {})
    events = [dict(type='npc_left_party', target_id=member_id, name=member.get('name') or member_id,
                   reason=reason, reason_text=LEAVE_REASONS[reason],
                   npc_id=origin.get('npc_id'))]
    return {'state': validated, 'member': member, 'events': events}


def join_sentence(member, reason=None):
    """The line the player reads when the NPC joins, with the reason named.

    The sheet the host chose travels with the sentence: a class the material
    named is stated, and a default sheet says so instead of passing for the
    book's own numbers.
    """
    origin = member.get('origin') or {}
    code = str(reason or origin.get('reason') or '')
    why = JOIN_REASONS.get(code, code)
    line = str(member.get('name') or 'НСП') + ' вступает в партию'
    line += (': ' + why if why else '') + '.'
    sheet = str(origin.get('sheet') or origin.get('note') or '')
    if sheet and sheet not in line:
        line += ' ' + sheet
    return line


def leave_sentence(member, reason=None):
    """The line the player reads when he leaves, with the reason named."""
    why = LEAVE_REASONS.get(str(reason or 'left'), str(reason or 'left'))
    return str(member.get('name') or 'НСП') + ' покидает партию: ' + why + '.'


def candidates(state):
    """NPCs standing where the hero stands, as the screen lists them.

    Read-only: who is in the place, what the material says about his class, and
    whether he is already travelling with the party. Nothing is written here, so
    asking what can be done never changes the table.
    """
    party = (state.get('nat20') or {}).get('party') or []
    taken = {str((item.get('origin') or {}).get('npc_id'))
             for item in party
             if (item.get('origin') or {}).get('npc_id')}
    place_name = str(state.get('location') or '')
    rows = []
    for place in state.get('locations') or []:
        if place.get('name') != place_name:
            continue
        for npc in place.get('npc') or []:
            identifier = str(npc.get('id') or npc.get('name') or '').strip()
            if not identifier:
                continue
            hint = read_hint(npc.get('name') or '', npc.get('character') or '',
                             npc.get('biography') or '', npc.get('known') or '',
                             *list(npc.get('private_knowledge') or ()))
            rows.append(dict(id=identifier, name=npc.get('name') or identifier,
                             place=place_name, present=bool(npc.get('present', True)),
                             attitude=npc.get('attitude'),
                             joined=identifier in taken,
                             class_slug=hint.get('class_slug') or DEFAULT_CLASS,
                             species_slug=hint.get('species_slug') or DEFAULT_SPECIES,
                             from_book=bool(hint.get('from_book')),
                             evidence=hint.get('evidence') or ''))
    return rows
