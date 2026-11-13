"""Alert — Initiative Swap (SRD 5.2, черта Alert).

«Immediately after you roll Initiative, you can swap your Initiative with the
Initiative of one willing ally in the same combat. You can't make this swap if
you or the ally has the Incapacitated condition.»

Проверки идут через публичный `start_combat` + `swap_initiative` и смотрят на
порядок инициативы, а не на флаг успеха.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=10, dexterity=14, constitution=12,
    intelligence=10, wisdom=10, charisma=10,
)


def _hero(entity_id: str, feats: tuple, initiative: int) -> object:
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=4, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id=entity_id, name=entity_id, zone_id='0,0',
            initiative=initiative, hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    return member.model_copy(update={'feats': feats})


def _fight(*, swapper_feats: tuple = ('alert',), active_effects: tuple = ()) -> object:
    """Бой: pc1 (20), монстр (10), pc2 (5)."""
    # Состояние задаётся так, как это делает настоящий бой: через активные
    # эффекты на старте. У `PartyMemberSpec` начальных состояний нет —
    # `model_copy(update={'conditions': ...})` молча принимает поле,
    # которого у модели нет, и до бойца ничего не доходит.
    ally = _hero('pc2', (), 5)
    return asyncio.run(
        O.start_combat(
            session_id="alert-swap",
            party=[_hero('pc1', swapper_feats, 20), ally],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=10, hp_current=9, hp_max=9, ac=10, zone_id='0,2',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=1,
            active_effects=list(active_effects),
        )
    ).handle


def _order(handle: object) -> list:
    return [c.entity_id for c in O._get_live(handle).initiative]


def test_alert_swaps_initiative_with_a_willing_ally() -> None:
    """Обмен меняет места союзников в порядке инициативы."""
    handle = _fight()
    O.drain_pending_events(handle)
    assert _order(handle) == ['pc1', 'm1', 'pc2']
    result = asyncio.run(O.swap_initiative(handle, 'pc1', 'pc2'))
    assert result.swapped is True, result.rejected_reason
    assert result.initiative_before == {'pc1': 20, 'pc2': 5}
    assert result.initiative_after == {'pc1': 5, 'pc2': 20}
    assert _order(handle) == ['pc2', 'm1', 'pc1']


def test_the_swap_happens_once() -> None:
    """Второй обмен в том же броске отклоняется."""
    handle = _fight()
    O.drain_pending_events(handle)
    assert asyncio.run(O.swap_initiative(handle, 'pc1', 'pc2')).swapped is True
    again = asyncio.run(O.swap_initiative(handle, 'pc1', 'pc2'))
    assert again.swapped is False
    assert again.rejected_reason == 'alert_swap_used'


def test_a_creature_without_alert_cannot_swap() -> None:
    """Без черты обмена нет, и порядок не меняется."""
    handle = _fight(swapper_feats=())
    O.drain_pending_events(handle)
    before = _order(handle)
    result = asyncio.run(O.swap_initiative(handle, 'pc1', 'pc2'))
    assert result.swapped is False
    assert result.rejected_reason == 'alert_swap_unavailable'
    assert _order(handle) == before


def test_a_foe_is_not_a_willing_ally() -> None:
    """Обмен только с союзником-персонажем."""
    handle = _fight()
    O.drain_pending_events(handle)
    result = asyncio.run(O.swap_initiative(handle, 'pc1', 'm1'))
    assert result.rejected_reason == 'not_a_willing_ally'


def test_an_incapacitated_ally_blocks_the_swap() -> None:
    """Книга запрещает обмен, если у любого из двоих Incapacitated."""
    from dnd5e_engine.events import ActiveEffect

    incapacitated = ActiveEffect(
        id='effect:test-incapacitated',
        name='Incapacitated',
        target_id='pc2',
        origin='cast:test',
        statuses=['incapacitated'],
        changes=[],
    )
    handle = _fight(active_effects=(incapacitated,))
    O.drain_pending_events(handle)
    before = _order(handle)
    result = asyncio.run(O.swap_initiative(handle, 'pc1', 'pc2'))
    assert result.swapped is False
    assert result.rejected_reason == 'alert_swap_disabled'
    assert _order(handle) == before
