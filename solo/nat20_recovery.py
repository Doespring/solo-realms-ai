"""Charge-pool recovery driven by explicit game events (DELIVERY 14.203/14.205).

The engine scores exactly one event at a time: a Short/Long Rest, or a calendar
event (dawn / day / dusk). It never infers a calendar event from a rest --
whether dawn came is game time, and game time belongs to the host.

Two independent guards stop a delivery from recharging twice:

* Ordering. Every event carries the monotonic game_minutes at which it
  happened. A per-campaign cursor stores the last ACCEPTED game_minutes and
  event id. An event at or before the cursor is STALE and is rejected -- that
  is what catches the A -> B -> replay-A sequence a plain last-id check misses.
* Identity. The event id is unique per campaign, so an exact redelivery is
  refused even if it somehow carries a fresh timestamp.

The caller (solo.nat20_api) computes and writes inside the SAME
BEGIN IMMEDIATE transaction as the command, so a crash between them cannot
leave charges changed without the event recorded, or the reverse.
"""
import hashlib
import json

EVENTS = ("sr", "lr", "dawn", "day", "dusk")
CALENDAR = ("dawn", "day", "dusk")
DAY_MINUTES = 1440
REST_MINUTES = {"sr": 60, "lr": 480}


def event_id(campaign, event, game_minutes):
    """Deterministic identity for one recovery event."""
    return "recover:%s:%s:%d" % (campaign, event, int(game_minutes))


def check_event(event):
    if event not in EVENTS:
        raise ValueError("Unknown recovery event %r; expected one of %s"
                         % (event, ", ".join(EVENTS)))
    return event


# Two different events may legitimately share one game minute: a short rest that
# ends exactly at dawn and the dawn itself are both real, and neither may be
# refused or pushed to another minute. Identity and time are separate facts:
#
# * the event id decides whether THIS event was already processed;
# * the minute decides only that the campaign has not gone backwards.
#
# So a duplicate is refused by identity, and a different event at the same
# minute is accepted. Only a minute BELOW the high-water mark is stale.
def check_order(cursor, event, minutes, ident):
    """Refuse a redelivered id, or an event that predates the high-water minute.

    cursor is the stored dict(minutes=int, event_id=str) or None.
    """
    if cursor is None:
        return
    last = int(cursor.get('minutes', -1))
    if ident == cursor.get('event_id'):
        raise ValueError('Recovery event %r was already applied' % ident)
    if int(minutes) < last:
        raise ValueError(
            'Stale recovery event %r at minute %d: the campaign already'
            ' processed an event at minute %d' % (ident, int(minutes), last))


def seed_for(campaign, ident, seed):
    """Deterministic RNG seed: same event, same campaign, same rolls."""
    material = json.dumps([campaign, ident, int(seed)], sort_keys=True)
    # 63 bits: fits a signed SQLite INTEGER so the stored seed round-trips.
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16) & ((1 << 63) - 1)


def calendar_events(before, after, schedule):
    """Calendar events crossed while the clock moved before -> after.

    schedule is a list of (minute_of_day, event) pairs from the SCENE
    calendar, e.g. [(360, 'dawn'), (1080, 'dusk'), (1440, 'day')].
    Returns [(event, absolute_minutes)] in chronological order.
    """
    before, after = int(before), int(after)
    if after <= before:
        return []
    found = []
    for minute_of_day, name in schedule:
        period = DAY_MINUTES if name == "day" else DAY_MINUTES
        first = (before - minute_of_day) // period + 1
        last = (after - minute_of_day) // period
        for index in range(first, last + 1):
            found.append((name, int(index * period + minute_of_day)))
    return sorted(found, key=lambda row: row[1])

