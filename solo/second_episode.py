"""Episode two of the pinned book, expressed as data on the shared path.

This module used to hand-build places, nodes, routes, events and actors, one
Python statement per fact. That logic now lives once in episode_plan; what
remains here is the chapter itself - the pages, the places, the printed fight -
with the page each fact comes from. A new chapter is another plan, not another
program.
"""
from __future__ import annotations

from .episode_plan import Beat, EpisodePlan, Fight, Group, Link, Place, Quote, compile as compile_plan, encounters as plan_encounters, verify as verify_plan
from .first_episode import HOARD, flatten, page_text

MODULE = "hoard-episode-2"
TITLE = "Лагерь налетчиков — застава и след Леосина"

CAMP = "Тропа к лагерю налетчиков"
OUTPOST = "Застава отставших"
CAMPFIRE = "Лагерь налетчиков"

#: Pages this chapter is allowed to speak about, with one verified phrase each.
ANCHORS = {
    16: "Молодой человек подходит",
    17: "насчитать четырёх",
    18: "Награда: Персонажи получают",
}

#: Page 16 names the monk who explains the pursuit.
CONTACT = "Нэсим Валадра"
OUTPOST_ENCOUNTER = "outpost-skirmish"
FOUR_CULTISTS = 4
EIGHT_KOBOLDS = 8


def plan():
    """The chapter as data: three places, two links, two beats, one fight."""
    places = [
        Place(key="camp", name=CAMP, x=1, y=1, environment="forest_road",
              quote=Quote(page=16, start="Молодой человек подходит", stop="Нэсим Валадра")),
        Place(key="outpost", name=OUTPOST, x=3, y=2, environment="ruins"),
        Place(key="campfire", name=CAMPFIRE, x=5, y=2, environment="ruins"),
    ]
    links = [
        Link(source="camp", target="outpost", meters=200, minutes=4,
             description="Дым походного костра виден за несколько миль."),
        Link(source="outpost", target="campfire", meters=150, minutes=3,
             description="Дальше начинается ложбина с шатрами налётчиков."),
    ]
    beats = [
        Beat(id=OUTPOST_ENCOUNTER, place=OUTPOST, category="mandatory",
             flag="outpost_reached", page=17,
             notice="Четверо культистов и восемь кобольдов у костра."),
        Beat(id="camp-approach", place=CAMPFIRE, category="conditional",
             flag="camp_reached", page=18,
             notice="Ложбина с шатрами: дальше лагерь налётчиков и пленный монах Леосин."),
    ]
    fights = [Fight(id=OUTPOST_ENCOUNTER, page=17, width=12, height=12,
                    hero_x=1, hero_y=1,
                    groups=[Group(monster="cultist", count=FOUR_CULTISTS, x=6, y=2, prefix="cultist"),
                            Group(monster="kobold-warrior", count=EIGHT_KOBOLDS, x=7, y=1, prefix="kobold")])]
    return EpisodePlan(module=MODULE, title=TITLE, anchors=ANCHORS, document=HOARD,
                       places=places, links=links, beats=beats, fights=fights,
                       continuation_of=["hoard-episode-1"], minimum_level=1,
                       recommended_party=4)


def verify_anchors(library):
    """Refuse to build from pages that do not say what this chapter quotes."""
    return verify_plan(plan(), library)


def encounters():
    return plan_encounters(plan())


def definition(library, ruleset, ruleset_version, *, source_ruleset="dnd2014"):
    """The book is 2014; the mechanics preset is whatever the game runs."""
    return compile_plan(plan(), library, ruleset, ruleset_version, source_ruleset=source_ruleset)


def _outro(library):
    """Page 16: the wounded monk explains what happened to his master."""
    return " ".join(flatten(page_text(library, 16)).split())


__all__ = ["MODULE", "TITLE", "CAMP", "OUTPOST", "CAMPFIRE", "ANCHORS", "CONTACT",
           "OUTPOST_ENCOUNTER", "plan", "verify_anchors", "encounters", "definition"]
