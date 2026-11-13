"""What a person corrected in a parsed scene, and how the correction is applied.

A parser reads the printed forms it knows; a person reads the book. When a line
the parser typed as a place is really a monster of an appendix — or a name it
read as a person is a town — the correction belongs to the book, not to one
parse: it is stored by document, page and name, and applied again after the book
is cut anew. That is the difference between an edit and a temporary patch.

The correction never becomes evidence: the entity keeps its own quote, page and
`provenance`, and only `entity_type` / `scope` change, with `edited` saying a
person, not the page, decided the type.
"""
from .prepared_world import normalized

#: What a person may say a row is. `ignore` keeps the row out of the scene and
#: the world entirely — a line the book prints, but not a thing of this story.
VALUES = ('place', 'npc', 'monster', 'item', 'table', 'ignore')
#: Which list of the scene an edit addresses.
ENTITIES = ('location', 'npc', 'check', 'reference')
#: Not every row can become everything. A check has no printed name of its own,
#: so «сделать проверку местом» would build a place called '' and the book would
#: stop parsing; a person may only keep it or drop it.
ALLOWED = {'location': ('place', 'npc', 'monster', 'item', 'table', 'ignore'),
           'npc': ('npc', 'place', 'monster', 'item', 'table', 'ignore'),
           'check': ('check', 'ignore'),
           'reference': ('monster', 'item', 'table', 'place', 'npc', 'ignore')}


def check_name(row):
    """The stable name of a printed check, so a person can point at one of them.

    The place is part of the name: two «Ловкость (Скрытность) Сл 10» printed in
    different rooms are two checks, and a name without the room would make the
    second one impossible to correct.
    """
    return ('check:' + str(row.get('location') or '') + ':' +
            str(row.get('skill') or row.get('ability') or '') + ':' + str(row.get('dc') or ''))


def key(name):
    """How an edit remembers the printed name it corrects."""
    return normalized(str(name or ''))


def find(scene, entity, name, page):
    """The row of the scene a correction points at, or None."""
    key_name = 'reference' if entity == 'reference' else {'location': 'locations', 'npc': 'npcs',
                                                          'check': 'checks'}.get(entity, '')
    rows = scene.get(key_name) or []
    return _find(list(rows), entity, key(name), int(page or 0))


def _provenance(row, entity):
    """What the page proves about this row, kept apart from its type."""
    known = row.get('provenance') if isinstance(row.get('provenance'), dict) else {}
    source = known.get('source') or ('title' if row.get('binding') == 'title' else 'printed')
    return dict(document=known.get('document') or row.get('document') or '',
                page=known.get('page') or row.get('page') or 0,
                quote=known.get('quote') or row.get('quote') or '', source=source)


def _base(row, entity):
    provenance = _provenance(row, entity)
    return dict(name=row.get('name') or '', page=provenance['page'], document=provenance['document'],
                quote=provenance['quote'], provenance=provenance, edited=True)


def _as_location(row, entity):
    name = str(row.get('name') or '').strip()
    if not name:
        # A row without a printed name cannot stand in the world: the place would
        # be nameless, and the contract refuses it after the edit is already
        # stored, leaving the book unparseable. Refuse before writing anything.
        raise ValueError('У этой строки нет напечатанного названия: местом она стать не может')
    kind = row.get('kind') if entity == 'location' else ''
    if kind in (None, '', 'chapter'):
        kind = 'place'
    return dict(_base(row, entity), name=name, kind=kind,
                gloss=row.get('gloss') or '', mentions=1, binding='edited',
                entity_type='place', scope='scenario')


def _as_npc(row, entity):
    return dict(_base(row, entity), gloss=row.get('gloss') or '',
                location=row.get('location') or '', binding='edited', mentions=1,
                entity_type='npc', scope='scenario')


def _as_reference(row, entity, value):
    return dict(_base(row, entity), kind=value, entity_type=value, scope='reference', source='printed')


def _find(rows, entity, name, page):
    """The row an edit means: same printed name, and the same page when named."""
    for row in rows:
        row_name = check_name(row) if entity == 'check' else str(row.get('name') or '')
        if normalized(row_name) != name:
            continue
        if page and int(row.get('page') or 0) != page:
            continue
        return row
    return None


def _row_key(entity, row):
    """The key the parser de-duplicates a list with, so an edit does not merge rows.

    Checks were keyed by place, ability, skill, saving throw and DC — two printed
    «Ловкость (Скрытность) Сл 10» in different rooms are two checks. De-duplicating
    by name alone silently collapsed them on every parse.
    """
    if entity == 'check':
        return (normalized(str(row.get('location') or '')), str(row.get('ability') or ''),
                str(row.get('skill') or ''), bool(row.get('saving_throw')), str(row.get('dc') or ''))
    if entity == 'reference':
        return (str(row.get('entity_type') or row.get('kind') or ''),
                normalized(str(row.get('name') or '')))
    return normalized(str(row.get('name') or ''))


def _unique(rows, entity):
    seen, result = set(), []
    for row in rows:
        key = _row_key(entity, row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def apply(scene, edits):
    """The scene with every stored correction applied, or the same scene.

    An edit whose row is no longer printed (a page range changed, or the parser
    reads the line differently now) is skipped instead of inventing the row: the
    correction stays stored and applies again if the book prints the line again.
    """
    scene = dict(scene)
    rows = {entity: list(scene.get(key) or []) for entity, key in
            (('location', 'locations'), ('npc', 'npcs'), ('check', 'checks'), ('reference', 'reference'))}
    applied, skipped = [], []
    for edit in edits or []:
        entity = str(edit.get('entity') or '')
        value = str(edit.get('value') or '')
        # A row stored by an older version (or by a version with a wider list of
        # types) is reported, not thrown at the person as a broken book: the rest
        # of the corrections still apply and the page range still parses.
        if entity not in ENTITIES:
            skipped.append(dict(edit=dict(edit), reason='неизвестный вид строки: ' + entity))
            continue
        if value not in VALUES or value not in ALLOWED.get(entity, ()):
            skipped.append(dict(edit=dict(edit), reason='недопустимый тип сущности: '
                                + entity + ' → ' + value))
            continue
        source = _find(rows[entity], entity, normalized(str(edit.get('name') or '')),
                       int(edit.get('page') or 0))
        if source is None:
            skipped.append(dict(edit=dict(edit), reason='строки больше нет в сцене'))
            continue
        rows[entity].remove(source)
        try:
            if value == 'place':
                rows['location'].append(_as_location(source, entity))
            elif value == 'npc':
                rows['npc'].append(_as_npc(source, entity))
            elif value in ('monster', 'item', 'table'):
                rows['reference'].append(_as_reference(source, entity, value))
        except ValueError as error:
            # Строка не годится в новый вид: возвращаем её как была и говорим,
            # почему правка не применилась.
            rows[entity].append(source)
            skipped.append(dict(edit=dict(edit), reason=str(error)))
            continue
        applied.append(dict(entity=entity, name=edit.get('name') or '', page=edit.get('page'),
                            value=value, document=edit.get('document') or ''))
    scene['locations'] = _unique(rows['location'], 'location')
    scene['npcs'] = _unique(rows['npc'], 'npc')
    scene['checks'] = _unique(rows['check'], 'check')
    scene['reference'] = _unique(rows['reference'], 'reference')
    scene['edits'] = applied
    if skipped:
        scene['edits_rejected'] = skipped
    else:
        scene.pop('edits_rejected', None)
    counts = dict(scene.get('counts') or {})
    counts.update(locations=len(scene['locations']), npcs=len(scene['npcs']),
                  checks=len(scene['checks']), reference=len(scene['reference']))
    scene['counts'] = counts
    return scene
