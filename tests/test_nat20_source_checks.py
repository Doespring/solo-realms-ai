"""Проверки из книги: `ability=None`, короткий код характеристики, `WorldLocation.checks`.

Основание — `integration/engine-handoff/CHECKS-FROM-SOURCE-SPEC.md`.

Каждый тест здесь отвечает на дефект, который ломал игру или терял данные:

* §7.3 — проверка характеристики без `ability` роняла ход с `ValueError`
  из середины расчёта; `kind='skill'` без `ability` тоже падал;
* §7.3, найденное исполнением сверх спеки — `kind='ability'` с коротким
  кодом (`SceneCheck.ability == 'wis'`) не ронял ход, а молча считал
  характеристику как 10: модификатор +0 вместо +3. Тихое неверное число
  хуже падения, поэтому оно покрыто отдельно;
* §1.2 — `WorldLocation` строг (`extra='forbid'`), и проверки, положенные
  в словарь в обход модели, молча исчезали при `import_package`;
* §4 — Сл без маркера происхождения: игрок не отличает книжную Сл 14 от
  заглушки Сл 15.

`ability_scores` в движке ключуется **полными** именами
(`derived_sheet.ability_scores == {"strength": 15, ...}`), поэтому фикстура
ниже держит именно их.
"""

from __future__ import annotations

import random

import pytest

from dnd5e_engine import CheckSpec, resolve_check
from dnd5e_engine.rules.skills import SKILL_ABILITIES


SCORES = {
    "strength": 15, "dexterity": 14, "constitution": 13,
    "intelligence": 12, "wisdom": 16, "charisma": 8,
}


def _spec(**kwargs) -> CheckSpec:
    base = dict(
        kind="skill",
        ability_scores=SCORES,
        proficient_skills=("perception",),
        proficient_saves=(),
        proficiency_bonus=2,
        rng=random.Random(7),
    )
    base.update(kwargs)
    return CheckSpec(**base)


# --- Шаг 1: проверка характеристики больше не роняет ход -------------------


def test_a_skill_check_derives_its_ability_instead_of_failing_mid_roll():
    """§7.3(а): навык без явной характеристики получает её из SKILL_ABILITIES."""
    spec = _spec(kind="skill", skill="perception", ability=None, dc=12)

    assert spec.ability == SKILL_ABILITIES["perception"]
    result = resolve_check(spec)

    assert result.ability == SKILL_ABILITIES["perception"]
    assert isinstance(result.roll_total, int)


def test_the_derived_ability_matches_the_modifier_the_roll_used():
    """Wisdom 16 → +3, владение навыком +2: модификатор 5, а не 2."""
    result = resolve_check(_spec(kind="skill", skill="perception", ability=None, dc=12))

    assert result.ability == "wisdom"
    assert result.modifier == 3 + 2


def test_an_ability_check_without_an_ability_is_rejected_when_the_spec_is_built():
    """§7.3(б): ошибка данных поднимается на конструировании, а не посреди хода.

    Раньше `ValueError` прилетал из резолвера, после того как хост уже начал
    ход. Теперь тот же отказ, но на кадре вызывающего.
    """
    with pytest.raises(ValueError, match="requires `ability`"):
        _spec(kind="ability", skill=None, ability=None, dc=12)


def test_a_saving_throw_without_an_ability_is_rejected_the_same_way():
    with pytest.raises(ValueError, match="requires `ability`"):
        _spec(kind="saving_throw", ability=None, dc=12)


def test_a_skill_check_without_a_skill_is_rejected():
    with pytest.raises(ValueError, match="requires `skill`"):
        _spec(kind="skill", skill=None, ability=None, dc=12)


def test_an_ability_check_with_a_full_ability_name_still_rolls():
    result = resolve_check(_spec(kind="ability", skill=None, ability="strength", dc=10))

    assert result.ability == "strength"
    assert result.skill == ""
    assert result.modifier == 2  # Strength 15 → +2


# --- Найдено исполнением: короткий код характеристики ----------------------


def test_the_hosts_short_ability_code_scores_the_same_as_the_full_name():
    """`SceneCheck.ability` — `'wis'`, а лист персонажа — `'wisdom'`.

    До фикса `ability_scores.get('wis', 10)` промахивался и характеристика
    считалась десяткой: модификатор 0 вместо 3. Ошибки не было — было
    неверное число.
    """
    short = resolve_check(_spec(kind="ability", skill=None, ability="wis", dc=10))
    full = resolve_check(_spec(kind="ability", skill=None, ability="wisdom", dc=10))

    assert short.ability == "wisdom"
    assert short.modifier == 3
    assert short.modifier == full.modifier


@pytest.mark.parametrize(
    ("code", "name"), sorted({
        "str": "strength", "dex": "dexterity", "con": "constitution",
        "int": "intelligence", "wis": "wisdom", "cha": "charisma",
    }.items()),
)
def test_every_short_code_normalizes(code, name):
    spec = _spec(kind="ability", skill=None, ability=code, dc=10)

    assert spec.ability == name


def test_a_short_code_on_a_saving_throw_normalizes_too():
    result = resolve_check(_spec(kind="saving_throw", ability="dex", dc=10))

    assert result.ability == "dexterity"


def test_an_unknown_ability_is_rejected_rather_than_scored_as_ten():
    with pytest.raises(ValueError, match="is not a D&D ability"):
        _spec(kind="ability", skill=None, ability="charism", dc=10)


# --- Шаг 2: проверка живёт у места и переживает импорт пакета ---------------

from solo.world import (  # noqa: E402 — секция ниже зависит от моделей мира
    ScenarioPackage,
    WorldLocation,
    WorldNode,
    WorldSettings,
    import_package,
)

BOOK_QUOTE = "Внимательность (Сл 14) замечает кобольда за бочками."
DOCUMENT = "a" * 64  # sha256 формы: SourceQuote принимает только 64 hex


def _book_check(check_id: str = "loc:taproom:perception:1", **overrides) -> dict:
    value = {
        "ability": "wisdom",
        "dc": 14,
        "skill": "perception",
        "origin": "source",
        "source": {"document": DOCUMENT, "page": 11, "quote": BOOK_QUOTE},
        "trigger": "hero searches the crates behind the counter",
        "anchor": {"kind": "location", "id": "taproom"},
    }
    value.update(overrides)
    return value


def _empty_world() -> dict:
    """То, что `import_package` ожидает найти в состоянии кампании."""
    return dict(
        location="Таверна", turn=0, locations=[], facts=[], deferred=[],
        geography=dict(nodes=[], routes=[]),
        world=dict(settings=WorldSettings(mode="story").model_dump()),
    )


@pytest.fixture
def book_package() -> ScenarioPackage:
    place = WorldLocation(
        name="Таверна",
        description="Зал таверны",
        checks={"loc:taproom:perception:1": _book_check()},
    )
    return ScenarioPackage(
        name="Книга",
        source="book.pdf",
        locations=[place],
        nodes=[WorldNode(id="taproom", name="Таверна", coordinates=[1, 1])],
        routes=[],
    )


def test_a_places_checks_survive_import_package(book_package):
    """§1.2, ловушка 2: то, чего нет в модели, `model_dump()` выбрасывает молча.

    Именно так проверки и терялись: `WorldLocation` строг, а `import_package`
    пишет `place.model_dump()`.
    """
    world = _empty_world()

    import_package(world, book_package)

    stored = world["locations"][0]
    assert "checks" in stored
    assert list(stored["checks"]) == ["loc:taproom:perception:1"]
    assert stored["checks"]["loc:taproom:perception:1"]["dc"] == 14


def test_the_provenance_survives_import_too(book_package):
    world = _empty_world()

    import_package(world, book_package)

    check = world["locations"][0]["checks"]["loc:taproom:perception:1"]
    assert check["origin"] == "source"
    assert check["source"]["page"] == 11
    assert check["source"]["quote"] == BOOK_QUOTE


def test_a_saved_location_without_checks_still_loads():
    """Обратная совместимость: старые сохранения поля не имеют вообще."""
    legacy = {
        "name": "Старый зал", "description": "", "npc": [], "objects": [],
        "interactions": [], "aliases": [], "facts": [], "secrets": [],
        "sealed_text": "", "known_to_hero": True,
    }

    place = WorldLocation.model_validate(legacy)

    assert place.checks == {}
    assert place.model_dump()["checks"] == {}


def test_a_place_without_checks_imports_as_empty_not_missing():
    world = _empty_world()
    package = ScenarioPackage(
        name="Без проверок", source="mod",
        locations=[WorldLocation(name="Пустой зал")],
        nodes=[WorldNode(id="empty", name="Пустой зал", coordinates=[2, 2])],
        routes=[],
    )

    import_package(world, package)

    assert world["locations"][0]["checks"] == {}


def test_a_stored_check_value_is_step_compatible_once_provenance_is_set_aside():
    """`command_engine.py:183` делает `Step(kind='check', **value)`, а `Step` строг.

    Поэтому провенанс обязан лежать рядом с полями `Step`, а не внутри них:
    иначе legacy-ветка падает на лишних ключах.
    """
    from solo.contracts import Step

    value = dict(_book_check())
    for key in ("origin", "source", "trigger", "anchor"):
        value.pop(key)

    step = Step(kind="check", text="loc:taproom:perception:1", **value)

    assert step.dc == 14
    assert step.ability == "wisdom"  # хранится полное имя, `Step` его принимает


# --- Шаг 3: происхождение Сл видно явно ------------------------------------


def test_a_book_check_carries_its_reference():
    check = WorldLocation(
        name="Таверна", checks={"loc:taproom:perception:1": _book_check()}
    ).checks["loc:taproom:perception:1"]

    assert check.origin == "source"
    assert check.source is not None
    assert check.source.page == 11
    assert "Сл 14" in check.source.quote


def test_a_master_check_is_marked_as_master_and_carries_no_book_reference():
    """Умолчание помечено умолчанием: Сл 15-заглушка не притворяется книжной."""
    check = WorldLocation(
        name="Утёс",
        checks={"loc:cliff:ability:dex:1": {"ability": "dexterity", "dc": 13, "origin": "master"}},
    ).checks["loc:cliff:ability:dex:1"]

    assert check.origin == "master"
    assert check.source is None
    assert check.dc == 13


def test_a_check_claiming_the_book_without_a_quote_is_refused():
    value = _book_check()
    value.pop("source")

    with pytest.raises(Exception, match="source"):
        WorldLocation(name="X", checks={"loc:x:ability:wis:1": value})


def test_a_master_check_carrying_a_quote_is_refused():
    """Маркер должен быть однозначным в обе стороны."""
    with pytest.raises(Exception, match="master"):
        WorldLocation(
            name="X",
            checks={
                "loc:x:ability:wis:1": {
                    "ability": "wisdom", "dc": 14, "origin": "master",
                    "source": {"document": DOCUMENT, "page": 1, "quote": "Сл 14"},
                }
            },
        )


def test_a_book_check_must_state_its_dc():
    value = _book_check()
    value.pop("dc")

    with pytest.raises(Exception, match="dc"):
        WorldLocation(name="X", checks={"loc:x:perception:1": value})


# --- Форма хранения и перевод в реестр Nat20 -------------------------------


def test_a_stored_check_can_be_splatted_into_a_step():
    """`command_engine.py:183` делает это с любым значением из `checks`."""
    from solo.contracts import Step
    from solo.source_checks import SourceCheck, step_fields

    stored = SourceCheck(
        skill="perception", ability="wisdom", dc=14, origin="source",
        source={"document": DOCUMENT, "page": 11, "quote": BOOK_QUOTE},
    )

    step = Step(kind="check", text="loc:taproom:perception:1", **step_fields(stored))

    assert step.ability == "wisdom"
    assert step.dc == 14
    assert step.skill == "perception"


def test_the_short_ability_code_is_normalized_for_storage():
    """Хост говорит `'wis'`; хранить надо `'wisdom'`, иначе `Step` откажет."""
    from solo.source_checks import SourceCheck

    stored = SourceCheck(ability="wis", dc=13, origin="master")

    assert stored.ability == "wisdom"


def test_an_unknown_ability_is_refused_instead_of_guessed():
    from solo.source_checks import SourceCheck

    with pytest.raises(Exception, match="Неизвестная характеристика"):
        SourceCheck(ability="charism", dc=13, origin="master")


def test_a_book_label_shows_the_page_and_a_master_label_says_master():
    """§4: игрок обязан отличать книжную Сл от мастерской."""
    from solo.source_checks import SourceCheck, label

    printed = SourceCheck(
        skill="perception", ability="wisdom", dc=14, origin="source",
        source={"document": DOCUMENT, "page": 11, "quote": BOOK_QUOTE},
    )
    master = SourceCheck(ability="dexterity", dc=15, origin="master")

    assert "стр. 11" in label(printed, book="Книга")
    assert "Сл 14" in label(printed)
    # Заглушка Сл 15 не выдаёт себя за книгу.
    assert "мастер" in label(master)
    assert "книга" not in label(master)


def test_a_book_check_becomes_a_registry_entry_with_a_source_campaign_identity():
    """§4 (уровень сверки): id начинается с `source:` для книжной проверки."""
    from solo.source_checks import SourceCheck, to_prototype_check

    entry = to_prototype_check("loc:taproom:perception:1", SourceCheck(
        skill="perception", ability="wisdom", dc=14, origin="source",
        source={"document": DOCUMENT, "page": 11, "quote": BOOK_QUOTE},
    ))

    assert entry["kind"] == "skill"
    assert entry["ability"] is None  # PrototypeCheck требует ровно одно
    assert entry["source"]["id"].startswith("source:")
    assert "11" in entry["source"]["version"]


def test_a_master_check_becomes_a_master_registry_entry():
    from solo.source_checks import SourceCheck, to_prototype_check

    entry = to_prototype_check("loc:cliff:ability:dex:1",
                               SourceCheck(ability="dex", dc=13, origin="master"))

    assert entry["kind"] == "ability"
    assert entry["source"]["id"] == "master"


def test_every_retrieved_check_reads_the_way_the_engine_stores_it(book_package):
    """Сквозной: пакет → state → значение из state читается движком."""
    from solo.contracts import Step
    from solo.source_checks import step_fields

    world = _empty_world()
    import_package(world, book_package)
    stored = world["locations"][0]["checks"]["loc:taproom:perception:1"]

    step = Step(kind="check", text="loc:taproom:perception:1", **step_fields(stored))

    assert step.dc == 14 and step.ability == "wisdom"


# --- Шаг 4: проверки доходят до кампании ----------------------------------


def test_a_checks_bearing_place_survives_the_launch_replacement():
    """`preparation.py:264` пересобирает стартовую локацию через `model_dump()`.

    Именно там проверки терялись бы, если бы жили вне модели: `WorldLocation`
    строг, и незадекларированный ключ срезается при круглом проходе.
    """
    from solo.world import WorldLocation

    place = WorldLocation(name="Таверна", checks={"loc:taproom:perception:1": _book_check()})
    rebuilt = WorldLocation.model_validate(
        {**place.model_dump(), "description": "opening", "known_to_hero": True}
    )

    assert list(rebuilt.checks) == ["loc:taproom:perception:1"]
    assert rebuilt.model_dump()["checks"]["loc:taproom:perception:1"]["dc"] == 14


def test_the_source_is_started_once_and_its_places_are_stored_with_checks():
    """Сквозной минимум §3.1: текст источника → пакет → импорт в состояние."""
    from solo.preparation import text_structure

    package, opening = text_structure(
        "Старт: Таверна\nВы входите в зал.\n\nЛокация: Склад\nМешки и бочки.\n",
        "book.pdf",
    )
    assert package is not None and opening["name"] == "Таверна"

    # Проверки печатает источник, а не эта разметка: подкладываем ту, что
    # пришла бы из извлечения, и проверяем, что импорт её не съест.
    for place in package.locations:
        if place.name == "Таверна":
            place.checks = {"loc:taproom:perception:1": _book_check()}

    world = _empty_world()
    world["locations"] = [WorldLocation(
        name="Таверна",
        description=opening["text"],
        known_to_hero=True,
        checks={"loc:taproom:perception:1": _book_check()},
    ).model_dump()]
    remaining = package.model_copy(deep=True)
    remaining.locations = [p for p in remaining.locations if p.name != "Таверна"]
    import_package(world, remaining)

    stored = next(p for p in world["locations"] if p["name"] == "Таверна")
    assert stored["checks"]["loc:taproom:perception:1"]["origin"] == "source"
    assert stored["checks"]["loc:taproom:perception:1"]["source"]["page"] == 11
