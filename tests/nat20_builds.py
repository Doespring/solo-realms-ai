"""Legal test builds, filled from the native advancement data.

A build is only a character once every obligatory choice is made: the
background and its +2/+1, the class skills, the feature pools (fighting style,
Divine Order, origin feat), the subclass at its gate level, and each Ability
Score Improvement. Hand-writing that list in every test made the tests drift
from the rules, so this module asks the native module which picks a build owes
and fills them from the pools the corpus actually offers.

It is a fixture generator, not an assertion: a test that checks legality still
writes the illegal build by hand (see tests/test_nat20_advancement.py).
"""
from functools import lru_cache

from dnd5e_engine.build_spec import CharacterBuildSpec
from dnd5e_engine.rules.advancement import choice_options

STANDARD_ARRAY = dict(
    strength=15, dexterity=13, constitution=14, intelligence=12, wisdom=10, charisma=8
)

# Skills before expertise (an expertise pick needs a proficient skill), feature
# pools before the Ability Score Improvement that shares no pool with them.
RANK = {"background": 0, "increase": 0, "skill": 1, "expertise": 2, "feature": 3, "asi": 4}


@lru_cache(maxsize=1)
def loader():
    """The pinned corpus loader the release path returns, built once per run."""
    from solo.nat20_content import release
    from solo.nat20_prototype import RELEASE

    return release(RELEASE)[0]


def _increase(row):
    """A legal background increase: "+2 and +1", or three "+1"s when capped."""
    pool = list(row["pool"])
    if row["points"] == 3 and row["cap"] >= 2:
        return "background:{}+2,{}+1".format(pool[0], pool[1])
    return "background:" + ",".join(slug + "+1" for slug in pool[: row["points"]])


def _granted_by_background(build, corpus):
    """Feats the background already gives; picking them again is a repeat."""
    background = corpus.get_background(build.background_slug) if build.background_slug else None
    return {background.starting_feat_slug} if background and background.starting_feat_slug else set()


def legal_picks(build, *, corpus):
    """Every pick the build owes, chosen from the pools the corpus offers."""
    rows = choice_options(build, loader=corpus, include_advisory=False)
    rows = sorted(rows, key=lambda row: (RANK.get(row["kind"], 9), row["level"], row["source"]))
    picks = []
    for row in rows:
        if row["kind"] == "increase":
            picks.insert(0, _increase(row))
    taken = set(_granted_by_background(build, corpus))
    expertise_pools = [set(row["pool"]) for row in rows if row["kind"] == "expertise"]
    skilled = []

    for row in rows:
        # background_slug and subclass_slug are fields, not picks, so a row for
        # either is already spent.
        if row["kind"] in ("background", "increase", "subclass", "expertise"):
            continue
        pool = [slug for slug in row["pool"] if slug not in taken]
        for _ in range(row["count"]):
            if row["kind"] == "asi":
                owner = row["source"].split(":", 1)[1]
                picks.append(
                    "asi:{}:{}:{}+{}".format(owner, row["level"], row["pool"][0], row["points"])
                )
                continue
            if not pool:
                break
            if row["kind"] == "skill":
                # Prefer a skill the build's expertise pools can later raise.
                choice = next(
                    (slug for slug in pool if any(slug in p for p in expertise_pools)), pool[0]
                )
                picks.append("skill:" + choice)
                skilled.append(choice)
            else:
                choice = pool[0]
                picks.append(choice)
            pool.remove(choice)
            taken.add(choice)

    for row in rows:
        if row["kind"] != "expertise":
            continue
        # The pool names the skills expertise may raise; the pick has to be one
        # the build is already proficient in, which is what skilled holds.
        pool = list(row["pool"])
        for _ in range(row["count"]):
            choice = next((slug for slug in skilled if slug in pool), None)
            if choice is None:
                raise ValueError("build has no proficient skill its expertise pool allows")
            picks.append("expertise:" + choice)
            pool.remove(choice)
            skilled.remove(choice)
    return picks


def legal_spec(*, background="soldier", corpus=None, **payload):
    """A build every advancement rule accepts, with the picks it needs."""
    corpus = corpus or loader()
    payload.setdefault("ability_scores", STANDARD_ARRAY)
    payload.setdefault("ability_score_method", "standard_array")
    payload.setdefault("equipment", ())
    payload.setdefault("background_slug", background)
    payload["selected_choices"] = ()
    draft = CharacterBuildSpec(**payload)
    picks = legal_picks(draft, corpus=corpus)
    return CharacterBuildSpec(**{**payload, "selected_choices": tuple(picks)})


def legal(*, background="soldier", corpus=None, **payload):
    """legal_spec as the JSON-shaped dict the HTTP tests post."""
    return legal_spec(background=background, corpus=corpus, **payload).model_dump(mode="json")
