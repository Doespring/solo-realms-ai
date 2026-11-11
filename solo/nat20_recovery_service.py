"""Host side of charge-pool recovery: game time, ordering, atomic write.

The engine (dnd5e_engine.rest) scores one event against one item's rule and
knows nothing about time or storage. This module owns both:

* which items the hero actually carries, as engine slugs;
* when dawn/day/dusk happen in game time (from the scene calendar);
* the per-campaign cursor that makes a delivery exactly-once.

Every call happens INSIDE the caller's BEGIN IMMEDIATE transaction, so the
charge change and the recovery_events row commit together or not at all.
"""
import json
import random

from dnd5e_engine import recover_item_and_activity_pools

from .nat20_recovery import (DAY_MINUTES, calendar_events, check_event,
                             check_order, event_id, seed_for)

DEFAULT_SCHEDULE = ((360, "dawn"), (1080, "dusk"), (1440, "day"))


def schedule_of(state):
    """The scene calendar as (minute_of_day, event) pairs."""
    scene = ((state.get('world') or {}).get('calendar') or [])
    rows = []
    for row in scene:
        if not isinstance(row, dict):
            continue
        name, minute = row.get('event'), row.get('minute')
        if name in ('dawn', 'day', 'dusk') and isinstance(minute, int):
            rows.append((minute, name))
    return tuple(sorted(rows)) or DEFAULT_SCHEDULE


def carried_slugs(state):
    """Engine slugs of every item the hero carries, plus equipped ones."""
    catalog = {}
    try:
        from .inventory import CATALOG
        catalog = CATALOG
    except Exception:
        catalog = {}
    found = []
    for item in (state.get('economy') or {}).get('items') or []:
        if item.get('owner') != 'hero' or int(item.get('quantity') or 0) < 1:
            continue
        slug = item.get('catalog') or ''
        if slug and slug not in catalog and slug not in found:
            found.append(slug)
    # Starting equipment comes from the build, not from Economy.
    build = ((state.get('nat20') or {}).get('definition') or {}).get('build') or {}
    for slug in build.get('equipment') or ():
        if slug and slug not in found:
            found.append(slug)
    return found


def counters_of(state):
    hero = ((state.get('nat20') or {}).get('hero') or {})
    return hero.get('custom_counters') or {}


def apply(state, cursor, event, minutes, campaign, loader):
    """Score one recovery event; return (result, delta, new_cursor).

    Pure with respect to the database: the caller persists the delta and the
    cursor in its own transaction. Raises on an unknown or stale event.
    """
    check_event(event)
    ident = event_id(campaign, event, minutes)
    check_order(cursor, event, minutes, ident)
    slugs = carried_slugs(state)
    counters = counters_of(state)
    before = {key: dict(value) for key, value in counters.items()}
    if slugs:
        rng = random.Random(seed_for(campaign, ident, (state.get('nat20') or {}).get('seed') or 0))
        recovered = recover_item_and_activity_pools(before, event, slugs, loader, rng=rng)
    else:
        recovered = {}
    delta = {}
    for key, spent in recovered.items():
        was = int((before.get(key) or {}).get('spent', 0))
        if int(spent) != was:
            delta[key] = {'before': was, 'after': int(spent)}
    return dict(event=event, minutes=int(minutes), event_id=ident, items=list(slugs),
                recovered=recovered, delta=delta), recovered, dict(minutes=int(minutes), event_id=ident)


def crossed(before, after, state):
    """Calendar events the clock crossed while moving before -> after."""
    return calendar_events(before, after, schedule_of(state))


def plan(before, after, state, *, skip=None):
    """Calendar events crossed while the clock moved before -> after.

    Chronological by minute. Several events may share one minute; they are
    distinguished later by their id, never by nudging the world clock.
    """
    # Half-open on the scored minute: calendar_events counts a boundary AT
    # before, but a minute the cursor already covers is done, not owed again.
    start = int(before) if skip is None else max(int(before), int(skip) + 1)
    found = calendar_events(start, int(after), schedule_of(state))
    if skip is None:
        return found
    return [(name, minute) for name, minute in found if int(minute) > int(skip)]

def cursor_of(state):
    """How far the calendar has been scored, kept with the campaign state.

    A campaign gains minutes through paths that never consult this module
    (a scenario wait, an adventure step, a combat round). Remembering the
    scored frontier lets the next mutating command catch up instead of
    silently losing the boundaries crossed in between.

    A load may put back a save older than minutes already played here, so the
    stored cursor can sit ahead of the clock. Trusting it would mean every
    boundary up to it is treated as scored and the owed charges are lost for
    good, so the cursor never claims more than the clock has reached.
    """
    stored = int((state.get('nat20') or {}).get('recovery_minute', 0) or 0)
    now = int(state.get('elapsed_minutes', 0) or 0)
    return min(stored, now)


def pending(state):
    """Calendar events owed between the scored frontier and the clock."""
    now = int(state.get('elapsed_minutes', 0) or 0)
    return calendar_events(cursor_of(state), now, schedule_of(state)), now

def abandoned(db, campaign, started):
    """Recovery row ids that an undone or reloaded turn took into the future.

    Rows stay as history; what a load or an undo discards is their claim on the
    active timeline. The row is safe to score again when the turn that produced
    it has been undone and the live line now stands at or before the minute it
    covers, so the same minute was crossed by a branch that no longer counts.

    started is where the current command began. A row at or below it belongs to
    a turn that has already been discarded, never to a legal future: the live
    line cannot reach that minute again without crossing it.
    """
    undone = db.execute(
        "SELECT before_state,after_state FROM events WHERE campaign=? AND undone=1",
        (campaign,)).fetchall()
    # The minute each undone turn ENDED on: that is the minute its events
    # cover, and the clock the active line has since rolled back from.
    ends = []
    for row in undone:
        state = json.loads(row["after_state"])
        ends.append(int(state.get('elapsed_minutes', 0) or 0))
    if not ends:
        return set()
    rows = db.execute(
        "SELECT r.id,r.event,r.game_minutes FROM recovery_events r "
        "WHERE r.campaign=? AND r.id NOT IN ("
        "  SELECT rr.id FROM recovery_events rr JOIN events e ON e.id=rr.turn "
        "  WHERE rr.campaign=? AND e.undone=0)",
        (campaign, campaign)).fetchall()
    found = set()
    for row in rows:
        minutes = int(row['game_minutes'])
        # Already behind the live line: this minute was crossed by a branch
        # that no longer counts, so a fresh turn may cross it again.
        if minutes <= int(started):
            found.add(row['id'])
            continue
        # Ahead of the live line: only a turn that ended exactly here and has
        # since been undone can account for it. A turn that merely walked past
        # this minute did not produce it, so its row keeps the guard.
        if minutes in ends:
            found.add(row['id'])
    return found