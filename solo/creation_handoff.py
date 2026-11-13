"""One handoff from the eight-step master to the Nat20 launch.

The old master (``web/preparation.js``) and the launch (``web/creation.js`` and
``web/wizard.js``) used to be two state machines with one broken edge between
them: the master's last button created a *new empty* creation draft, so the
hero, the start and the chosen items stayed behind and the player opened
"Новое приключение" with nothing in it.

This module is the single record both sides read: the draft that already holds
the player's answers is named explicitly (``draft_id``, ``module_id``,
``hero_draft``, ``equipment``) instead of being re-created, and a draft that
still lacks a part says which part and which step fills it. The launch never
receives an empty screen for a half-written draft; it receives the list.
"""
import inspect

from .inventory import CATALOG
from .preparation import Draft

#: The master's own step names, so "чего не хватает" can say where to go.
STEP_TITLES = {0: 'Источник', 1: 'Правила и Мастер', 2: 'Подготовка',
               3: 'Подтверждение', 4: 'Лист героя', 5: 'Предыстория',
               6: 'Снаряжение', 7: 'Начало истории'}

#: What the campaign keeps as the hero's own words, not as mechanics: the
#: Nat20 sheet rebuilds every number, so only the player's text travels.
HERO_TEXT = ('name', 'background', 'species', 'class_name', 'subclass', 'level',
             'hp', 'max_hp', 'ac', 'origin', 'personality', 'appearance',
             'goals', 'bonds', 'ideals', 'alignment')


def hero_draft(body):
    """The hero exactly as the master recorded him, without the empty fields."""
    hero = dict((body or {}).get('hero') or {})
    return dict((key, hero[key]) for key in HERO_TEXT
                if hero.get(key) not in (None, '', [], {}))


def item_rows(ids):
    """The chosen items with the master's own Russian names, for the screen."""
    return [dict(id=str(key), name=(CATALOG.get(key) or {}).get('name') or str(key))
            for key in (ids or ())]


def missing_parts(body):
    """Everything the launch needs and the draft does not have yet.

    Each row names the part and the master's step that fills it, so the screen
    shows "чего не хватает" and a button to that step instead of an empty
    adventure.
    """
    def need(ok, key, text, step):
        if not ok:
            rows.append(dict(key=key, text=text, step=step, title=STEP_TITLES[step]))
    rows = []
    hero = dict((body or {}).get('hero') or {})
    need(bool(str(hero.get('name') or '').strip()), 'hero_name',
         'Имя героя не заполнено', 4)
    need(bool(str(hero.get('species') or '').strip() and str(hero.get('class_name') or '').strip()),
         'hero_sheet', 'Лист героя не собран: нужны вид и класс', 4)
    need(bool((body or {}).get('background_confirmed')), 'background',
         'Предыстория героя не подтверждена', 5)
    need(bool((body or {}).get('equipment')), 'equipment',
         'Не выбрано ни одного предмета', 6)
    need(bool(str((body or {}).get('start_name') or '').strip()), 'start_name',
         'Старт не назван', 2)
    need(bool(str((body or {}).get('opening') or '').strip()), 'opening',
         'Нет открывающей сцены', 2)
    need(bool((body or {}).get('prepared')), 'prepared',
         'Подготовка книги не подтверждена', 3)
    need(bool((body or {}).get('adventure_bundle')), 'materials',
         'К черновику не привязаны материалы книги', 0)
    return rows


def book_of(library, body):
    """The module the master chose, and whether the launch can open it.

    A module the library cut and reviewed is playable as the opening scene; one
    that was never cut is not, and the record says so instead of letting the
    launch pretend an empty world is the book.
    """
    row = dict(bundle=(body or {}).get('adventure_bundle') or '',
               module=(body or {}).get('adventure_module') or '',
               title='', ready=False, pages=[])
    if library is None or not row['bundle']:
        return row
    try:
        modules = library.modules(row['bundle'])
    except (KeyError, ValueError):
        modules = []
    # An appendix prints reference pages, not an episode: it is never the module
    # a campaign opens, even when its places were imported into the world.
    playable = [m for m in modules if m.get('kind') != 'appendix' and m['ready']]
    chosen = (next((m for m in playable if m['id'] == row['module']), None)
              or (playable[0] if playable else None))
    if chosen:
        row.update(module=chosen['id'], title=chosen.get('title') or '',
                   ready=True, pages=list(chosen.get('source_pages') or []))
    return row


def record(preparation, draft_id, *, library=None):
    """The explicit handoff the launch reads; never a second draft."""
    draft = preparation.get(draft_id)
    body = Draft.model_validate(draft['body'])
    snapshot = body.model_dump(mode='json')
    missing = missing_parts(snapshot)
    return dict(draft_id=draft_id,
                module_id=body.adventure_module,
                hero_draft=hero_draft(snapshot),
                equipment=list(body.equipment),
                equipment_items=item_rows(body.equipment),
                start=body.start_name, opening=body.opening,
                book=book_of(library, snapshot),
                flow=body.flow, revision=draft['revision'],
                campaign=draft['campaign'],
                missing=missing, ready=not missing)


def verify(state, *, module_id=None, hero_draft=None, equipment=None):
    """The parts the screen handed over must still be the saved draft's parts.

    A master step left open in another tab, or a second browser, would otherwise
    start a campaign on a hero or a list of items the player never approved.
    """
    if module_id is not None and str(module_id) != str(state.get('module_id') or ''):
        raise ValueError('Выбранное приключение изменилось: обновите шаг мастера.')
    if equipment is not None and list(equipment) != list(state.get('equipment') or ()):
        raise ValueError('Снаряжение изменилось: обновите шаг мастера.')
    if hero_draft is not None:
        sent = str((hero_draft or {}).get('name') or '')
        saved = str((state.get('hero_draft') or {}).get('name') or '')
        if sent != saved:
            raise ValueError('Герой изменился: обновите шаг мастера.')
    return True


def carry(store, campaign, preparation, draft_id):
    """The master-approved items become the hero's own kit in the new story.

    The Nat20 sheet owns the mechanics, so the items are not written into its
    build: they are recorded once, with their source, and the character screen
    shows them beside the sheet's equipment. A replay of the same draft does not
    add a second copy — the journal request id is the draft's.
    """
    draft = preparation.get(draft_id)
    body = Draft.model_validate(draft['body'])
    items = list(body.equipment)
    if not items:
        return []
    rows = item_rows(items)

    def transition(state):
        prepared = dict(state.get('preparation') or {})
        prepared['equipment'] = items
        prepared['master_draft'] = draft_id
        state['preparation'] = prepared
        return [dict(type='scripted', kind='equipment_carried',
                     items=rows, source='master-draft:' + draft_id,
                     text='Снаряжение из подготовки Мастера: '
                          + ', '.join(row['name'] for row in rows) + '.')]

    _record(store, campaign, 'carried-equipment:' + draft_id,
            'Начальное снаряжение из подготовки Мастера', transition)
    return rows


def _record(store, campaign, request_id, summary, transition):
    """Record the carried kit without counting it as the hero's turn.

    The turn-neutral flag arrived with the start-of-campaign balance work; a
    build that does not know it still records the note, as an ordinary event.
    """
    options = {}
    if 'counts_as_turn' in inspect.signature(store.record_scenario).parameters:
        options['counts_as_turn'] = False
    return store.record_scenario(campaign, request_id, summary, transition, **options)
