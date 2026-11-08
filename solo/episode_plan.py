"""One shared path from verified book pages to a playable episode.

Every episode used to spell out the same steps in its own module: check that
the pages still say what we quote, cut the read-aloud text out of the page,
turn places into nodes and routes, turn a location into an event, turn a count
into actors. That work is identical for every chapter, so it lives here once.

A chapter is now data - places, routes, events, actors, anchors - and this
module compiles it. A new chapter is a new data file, not a new program, and
every fact carries the page it came from.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from .adventure_contracts import AdventureDefinition, Effect, Predicate, ScenarioEvent
from .contracts import Strict
from .progression import Progression, book_progression
from .world import ScenarioPackage, WorldLocation, WorldNode, WorldRoute

ENVIRONMENTS = ("room", "forest_road", "ruins", "bridge", "balcony", "cellar")


def flatten(text):
    """Page text as one comparable line, with the PDF line breaks removed."""
    return " ".join(str(text or "").split())


def read_aloud(library, page, start, stop):
    """The printed passage between two anchors, read back from the page.

    A missing anchor raises instead of silently returning a fragment: a quote
    that cannot be located is not a quote.
    """
    from .first_episode import page_text
    flat = flatten(page_text(library, page))
    head, tail = flatten(start), flatten(stop)
    at = flat.find(head)
    if at < 0:
        raise ValueError("Начало цитаты не найдено на странице " + str(page))
    end = flat.find(tail, at)
    if end < 0:
        raise ValueError("Конец цитаты не найден на странице " + str(page))
    return " ".join(flat[at:end + len(tail)].split())


class Quote(Strict):
    """A read-aloud passage, addressed by the anchors that bound it."""

    page: int = Field(ge=1, le=2000)
    start: str = Field(min_length=3, max_length=200)
    stop: str = Field(min_length=1, max_length=200)


class Place(Strict):
    """One location, its grid node, and the printed text the player sees."""

    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=120)
    quote: Quote | None = None
    description: str = Field(default="", max_length=4000)
    x: int = Field(default=0, ge=0, le=80)
    y: int = Field(default=0, ge=0, le=80)
    environment: str = Field(default="room", max_length=20)

    @model_validator(mode="after")
    def known_environment(self):
        if self.environment not in ENVIRONMENTS:
            raise ValueError("Неизвестная среда: " + self.environment)
        return self


class Link(Strict):
    """A route between two places, with the distance the book implies."""

    source: str = Field(min_length=1, max_length=40)
    target: str = Field(min_length=1, max_length=40)
    meters: int = Field(ge=0, le=100000)
    minutes: int = Field(ge=0, le=100000)
    description: str = Field(default="", max_length=2000)


class Beat(Strict):
    """What happens when the party reaches a place.

    The book expresses almost every chapter step this way: a place, a flag and
    a line the table sees. Anything richer stays a normal event on the
    definition instead of growing a second mechanism here.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    place: str = Field(min_length=1, max_length=120)
    category: str = Field(default="conditional", max_length=20)
    flag: str = Field(default="", max_length=80)
    notice: str = Field(default="", max_length=2000)
    page: int = Field(ge=1, le=2000)
    #: A printed reward this beat pays, by the id of its registered rule.
    reward: str = Field(default="", max_length=80)


class Group(Strict):
    """A printed group of creatures: how many, of what, and where they stand."""

    monster: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    count: int = Field(ge=1, le=20)
    x: int = Field(ge=0, le=80)
    y: int = Field(ge=0, le=80)
    step: str = Field(default="y", max_length=1)
    prefix: str = Field(default="", max_length=40)


class Fight(Strict):
    """One encounter the book fixes, with the page that fixes it."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    page: int = Field(ge=1, le=2000)
    width: int = Field(default=10, ge=2, le=80)
    height: int = Field(default=10, ge=2, le=80)
    hero_x: int = Field(default=1, ge=0, le=80)
    hero_y: int = Field(default=1, ge=0, le=80)
    groups: list[Group] = Field(min_length=1, max_length=8)

    def actors(self):
        rows = []
        for group in self.groups:
            for index in range(group.count):
                along = index if group.step == "y" else 0
                across = index if group.step == "x" else 0
                name = group.prefix or group.monster
                rows.append(dict(
                    id=f"{name}-{index + 1}",
                    monster=group.monster,
                    position=dict(x=group.x + across, y=group.y + along, z=0)))
        return rows

    def encounter(self):
        return dict(grid=dict(width=self.width, height=self.height),
                    hero_position=dict(x=self.hero_x, y=self.hero_y, z=0),
                    actors=self.actors())


class EpisodePlan(Strict):
    """A whole chapter as data, before anything of it is executable."""

    module: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    anchors: dict[int, str] = Field(default_factory=dict, max_length=40)
    places: list[Place] = Field(min_length=1, max_length=60)
    links: list[Link] = Field(default_factory=list, max_length=120)
    beats: list[Beat] = Field(default_factory=list, max_length=120)
    fights: list[Fight] = Field(default_factory=list, max_length=40)
    continuation_of: list[str] = Field(default_factory=list, max_length=20)
    minimum_level: int = Field(default=1, ge=1, le=20)
    recommended_party: int = Field(default=4, ge=1, le=12)
    progression: Progression | None = None
    document: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def consistent(self):
        keys = {p.key for p in self.places}
        if len(keys) != len(self.places):
            raise ValueError("Повтор места в эпизоде")
        for link in self.links:
            if link.source not in keys or link.target not in keys:
                raise ValueError("Маршрут ссылается на неизвестное место")
        names = {p.name for p in self.places}
        for beat in self.beats:
            if beat.place not in names:
                raise ValueError("Событие ссылается на неизвестное место")
        if len({f.id for f in self.fights}) != len(self.fights):
            raise ValueError("Повтор встречи в эпизоде")
        return self


def verify(plan, library, *, reader=None, check_anchors=True):
    """Refuse to build when a page no longer says what the plan quotes.

    reader lets a chapter keep its own page normalisation (episode one folds
    the PDF hyphenation marker, for instance) without copying the structural
    work. It defaults to this module reader.
    """
    from .first_episode import page_text
    read = reader or read_aloud
    if check_anchors:
        for page, phrase in plan.anchors.items():
            if flatten(phrase) not in flatten(page_text(library, page)):
                raise ValueError("Источник не подтверждает «" + phrase + "» на странице " + str(page))
    for place in plan.places:
        if place.quote is not None:
            read(library, place.quote.page, place.quote.start, place.quote.stop)
    # A beat may only pay a reward the chapter registers; otherwise the runtime
    # would have to invent a price, which is exactly what must not happen.
    from .progression import book_progression
    registered = {rule.id for rule in (plan.progression or book_progression()).rewards}
    for beat in plan.beats:
        if beat.reward and beat.reward not in registered:
            raise ValueError("Событие «" + beat.id + "» выдаёт незарегистрированную награду: " + beat.reward)
    return True


def encounters(plan):
    return {fight.id: fight.encounter() for fight in plan.fights}


def compile(plan, library, ruleset, ruleset_version, *, source_ruleset="dnd2014",
            reader=None, check_anchors=True):
    """The shared compile step every chapter goes through."""
    verify(plan, library, reader=reader, check_anchors=check_anchors)
    read = reader or read_aloud
    places, nodes = [], []
    for place in plan.places:
        text = place.description
        if place.quote is not None:
            text = read(library, place.quote.page, place.quote.start, place.quote.stop)
        places.append(WorldLocation(name=place.name, description=text, known_to_hero=True))
        nodes.append(WorldNode(id=place.key, name=place.name,
                               coordinates=[place.x, place.y],
                               environment=place.environment))
    routes = [WorldRoute(**{"from": link.source, "to": link.target},
                          meters=link.meters, minutes=link.minutes,
                          description=link.description)
              for link in plan.links]
    events = []
    for beat in plan.beats:
        effects = []
        if beat.flag:
            effects.append(Effect(kind="flag", target=beat.flag, value=True))
        if beat.notice:
            effects.append(Effect(kind="notice", text=beat.notice, public=True))
        if beat.reward:
            # Paid through the ledger, once, with the page the rule carries.
            effects.append(Effect(kind="reward", target=beat.reward))
        events.append(ScenarioEvent(
            id=beat.id, category=beat.category,
            conditions=[Predicate(path="location", op="eq", value=beat.place)],
            effects=effects,
            source=dict(document=plan.document, page=beat.page, review="confirmed")))
    return AdventureDefinition(
        id=plan.module, title=plan.title, ruleset=ruleset,
        ruleset_version=ruleset_version,
        source_ruleset=source_ruleset, source_ruleset_version=1,
        reviewed=True, minimum_level=plan.minimum_level,
        recommended_party=plan.recommended_party,
        events=events, continuation_of=list(plan.continuation_of),
        progression=plan.progression or book_progression(),
        world=ScenarioPackage(name=plan.title, source=plan.module,
                              locations=places, nodes=nodes, routes=routes))


__all__ = ["EpisodePlan", "Place", "Quote", "Link", "Beat", "Fight", "Group",
           "flatten", "read_aloud", "verify", "compile", "encounters", "ENVIRONMENTS"]
