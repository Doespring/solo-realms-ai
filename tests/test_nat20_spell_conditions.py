"""Состояния от заклинаний: связь данных с механизмом наложения.

Пункт 7 запрещает считать способность реализованной потому, что JSON загружается
или обработчик вернул успех. Здесь проверяется фактическая связь: список
`passive_effects[].statuses` — это ровно то, что движок превращает в
`ConditionApplied` (`activities/effects.py:332-345`).

Тест фиксирует и известный пробел ДАННЫХ: у части заклинаний со спасброском нет
ни урона, ни состояний, ни изменений, поэтому движку нечего исполнять.
Перечень назван поимённо, чтобы пробел не выдавался за покрытие.
"""
import pytest

from dnd5e_engine.rules.conditions import Condition
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _raw(loader, slug):
    return loader.get_spell(slug).model_dump(mode="json")


def _statuses(raw):
    out = set()
    for effect in raw.get("passive_effects") or []:
        out |= set(effect.get("statuses") or [])
    return out


def test_sixty_spells_impose_conditions_through_data(loader):
    imposing = {
        slug: sorted(_statuses(_raw(loader, slug)))
        for slug in loader.list_slugs("spells")
        if _statuses(_raw(loader, slug))
    }
    assert len(imposing) == 60
    # Сверка по SRD 5.2: эти состояния не догадка.
    assert imposing["hold-person"] == ["paralyzed"]
    assert imposing["animal-friendship"] == ["charmed"]
    assert imposing["blindness-deafness"] == ["blinded", "deafened"]
    assert imposing["black-tentacles"] == ["restrained"]
    assert imposing["earthquake"] == ["prone"]


def test_the_corpus_also_carries_foundry_only_markers(loader):
    """Корпус использует и пометки Foundry вне пятнадцати условий SRD.

    Движок накладывает `ConditionApplied` только для известных условий
    (`activities/effects.py`), а прочие оставляет в `statuses` эффекта как
    повествовательные. Это объявленное поведение, а не потеря механики.
    """
    known = {c.value for c in Condition}
    beyond = set()
    for slug in loader.list_slugs("spells"):
        beyond |= _statuses(_raw(loader, slug)) - known
    assert beyond == {"cursed", "ethereal", "dead", "transformed", "marked", "silenced", "stable"}


def test_spell_data_covers_twelve_of_the_fifteen_conditions(loader):
    """Заклинания накладывают 12 условий из 15.

    Три оставшихся — `exhaustion`, `grappled`, `petrified` — заклинаниями не
    накладываются: они приходят от других источников (исчерпание — от правил
    отдыха и голода, захват — от действия Grapple, окаменение — от эффектов
    существ). Их механика живёт в `dnd5e_engine/rules/conditions.py`, а не в
    данных заклинаний, поэтому здесь проверяется только достижимое.
    """
    known = {c.value for c in Condition}
    used = set()
    for slug in loader.list_slugs("spells"):
        used |= _statuses(_raw(loader, slug)) & known
    assert sorted(known - used) == ["exhaustion", "grappled", "petrified"]
    assert len(used) == 12


#: Заклинания со спасброском, у которых нет НИ урона, НИ состояний, НИ изменений.
#: Последствия описаны только текстом — движку нечего исполнять.
SAVE_WITHOUT_CONSEQUENCE = (
    "animal-messenger", "color-spray", "command", "confusion", "counterspell",
    "detect-thoughts", "dispel-evil-and-good", "faerie-fire", "grease",
    "gust-of-wind", "levitate", "magic-circle", "planar-binding", "polymorph",
    "resilient-sphere", "reverse-gravity", "scrying", "seeming", "sleet-storm",
    "telekinesis", "true-polymorph", "zone-of-truth",
)


def test_the_spells_without_consequences_are_named(loader):
    """Пробел данных назван поимённо, а не спрятан за «редким случаем»."""
    offenders = []
    for slug in loader.list_slugs("spells"):
        raw = _raw(loader, slug)
        activities = raw.get("activities") or []
        if not activities or not activities[0].get("save"):
            continue
        effects = raw.get("passive_effects") or []
        has_damage = any((a.get("damage") or {}).get("parts") for a in activities)
        has_change = any(e.get("changes") for e in effects)
        if not has_damage and not has_change and not _statuses(raw):
            offenders.append(slug)
    assert sorted(offenders) == list(SAVE_WITHOUT_CONSEQUENCE)


def test_the_gap_is_not_a_hole_in_the_mechanism(loader):
    """Рядом стоят заклинания той же формы, где последствия ЕСТЬ.

    Разница только в данных: `hold-person` (спасбросок + `paralyzed`) против
    `polymorph` (спасбросок, без последствий). Обработчик способен накладывать
    состояние — не хватает записи.
    """
    hold = _raw(loader, "hold-person")
    poly = _raw(loader, "polymorph")
    assert (hold.get("activities") or [])[0].get("kind") == "save"
    assert (poly.get("activities") or [])[0].get("kind") == "save"
    assert _statuses(hold) == {"paralyzed"}
    assert _statuses(poly) == set()

