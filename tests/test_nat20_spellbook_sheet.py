"""Выбранные заклинания доходят до листа и до боя (требования 5 и 7).

Дефект найден отрицательным примером через публичный интерфейс: волшебник
1 уровня, selected_choices = ("skilled", "cantrip:fire-bolt", ...,
"spell:magic-missile", ...), require_complete_choices=True — и пустой
spells_known у героя в бою.

`derive_sheet` квоты проверял и хранил в листе ТОКЕНЫ ("spell:magic-missile"),
а `spell_abilities` наполнялся только Magic Initiate. `build_party_member`
строит `PartyMemberSpec.spells_known` из `sheet.spell_abilities`, поэтому у
заклинающего персонажа `spells_known` был ПУСТОЙ. Дальше два взаимоисключающих
дефекта из одного корня:

* до гейта `spell_not_available` — каст проходил по корпусу без всякого
  ограничения (лист не знал своих заклинаний);
* после гейта — заклинающий не мог кастовать НИЧЕГО, потому что движку не
  сообщали ни одного заклинания.

Квота «3 заговора + 4 подготовленных» проверялась, а сами заклинания в
механике отсутствовали — по п.7 это нереализованность, а не «редкий случай».
Способность заклинаний берётся из документа класса в корпусе
(`spellcasting.ability`), в коде она не задаётся.

Второй дефект того же корня: `build_party_member` ОБЪЕДИНЯЛ явный список
хоста с листом (`build_spec.derive_sheet(spells_known=...)`), хотя для
проверки сборки хост передаёт выборок по одному заклинанию. После первой
правки лист стал непустым, и объединение стало расширять книгу героя:
повышение уровня у `solo/nat20_levelup.py` (он передаёт `prepared_spells`)
добавляло заговоры, которых игрок не выбирал, а мультиклассирующий боец
получал весь список класса. Теперь явный список приоритетен.
"""
import asyncio

from dnd5e_engine import (
    CombatInstance,
    GridScene,
    PlayerIntent,
    build_party_member,
    configure_lib_loader,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_engine.orchestrator import _get_live
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=12, constitution=14,
    intelligence=16, wisdom=10, charisma=10,
)
CANTRIPS = ("cantrip:fire-bolt", "cantrip:light", "cantrip:mage-hand")
PREPARED = (
    "spell:magic-missile", "spell:shield", "spell:detect-magic", "spell:burning-hands",
)
LOADER = BundledAssetLoader()


def _wizard():
    return CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=1, ability_scores=SCORES,
        selected_choices=("skilled", *CANTRIPS, *PREPARED),
        require_complete_choices=True,
    )


def _fight():
    configure_lib_loader(LOADER)
    hero = build_party_member(
        _wizard(),
        CombatInstance(entity_id="hero", name="Hero", zone_id="1,1", initiative=20),
        loader=LOADER,
    )
    foe = EncounterMemberSpec(
        entity_id="foe", entity_type="Monster", name="Foe", initiative=1,
        hp_current=30, hp_max=30, ac=13, attack_bonus=4,
        damage_dice="1d6", damage_type="slashing", zone_id="4,1", base_speed=0,
    )
    handle = asyncio.run(start_combat(
        session_id="spellbook-1", party=[hero], encounter=[foe],
        grid_scene=GridScene(width=6, height=6), rng_seed=7,
    )).handle
    drain_pending_events(handle)
    return handle


def _cast(handle, spell, *, target, slot=1):
    asyncio.run(submit_player_intent(
        handle, "hero",
        PlayerIntent(intent_type="cast_spell", spell_id=spell, target_id=target, slot_level=slot),
    ))
    return [event.type for event in drain_pending_events(handle)]


def test_the_sheet_carries_the_chosen_spells_as_slugs():
    """Лист хранит выбранное по слагам: токен выбора — не имя заклинания."""
    sheet = derive_sheet(_wizard(), loader=LOADER)
    assert sheet.cantrips == ("fire-bolt", "light", "mage-hand"), sheet.cantrips
    assert sheet.prepared_spells == (
        "magic-missile", "shield", "detect-magic", "burning-hands",
    ), sheet.prepared_spells


def test_the_casting_ability_comes_from_the_class_document():
    """`wizard.json`: `spellcasting.ability = "int"` — способность из источника."""
    sheet = derive_sheet(_wizard(), loader=LOADER)
    assert set(sheet.cantrips) | set(sheet.prepared_spells) <= set(sheet.spell_abilities)
    assert set(sheet.spell_abilities.values()) == {"int"}, sheet.spell_abilities


def test_the_hero_enters_combat_knowing_its_spells():
    """`spells_known` героя — не пустой список у заклинающего персонажа."""
    hero = build_party_member(
        _wizard(),
        CombatInstance(entity_id="hero", name="Hero", zone_id="1,1"),
        loader=LOADER,
    )
    assert set(hero.spells_known) == {
        "fire-bolt", "light", "mage-hand",
        "magic-missile", "shield", "detect-magic", "burning-hands",
    }, hero.spells_known


def test_a_chosen_spell_actually_fires():
    """Подготовленное заклинание исполняется: урон и списанный слот."""
    handle = _fight()
    events = _cast(handle, "magic-missile", target="foe")
    assert "spell_cast" in events and "damage_applied" in events, events
    assert _get_live(handle).spell_slots_by_entity["hero"][1] == 1


def test_a_spell_outside_the_sheet_is_refused_by_name():
    """Bless не выбран — отказ, а не каст: слот и действие остаются."""
    handle = _fight()
    before = dict(_get_live(handle).spell_slots_by_entity["hero"])
    events = _cast(handle, "bless", target="hero")
    assert "cast_failed" in events, events
    after = _get_live(handle)
    assert dict(after.spell_slots_by_entity["hero"]) == before
    caster = next(c for c in after.initiative if c.entity_id == "hero")
    assert caster.action_available is True

def test_an_explicit_spell_list_is_not_widened_by_the_build():
    """Хост передал книгу заклинаний — она и есть ответ. Не расширять её.

    `solo/nat20_levelup.py` передаёт `prepared_spells` кампании (и выбор
    Magic Initiate), а правила сборки добавляют свои заклинания. Слияние
    давало герою заговоры, которых он не выбирал, и мультиклассирующий
    персонаж молча получал весь список класса.
    """
    hero = build_party_member(
        _wizard(),
        CombatInstance(
            entity_id="hero", name="Hero", zone_id="1,1",
            spells_known=("magic-missile",),
        ),
        loader=LOADER,
    )
    assert list(hero.spells_known) == ["magic-missile"], hero.spells_known


def test_an_untransmitted_spell_list_falls_back_to_the_build():
    """Явного списка нет (None) — действуют правила сборки, а не пустота."""
    hero = build_party_member(
        _wizard(),
        CombatInstance(entity_id="hero", name="Hero", zone_id="1,1"),
        loader=LOADER,
    )
    assert "magic-missile" in hero.spells_known, hero.spells_known


def test_a_non_caster_with_no_explicit_list_stays_without_spells():
    """Сборка без заклинаний ничего не получает: пустой лист = пустой список."""
    fighter = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES,
        equipment=("longsword", "chain-mail", "shield"),
    )
    hero = build_party_member(
        fighter,
        CombatInstance(entity_id="hero", name="Hero", zone_id="1,1"),
        loader=LOADER,
    )
    assert hero.spells_known == [], hero.spells_known
