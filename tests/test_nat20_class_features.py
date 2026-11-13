"""Критерий 14.2: обязательные способности имеют работающую механику.

Пункт 7 запрещает считать способность реализованной потому, что «она появилась
в списке». Здесь проверяется то, что можно доказать через публичный путь:
способность ДОСТИГАЕТ рассчитанного листа ровно с того уровня, на котором её
даёт SRD 5.2, и не появляется раньше.

Уровни взяты из таблиц классов SRD 5.2 и проверены расчётом по всем 20 уровням.
"""
import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


BASE = dict(
    species_slug="human",
    ability_score_method="standard_array",
    background_slug="soldier",
    equipment=(),
)
SCORES = dict(strength=15, dexterity=14, constitution=13,
              intelligence=8, wisdom=10, charisma=12)


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _features(loader, cls, level):
    spec = CharacterBuildSpec(class_slug=cls, classes={cls: level}, level=level,
                              ability_scores=SCORES, **BASE)
    raw = derive_sheet(spec, loader=loader).model_dump()["features"]
    return {f if isinstance(f, str) else (f.get("slug") or f.get("name")) for f in raw}


#: (класс, способность, уровень получения по SRD 5.2)
GAINS = [
    ("fighter", "second-wind", 1),
    ("fighter", "action-surge", 2),
    ("fighter", "extra-attack", 5),
    ("fighter", "indomitable", 9),
    ("fighter", "studied-attacks", 13),
    ("fighter", "two-extra-attacks", 11),
    ("fighter", "three-extra-attacks", 20),
    ("wizard", "spellcasting", 1),
    ("wizard", "memorize-spell", 5),
    ("wizard", "spell-mastery", 18),
    ("wizard", "signature-spells", 20),
    ("rogue", "sneak-attack", 1),
    ("rogue", "cunning-action", 2),
    ("rogue", "uncanny-dodge", 5),
    ("rogue", "evasion", 7),
    ("rogue", "slippery-mind", 15),
    ("rogue", "elusive", 18),
    ("rogue", "stroke-of-luck", 20),
    ("barbarian", "rage", 1),
    ("barbarian", "danger-sense", 2),
    ("barbarian", "fast-movement", 5),
    ("barbarian", "feral-instinct", 7),
]


@pytest.mark.parametrize("cls,feature,level", GAINS,
                         ids=[f"{c}-{f}-{lv}" for c, f, lv in GAINS])
def test_a_class_feature_reaches_the_sheet_on_its_level(loader, cls, feature, level):
    assert feature in _features(loader, cls, level), (
        f"{feature} отсутствует на листе {cls} {level} уровня"
    )


@pytest.mark.parametrize("cls,feature,level", GAINS,
                         ids=[f"{c}-{f}-{lv}" for c, f, lv in GAINS])
def test_a_class_feature_is_not_granted_early(loader, cls, feature, level):
    # Самодостаточно: сначала доказываем, что способность ВООБЩЕ есть на своём
    # уровне, иначе проверка «не раньше» проходит и на полностью удалённой
    # способности (находка независимого критика).
    assert feature in _features(loader, cls, level), (
        f"{feature} отсутствует на своём {level} уровне — проверять 'не раньше' нечего"
    )
    if level == 1:
        return  # уровня ниже первого не существует
    assert feature not in _features(loader, cls, level - 1), (
        f"{feature} выдан на {level - 1} уровне, а книга даёт его на {level}"
    )


def test_extra_attacks_scale_exactly_as_the_book_says(loader):
    """Фехтовальщик: 1 атака по умолчанию, +1 на 5-м, +2 на 11-м, +3 на 20-м."""
    def count(level):
        spec = CharacterBuildSpec(class_slug="fighter", classes={"fighter": level},
                                  level=level, ability_scores=SCORES, **BASE)
        return derive_sheet(spec, loader=loader).extra_attack_count

    assert [count(lv) for lv in (1, 4, 5, 10, 11, 19, 20)] == [0, 0, 1, 1, 2, 2, 3]


def test_every_class_keeps_gaining_features_up_to_twenty(loader):
    """Ни один класс не «замирает» раньше максимального уровня."""
    thin = []
    for cls in loader.list_slugs("classes"):
        first = _features(loader, cls, 1)
        last = _features(loader, cls, 20)
        gained = len(last) - len(first)
        if gained < 3:
            thin.append(f"{cls}: +{gained}")
    assert not thin, f"класс почти не растёт: {thin}"


def test_the_feature_set_only_grows(loader):
    """Способность, выданная однажды, не исчезает на следующем уровне."""
    for cls in loader.list_slugs("classes"):
        previous = set()
        for level in range(1, 21):
            current = _features(loader, cls, level)
            missing = previous - current
            assert not missing, f"{cls} уровень {level}: пропали {missing}"
            previous = current

