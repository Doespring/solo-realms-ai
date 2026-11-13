"""PHB 2024 Influence / Study / Search outside combat: what the host may show.

The roll is the engine's own (nat20_worker -> resolve_state_check ->
dnd5e_engine.resolve_check); this module never computes one. It only decides
which facts the scene already holds, what one Study may reveal, and who is
being influenced: r179 makes the NPC's attitude a state the check moves
(`solo/social.py`), so a success is visible in the campaign, not only in prose.
What stays unstated: agreement, motives, and any clue the scene does not carry.

Contract of the project: предложенное != совершённое. Only what the host can
show from the scene is written; everything else is the Master's separate,
confirmed event.
"""
from .nat20_content import SCENE_ACTIONS, SKILL_NAMES
from . import social

STUDY = "study"
INFLUENCE = "influence"


def _place(state):
    return next((place for place in state.get("locations") or []
                 if place.get("name") == state.get("location")), None)


def _revealed(state):
    scene = state.get("scene_actions")
    return [fact for fact in (scene or {}).get("revealed") or []] if isinstance(scene, dict) else []


def study_facts(state):
    """Facts the scene already holds and the host has not shown yet.

    Only text the world carries itself: the current place's own facts plus the
    campaign's confirmed facts. Nothing is derived, generated or completed.
    """
    shown = set(_revealed(state))
    place = _place(state)
    facts = list(place.get("facts") or []) if place else []
    facts += list(state.get("facts") or [])
    return [fact for fact in dict.fromkeys(facts) if fact not in shown]


def guard(state, action):
    """Refuse before the roll what could not be shown after it."""
    if getattr(action, "kind", None) != "scene_action":
        return
    if action.action_id == STUDY and not study_facts(state):
        raise ValueError("Здесь нечего изучать: в сцене нет ни одного непоказанного факта.")
    if action.action_id == INFLUENCE:
        # r179: кого именно убеждают — факт сцены, а не догадка модели, и
        # кость не бросается, если реакцию НИПа Мастер уже назвал (стр. 16).
        npc = social.select(state, getattr(action, "npc_id", None))
        decision = social.check_needed(npc)
        if not decision["needed"]:
            raise ValueError("Проверка Влияния не нужна: " + decision["reason"] + ".")


def confirmed(state, action, response, event_id):
    """Journal text and memory evidence for one confirmed scene action.

    Returns ``(None, [])`` for every other command, so the caller keeps its own
    wording. A Study success reveals exactly one existing scene fact and records
    it, so the next Study cannot show the same text again.
    """
    if getattr(action, "kind", None) != "scene_action":
        return None, []
    result = response["result"]
    spec = SCENE_ACTIONS[action.action_id]
    outcome = "успех" if result.get("success") else "неудача"
    if result.get("success") is None:
        outcome = "исход не определён: у проверки нет Сл"
    text = "%s (%s) против Сл %s: бросок %s — %s." % (
        spec["noun"], SKILL_NAMES[action.skill], result.get("dc"),
        result.get("roll_total"), outcome)
    memories = []
    if action.action_id == STUDY:
        if not result.get("success"):
            text += " Провал не открывает факт сцены."
        else:
            facts = study_facts(state)
            if not facts:
                text += " Сцена не содержит непоказанного факта: выдуманного знания не будет."
            else:
                scene = state.get("scene_actions")
                if not isinstance(scene, dict):
                    scene = state["scene_actions"] = dict(version=1, revealed=[])
                scene["revealed"].append(facts[0])
                text += " Факт сцены: «%s»." % facts[0]
                memories.append(dict(text=facts[0], known_by=["hero"], scope="private",
                                     provenance="scenario", truth="fact", kind="event",
                                     importance=7,
                                     source_ref="scene_actions.study;events:" + event_id))
    elif action.action_id == INFLUENCE:
        # r179: успех Влияния меняет отношение НИПа — состояние, которое
        # игрок проверяет первым. Книга печатает, что действие Влияние
        # «меняет отношение существа» (стр. 15) и что дружелюбие даёт
        # преимущество (стр. 18); шаг на одну ступень — правило хоста.
        npc = social.select(state, getattr(action, "npc_id", None))
        change = social.attitude_change(npc, result.get("success"))
        if result.get("dc_source") == "registry":
            text += (" Сл — значение хоста: книгу Сл Влияния оставляет "
                     "Мастеру (стр. 16), напечатанного числа нет.")
        text += " " + social.describe(npc.get("name", "?"), change)
        if change["changed"]:
            memories.append(dict(
                text="Отношение НИПа «%s»: %s → %s (%s, %s, Сл %s, бросок %s)." % (
                    npc.get("name", "?"), social.LABELS[change["before"]],
                    social.LABELS[change["after"]], spec["noun"],
                    SKILL_NAMES[action.skill], result.get("dc"), result.get("roll_total")),
                known_by=["hero"] + [npc["id"]] if npc.get("id") else ["hero"],
                scope="private", provenance="rules", truth="fact",
                kind="relationship", importance=6,
                subjects=[npc["id"]] if npc.get("id") else [],
                source_ref="scene_actions.influence;events:" + event_id))
    else:
        text += (" Проверка записана; находка не назначается без "
                 "зарегистрированного объекта сцены.")
    return text, memories
