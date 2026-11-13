"""Требование 6: укрытие (SRD 5.2 §Cover).

Полуукрытие даёт +2 к КД против атаки; три четверти — +5; полное укрытие
делает цель недосягаемой для прямой атаки.

Проверяется не наличие поля `cover`, а изменение ИСХОДА: один и тот же бросок
попадает без укрытия и промахивается по цели в полуукрытии.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)

# seed=3 даёт итог броска атаки ровно 14 — попадание по AC 14.
SEED = 3
ATTACK_TOTAL = 14


def _attack(target_ac, blocker):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES, selected_choices=("archery", "skilled"),
    )
    archer = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Archer", zone_id="0,0", initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )
    foes = [
        EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=100, hp_max=100, ac=target_ac, zone_id="4,0",
        )
    ]
    if blocker:
        # Живое существо на линии огня даёт полуукрытие (+2 КД).
        foes.append(
            EncounterMemberSpec(
                entity_id="m2", name="Blocker", entity_type="Monster",
                initiative=1, hp_current=100, hp_max=100, ac=12, zone_id="2,0",
            )
        )
    handle = asyncio.run(
        O.start_combat(
            session_id="cover", party=[archer], encounter=foes,
            grid_scene=GridScene(width=8, height=6), rng_seed=SEED,
        )
    ).handle
    live = O._get_live(handle)
    target = next(c for c in live.initiative if c.entity_id == "m1")
    cover = O._target_cover_map(live, "pc1", [target])["m1"]
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="attack", target_id="m1", weapon_id="longbow"),
        )
    )
    events = O.drain_pending_events(handle)
    rolled = next(e for e in events if e.type == "attack_rolled")
    return cover, rolled


def test_a_creature_on_the_line_grants_half_cover():
    """Существо на линии огня даёт цели полуукрытие, а не «нет укрытия»."""
    cover, _ = _attack(14, blocker=True)
    assert cover == "half", "ожидалось полуукрытие, получено: " + str(cover)


def test_clear_line_gives_no_cover():
    cover, _ = _attack(14, blocker=False)
    assert cover == "none", "пустая линия не должна давать укрытие: " + str(cover)


def test_half_cover_turns_a_hit_into_a_miss():
    """Тот же бросок: по AC 14 попадание, по AC 14 в полуукрытии (AC 16) — промах."""
    cover, hit = _attack(14, blocker=False)
    assert cover == "none"
    assert hit.roll_total == ATTACK_TOTAL, (
        "опыт опирается на бросок " + str(ATTACK_TOTAL) + ", получен " + str(hit.roll_total)
    )
    assert hit.is_hit is True, "бросок 14 обязан попасть по AC 14"

    cover, miss = _attack(14, blocker=True)
    assert cover == "half"
    assert miss.roll_total == hit.roll_total, "броски должны совпадать для чистоты опыта"
    assert miss.is_hit is False, (
        "полуукрытие (+2 КД) должно превратить попадание в промах"
    )

