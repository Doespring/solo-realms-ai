"""Требование 6: длительность и снятие эффектов концентрации (§7 — через
публичный интерфейс, а не прямым вызовом).

SRD 5.2: Bless — концентрация до 1 минуты = 10 раундов; концентрация отсчитывается
в конце хода ЗАКЛИНАТЕЛЯ, поэтому ход чужого существа счётчик не двигает.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=17, wisdom=10, charisma=8,
)


def _bless_fight():
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=5, ability_scores=SCORES,
    )
    wizard = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Wizard", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="duration",
            party=[wizard],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="cast_spell", spell_id="bless", target_id="pc1"),
        )
    )
    O.drain_pending_events(handle)
    return handle


def _bless_ids(handle):
    return [
        effect.id
        for group in O._get_live(handle).active_effects.values()
        for effect in group
        if effect.name.lower().startswith("bless")
    ]


def test_bless_starts_a_ten_round_concentration():
    handle = _bless_fight()
    remaining = O._get_live(handle).concentration_rounds_remaining
    assert remaining == {"pc1": 10}, "концентрация не 10 раундов: " + str(remaining)
    assert _bless_ids(handle), "эффект Bless отсутствует в active_effects"


def test_only_the_casters_turn_advances_the_clock():
    handle = _bless_fight()
    first = O._get_live(handle).current_actor_id
    asyncio.run(O.submit_player_intent(handle, first, O.PlayerIntent(intent_type="pass")))
    after = dict(O._get_live(handle).concentration_rounds_remaining)
    assert after == {"pc1": 9}, "после хода " + str(first) + " счётчик " + str(after)


def test_the_effect_survives_until_the_last_round_then_expires():
    """Снятие привязано к длительности: эффект жив на 9 раундах, снят на 10-м."""
    handle = _bless_fight()
    assert _bless_ids(handle), "нечего снимать"
    alive_during = []
    dropped_at_round = None
    expired = False
    for _ in range(40):
        live = O._get_live(handle)
        if live.ended:
            break
        try:
            asyncio.run(
                O.submit_player_intent(
                    handle, live.current_actor_id, O.PlayerIntent(intent_type="pass"),
                )
            )
        except Exception:
            pass
        kinds = set(event.type for event in O.drain_pending_events(handle))
        live = O._get_live(handle)
        remaining = live.concentration_rounds_remaining.get("pc1")
        if remaining is not None and remaining > 1:
            alive_during.append((remaining, bool(_bless_ids(handle))))
        if "effect_expired" in kinds:
            expired = True
        if remaining is None:
            dropped_at_round = live.round_number
            break
    assert alive_during, "не удалось отследить промежуточные раунды"
    for remaining, present in alive_during:
        assert present, "эффект исчез раньше срока при " + str(remaining) + " раундах"
    assert dropped_at_round is not None, "концентрация не истекла за 40 ходов"
    assert expired, "не было события effect_expired"
    assert not _bless_ids(handle), "эффект Bless остался после истечения"

