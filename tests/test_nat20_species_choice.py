"""Требование 5: вид с обязательным выбором не создаёт лист без выбора и без квоты.

Источник — `canonical/species/goliath.json`, `advancement` типа `ItemChoice`:
```json
{"type": "ItemChoice",
 "configuration": {"choices": {"0": {"count": 1, "replacement": false}},
                   "pool": [6 UUID: clouds-jaunt, fires-burn, frosts-chill,
                            hills-tumble, stones-endurance, storms-thunder],
                   "restriction": {"type": "race"}, "type": "feat"}}
```
То есть Голиаф обязан взять РОВНО ОДНО преимущество предка (PHB 2024,
Giant Ancestry: «Choose one of the following benefits»).

Найденные дефекты (отрицательными примерами):
1. без выбора персонаж создавался молча — обязательный выбор пропущен;
2. выбирались ВСЕ ШЕСТЬ сразу — квота `count: 1` не проверялась.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=12, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)

ALL_SIX = (
    "clouds-jaunt", "fires-burn", "frosts-chill",
    "hills-tumble", "stones-endurance", "storms-thunder",
)


def _goliath(**kwargs):
    return CharacterBuildSpec(
        species_slug="goliath", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES, **kwargs
    )


def _species_feats(sheet):
    base = {"fighting-style", "second-wind", "weapon-mastery",
            "giant-ancestry", "powerful-build"}
    return [str(f) for f in (getattr(sheet, "features", ()) or ()) if str(f) not in base]


def test_goliath_without_the_ancestry_choice_is_refused():
    """Нельзя создать лист, где обязательное преимущество просто отсутствует."""
    # Боевой стиль указан явно: без него отказ пришёл бы от бойца, и тест
    # не доказал бы именно отсутствие преимущества предка.
    with pytest.raises(Exception) as caught:
        derive_sheet(
            _goliath(
                selected_choices=("defense",), require_complete_choices=True
            ),
            loader=BundledAssetLoader(),
        )
    text = str(caught.value).lower()
    assert "goliath" in text or "ancestry" in text or "выбор" in text, text


def test_goliath_takes_exactly_one_ancestry_benefit():
    """`count: 1` — одна черта из шести, и она видна в листе."""
    sheet = derive_sheet(_goliath(selected_choices=("defense", "clouds-jaunt")), loader=BundledAssetLoader())
    chosen = _species_feats(sheet)
    assert chosen == ["clouds-jaunt"], chosen


def test_goliath_cannot_take_every_ancestry_benefit():
    """Квота `count: 1` ограничивает и сверху — шесть черт недопустимы."""
    with pytest.raises(Exception) as caught:
        derive_sheet(_goliath(selected_choices=("defense",) + ALL_SIX), loader=BundledAssetLoader())
    text = str(caught.value)
    assert any(word in text for word in ("too many", "one", "count", "more than")), text


def test_a_species_benefit_outside_the_pool_is_refused():
    """Пул ограничен шестью; посторонняя черта не принимается."""
    with pytest.raises(Exception):
        derive_sheet(
            _goliath(selected_choices=("defense", "clouds-jaunt", "fireball")),
            loader=BundledAssetLoader(),
        )

