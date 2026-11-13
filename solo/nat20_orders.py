"""Orders to a companion, and the relation the companion keeps (r216).

Owner's decision 03.11.2026 (SPEC-COMPANIONS item 2, quoted): «ИИ играет
полностью, игрок может отдать приказ или просьбу, но персонаж не обязан
подчиняться или выполнять, должна быть система отношений сопартийцев».

The division of labour is the one r215 uses for a companion's turn:

* the *interpreter* (the model) reads the player's free sentence and names what
  is being asked: a form (``order``/``request``), one of four categories, and
  the companion it addresses. That reading is data; it is printed in the journal,
  so a wrong reading is visible instead of silent;
* the *host* decides - from the companion's own traits and its relation to the
  hero, both stored in the campaign state. A model cannot talk a companion into
  obedience and cannot erase the reason: ``decide`` is the only place a decision
  is made and the table below is the whole rule;
* the *journal* keeps the order, the decision and the relation shift with its
  numbers before and after.

Nothing here is a hidden penalty: every shift is one of the named reasons below,
its size is this table's, and the player-visible sentence carries both numbers.
A companion below ``LEAVE_THRESHOLD`` leaves the party with its own event.
"""
from __future__ import annotations

TRAITS = {'cautious': 'осторожный', 'loyal': 'верный',
          'greedy': 'жадный', 'proud': 'гордый'}

CATEGORIES = {'risk': 'риск для жизни', 'profit': 'личная выгода',
              'against_character': 'против характера', 'normal': 'обычное действие'}

FORMS = {'order': 'приказ', 'request': 'просьба'}

RELATION_MIN, RELATION_MAX = -100, 100
DEFAULT_RELATION = 0
#: Below this relation the companion stops travelling with the hero.
LEAVE_THRESHOLD = -60
#: A betrayal is the strongest negative step the host knows. It is a reason
#: code for a host event (see ``betray``), not something an order can produce.
BETRAYAL = -25
#: The relation a proud companion needs before a bare order stops offending him.
PROUD_ORDER_FLOOR = 40
#: The band in which an ordinary request is answered with silence: too low to
#: obey, high enough not to refuse to the hero's face.
SILENT_FLOOR = -40

#: The relation a companion needs to agree: the category sets the bar, every
#: trait moves it by its own amount. Additive on purpose - a cautious loyal
#: companion is harder to send into danger than a plain one but easier than a
#: purely cautious one, instead of one trait silently winning over another.
BASE = {'risk': 20, 'profit': 0, 'normal': -20, 'against_character': 0}
ADJUST = {
    'risk': {'cautious': 30, 'greedy': 30, 'proud': 20, 'loyal': -40},
    'profit': {'cautious': 10, 'greedy': -40, 'proud': 0, 'loyal': -10},
    'normal': {'cautious': 20, 'greedy': 10, 'proud': 20, 'loyal': -10},
    # A request against the character is refused before any bar is compared; the
    # zeros are here so the table has one shape for every category.
    'against_character': {'cautious': 0, 'greedy': 0, 'proud': 0, 'loyal': 0},
}


#: How much one answer moves the relation. Every row is a named reason, and the
#: journal prints the reason and both numbers, so nothing moves unseen.
SHIFTS = {
    ('risk', 'obeyed'): 5, ('risk', 'refused'): -2, ('risk', 'silent'): -2,
    ('profit', 'obeyed'): 1, ('profit', 'refused'): -1, ('profit', 'silent'): -1,
    ('against_character', 'refused'): -5,
    ('normal', 'obeyed'): 1, ('normal', 'refused'): -1, ('normal', 'silent'): -1,
}


def clamp(value):
    return max(RELATION_MIN, min(RELATION_MAX, int(value)))


def normalise_traits(value):
    """The 2-4 traits the editor named, or an error naming the wrong one.

    An empty list is allowed and means "no strong character": such a companion is
    decided by relation alone. That is not a silent default - the record keeps the
    empty list, and ``decide`` says so in the reason it prints.
    """
    if value is None:
        return []
    traits = []
    for raw in value:
        slug = str(raw or '').strip()
        if slug not in TRAITS:
            raise ValueError('Неизвестная черта спутника: ' + slug + '. Известные: '
                             + ', '.join(sorted(TRAITS)))
        if slug in traits:
            raise ValueError('Черта спутника повторяется: ' + slug)
        traits.append(slug)
    if traits and not 2 <= len(traits) <= 4:
        raise ValueError('У спутника 2-4 черты характера, а не ' + str(len(traits)) + '.')
    return traits


def labels(traits):
    return [TRAITS[slug] for slug in traits if slug in TRAITS]


def floor_for(category, traits):
    """The relation needed to say yes, and the trait that moved the bar most."""
    table = ADJUST[category]
    adjustment = {slug: table[slug] for slug in traits if slug in table}
    floor = BASE[category] + sum(adjustment.values())
    trait = max(adjustment, key=lambda slug: abs(adjustment[slug])) if adjustment else None
    return max(-60, min(90, floor)), trait


def _quote(text, limit=120):
    text = str(text or '').strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _reason(category, traits, form, relation, floor, trait, text):
    """A refusal the player can act on: what is wrong and what would change it."""
    needed = str(relation) + ' из ' + str(floor)
    if category == 'against_character':
        who = TRAITS.get(trait) if trait else ''
        head = 'Отказывается: это против его характера' + (' (' + who + ')' if who else '')
        return head + ' — «' + _quote(text) + '».'
    if form == 'order' and trait == 'proud':
        return ('Не станет слушать приказ: гордый. Попросите — и он подумает '
                '(доверие к герою ' + str(relation) + ', приказом нужно '
                + str(PROUD_ORDER_FLOOR) + ').')
    if category == 'risk':
        if trait == 'greedy':
            return ('Не пойдёт первым в опасное место, если не заплатишь: жадный, '
                    'а доверие к герою ' + str(relation) + ' меньше нужных ' + str(floor) + '.')
        if trait == 'cautious':
            return ('Отказывается идти первым в опасное место: осторожен, '
                    'доверие к герою ' + needed + '.')
        if trait == 'loyal':
            return ('Отказывается рисковать жизнью: верен, но доверие к герою '
                    'упало до ' + str(relation) + '.')
        if trait == 'proud':
            return ('Отказывается рисковать ради героя: гордый, доверие к герою '
                    + needed + '.')
        return ('Отказывается: в опасное место без веры в героя не идёт '
                '(доверие ' + needed + ').')
    if category == 'profit':
        if trait == 'greedy':
            return ('Отказывается: доля мала, а доверие к герою ' + str(relation)
                    + ' — за так работать не станет (нужно ' + str(floor) + ').')
        if trait == 'cautious':
            return ('Отказывается ради чужой выгоды: осторожен, на дело такого рода '
                    'нужно доверие ' + str(floor) + ', а есть ' + str(relation) + '.')
        return ('Отказывается ради чужой выгоды: доверие к герою ' + str(relation)
                + ' меньше нужных ' + str(floor) + '.')
    return ('Отказывается: доверие к герою ' + str(relation)
            + ' слишком низкое для простой услуги (нужно ' + str(floor) + ').')


def decide(relation, traits, category, form, *, text=''):
    """The host's answer to one order: obey, stay silent, or refuse — with why.

    The result is ``{'decision', 'reason', 'delta', 'floor', 'trait'}``; the
    delta is the table's row for this category and answer, so the same answer
    always moves the relation by the same number.
    """
    relation = clamp(relation)
    traits = [slug for slug in traits or () if slug in TRAITS]
    category = category if category in CATEGORIES else 'normal'
    form = form if form in FORMS else 'request'
    floor, trait = floor_for(category, traits)
    if category == 'against_character' or (
            form == 'order' and 'proud' in traits and relation < PROUD_ORDER_FLOOR):
        # A bare order to a proud companion is itself against his character; the
        # reason names that, so the player knows what to change (ask, not command).
        trait = trait or 'proud'
        decision = 'refused'
    elif relation >= floor:
        decision = 'obeyed'
    elif category == 'normal' and relation >= SILENT_FLOOR:
        decision = 'silent'
    else:
        decision = 'refused'
    if decision == 'obeyed':
        if category == 'risk':
            reason = ('Согласие: риск перевешивается доверием к герою ('
                      + str(relation) + ').')
        else:
            reason = 'Согласие: «' + _quote(text) + '» — не против.'
    elif decision == 'silent':
        reason = ('Молчание: доверие к герою ' + str(relation)
                  + ' — ни отказать в лицо, ни подчиниться, и с места не двигается.')
    else:
        reason = _reason(category, traits, form, relation, floor, trait, text)
    return dict(decision=decision, reason=reason, delta=SHIFTS.get((category, decision), 0),
                floor=floor, trait=trait)


def _record(member, action, verdict, before, after):
    return dict(id=str(member.get('id')), name=str(member.get('name') or member.get('id')),
                text=_quote(action.text, 300), category=action.category, form=action.form,
                traits=list(member.get('traits') or ()), decision=verdict['decision'],
                reason=verdict['reason'], relation_before=before, relation_after=after,
                left=False)


def events_for(record):
    """The journal events of one order, in the order they happened."""
    member = record['id']
    events = [dict(type='order', actor_id='hero', target_id=member, text=record['text'],
                   category=record['category'], form=record['form'],
                   classified_by='interpreter')]
    kind = {'obeyed': 'order_obeyed', 'refused': 'order_refused',
            'silent': 'order_silent'}[record['decision']]
    events.append(dict(type=kind, target_id=member, name=record['name'],
                       reason=record['reason'], category=record['category']))
    if record['relation_before'] != record['relation_after']:
        events.append(dict(type='relation_shift', target_id=member,
                           reason=record['decision'] + ':' + record['category'],
                           before=record['relation_before'], after=record['relation_after']))
    if record['left']:
        events.append(dict(type='companion_left', target_id=member, name=record['name'],
                           reason='доверие к герою упало до ' + str(record['relation_after']),
                           relation=record['relation_after']))
    return events


def apply(state, action):
    """One order, decided and written into ``state``; returns (result, events).

    ``state`` is the Nat20 durable state as plain data; the companion record is
    edited in place, so the caller's single validation at the exit is what makes
    the change canonical. An order is a scene decision, never a combat action:
    during a fight the master already plays the companion (r215).
    """
    if state.get('combat'):
        raise ValueError('В бою спутник действует сам: приказ отдаётся до боя или после.')
    member_id = str(getattr(action, 'target_id', '') or '')
    party = list(state.get('party') or [])
    member = next((item for item in party
                   if str(item.get('id')) == member_id and item.get('role') == 'companion'), None)
    if member is None:
        names = ', '.join(str(item.get('name')) + ' (' + str(item.get('id')) + ')'
                          for item in party if item.get('role') == 'companion')
        raise ValueError('Такого спутника в партии нет: ' + (member_id or 'пусто')
                         + ('. В партии: ' + names if names else '. Спутников нет.'))
    before = clamp(member.get('relation') or DEFAULT_RELATION)
    traits = normalise_traits(member.get('traits'))
    verdict = decide(before, traits, action.category, action.form, text=action.text)
    record = _record(member, action, verdict, before, clamp(before + verdict['delta']))
    member['relation'] = record['relation_after']
    if record['relation_after'] <= LEAVE_THRESHOLD:
        # The companion does not stay with someone he no longer trusts. The
        # removal happens here, before validation, so the party never carries a
        # member the screen would still show.
        record['left'] = True
        state['party'] = [item for item in party if item is not member]
    return {'order': record}, events_for(record)


def betray(state, member_id, reason):
    """A betrayal: the strongest named shift, recorded like any other event.

    No current route produces it (an order is not a betrayal); it exists so the
    next host event that earns it - a broken promise, a left ally - uses the same
    table and the same journal instead of inventing a private number.
    """
    party = list(state.get('party') or [])
    member = next((item for item in party if str(item.get('id')) == str(member_id)), None)
    if member is None:
        raise ValueError('Такого члена партии нет: ' + str(member_id))
    before = clamp(member.get('relation') or DEFAULT_RELATION)
    after = clamp(before + BETRAYAL)
    member['relation'] = after
    events = [dict(type='relation_shift', target_id=str(member_id), reason='betrayal: ' + str(reason),
                   before=before, after=after)]
    if after <= LEAVE_THRESHOLD:
        state['party'] = [item for item in party if item is not member]
        events.append(dict(type='companion_left', target_id=str(member_id),
                           name=str(member.get('name') or member_id),
                           reason='предательство', relation=after))
    return events


def sentence(record):
    """The journal line of one order: what was asked, what the companion did, why."""
    if not record:
        return ''
    name = str(record.get('name') or record.get('id') or 'спутник')
    asked = str(record.get('text') or '').strip()
    answer = {'obeyed': 'согласие', 'refused': 'отказ', 'silent': 'молчание'}.get(
        record.get('decision'), str(record.get('decision')))
    category = CATEGORIES.get(str(record.get('category')), str(record.get('category')))
    line = (name + ', ' + category + ': ' + answer
            + (' на «' + asked + '»' if asked else '') + '. ' + str(record.get('reason')))
    before, after = record.get('relation_before'), record.get('relation_after')
    if before is not None and after is not None and before != after:
        line += ' Отношение: ' + str(before) + ' → ' + str(after) + '.'
    if record.get('left'):
        line += ' ' + name + ' покидает партию.'
    return line


def journal_text(record):
    return sentence(record)


def projection(member):
    """What the party screen shows about one companion's relation, read from data."""
    relation = clamp(member.get('relation') or DEFAULT_RELATION)
    traits = [slug for slug in (member.get('traits') or ()) if slug in TRAITS]
    return dict(relation=relation, traits=traits, trait_names=labels(traits),
                relation_band=('враждебно' if relation <= LEAVE_THRESHOLD else
                               'настороженно' if relation < 0 else
                               'ровно' if relation < 40 else 'дружески'))
