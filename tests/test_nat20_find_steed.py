"""Find Steed: форма выбирается игроком, и ровно один скакун садится.

SRD 5.2 Find Steed несёт ДВЕ активности — "Summon Steed" и "Summon
Flying Steed" — и три профиля (Celestial, Fey, Fiend). Пока движок
исполнял все активности разом, одно заклинание за один слот сажало ДВУХ
скакунов (раунд 70). Тест бьёт по трём обязательствам сразу:

* выбранная форма даёт своего скакуна, а не произвольного;
* за один слот садится ОДИН скакун;
* неполный запрос (нет формы, нет активности) отклоняется ДО списания
  слота, а не угадывается за игрока (§5, §8).

Проверки идут через публичный интерфейс: submit_player_intent + события.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

STEED = "dnd5eactivity000"       # "Summon Steed"
FLYING = "o4YsONnsS8rOOEaS"      # "Summon Flying Steed"


def _scores():
    return AbilityScores(strength=10, dexterity=14, constitution=14,
                         intelligence=10, wisdom=10, charisma=18)


def _fight(seed=1):
    spec = CharacterBuildSpec(species_slug="human", class_slug="paladin",
                              background_slug="acolyte", level=5,
                              ability_scores=_scores())
    p = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0", initiative=20,
                       hp_max=44, hp_current=44),
        loader=BundledAssetLoader(),
    )
    h = asyncio.run(O.start_combat(
        session_id="s", party=[p],
        encounter=[EncounterMemberSpec(entity_id="m1", name="T",
                                       entity_type="Monster", initiative=1,
                                       hp_current=50, hp_max=50, ac=12,
                                       zone_id="8,4")],
        grid_scene=GridScene(width=12, height=8), rng_seed=seed,
    )).handle
    O.drain_pending_events(h)
    return h


def _slots(h):
    """Оставшиеся слоты заклинаний персонажа: снимок «до/после»."""
    # ``_LiveCombat.spell_slots_by_entity[entity_id][level]`` — оставшиеся.
    live = O._get_live(h)
    slots = live.spell_slots_by_entity.get("pc1", {});
    return {str(k): int(v) for k, v in dict(slots).items()}


def _summons(h):
    live = O._get_live(h)
    return [c.entity_id for c in live.initiative if c.entity_id.startswith("summon:")]


def _cast(h, form=None, activity=None):
    kw = {}
    if form is not None:
        kw["form_id"] = form
    if activity is not None:
        kw["activity_id"] = activity
    before = _slots(h)
    asyncio.run(O.submit_player_intent(
        h, "pc1",
        O.PlayerIntent(intent_type="cast_spell", spell_id="find-steed",
                       target_zone_id="2,0", **kw),
    ))
    events = O.drain_pending_events(h)
    return [e.type for e in events], before, _slots(h)


@pytest.mark.parametrize("form,slug", [
    ("celestial", "celestial-steed"),
    ("fey", "fey-steed"),
    ("fiend", "fiend-steed"),
])
def test_the_chosen_form_seats_exactly_one_steed(form, slug):
    """Выбор формы даёт СВОЕГО скакуна — и ровно одного за слот."""
    h = _fight()
    kinds, before, after = _cast(h, form=form, activity=STEED)
    assert "spell_cast" in kinds, kinds
    summons = _summons(h)
    # Один слот — один скакун. Две активности сразу давали двух.
    assert len(summons) == 1, summons
    assert summons[0].split(":")[2] == slug, summons
    # Слот 2 уровня действительно потрачен: осталось на один меньше.
    key = "2"
    assert int(after.get(key, 0)) == int(before.get(key, 0)) - 1, (before, after)


def test_an_incomplete_request_is_refused_before_the_slot_is_spent():
    """Без формы или без активности заклинание отклоняется, а не угадывается.

    SRD 5.2 не задаёт форму по умолчанию, поэтому молчаливый выбор был бы
    подменой решения игрока (§5). Слот при отказе не тратится (§9).
    """
    for form, activity in ((None, STEED), ("fey", None), (None, None),
                           ("griffon", STEED)):
        h = _fight()
        kinds, before, after = _cast(h, form=form, activity=activity)
        where = f"form={form!r} activity={activity!r}"
        assert "cast_failed" in kinds, (where, kinds)
        assert "spell_cast" not in kinds, (where, kinds)
        assert _summons(h) == [], (where, _summons(h))
        assert after == before, (where, before, after)


def test_the_flying_activity_seats_the_flying_steed():
    """Альтернативная активность разрешает свой вариант, а не первый."""
    h = _fight()
    kinds, _, _ = _cast(h, form="fey", activity=FLYING)
    assert "spell_cast" in kinds, kinds
    assert len(_summons(h)) == 1, _summons(h)
