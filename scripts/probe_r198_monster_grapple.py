"""r198: почему существо не может схватить героя — замер движка, не догадка.

Три факта, каждый печатается:

1. Корпус: у действий монстров, где в тексте есть «Grappled», НЕТ ни одной
   типизированной пометки состояния — только ссылка на эффект Foundry
   (`effects: [{"id": …}]`), которого в корпусе нет.
2. Селектор: `select_typed_monster_action` берёт мультиатаку, иначе первую
   активность с атакой/спасброском, поэтому боевой захват (Quick Grapple,
   Constrict) не выбирается никогда.
3. Даже если заставить движок выбрать захват (подменённый лоадер: у монстра
   остаётся ровно это действие), спасбросок бросается, а состояние
   `grappled` НЕ появляется: применять нечего.

Только чтение движка, бой в памяти, ни одного сетевого вызова.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dnd5e_engine import (AbilityScores, CharacterBuildSpec, CombatInstance,
                          EncounterMemberSpec, GridScene, build_party_member,
                          configure_lib_loader, start_combat, advance_monster_turn,
                          drain_pending_events)
from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities.monster_actions import (expand_action_to_parts,
                                                     select_typed_monster_action)
from dnd5e_srd_data.loader import BundledAssetLoader

CORPUS = ROOT / 'vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/monsters'
SCORES = AbilityScores(strength=16, dexterity=12, constitution=14,
                       intelligence=10, wisdom=10, charisma=8)

loader = BundledAssetLoader()


def typed_grapple_actions():
    """Действия монстров, у которых захват есть в ТЕКСТЕ, и что у них в данных."""
    rows = []
    for path in sorted(CORPUS.glob('*.json')):
        monster = json.loads(path.read_text(encoding='utf-8'))
        for action in monster.get('actions') or []:
            text = json.dumps(action, ensure_ascii=False).lower()
            if 'grappl' not in text:
                continue
            statuses = sorted({status for activity in action.get('activities') or []
                               for effect in activity.get('effects') or []
                               for status in (effect.get('statuses') or [])})
            rows.append(dict(monster=path.stem, action=action.get('name'),
                             activity_kinds=[a.get('kind') for a in action.get('activities') or []],
                             typed_statuses=statuses,
                             effect_ids=[effect.get('id') for activity in action.get('activities') or []
                                         for effect in activity.get('effects') or []]))
    return rows


def selector(slug):
    monster = loader.get_monster(slug)
    chosen = select_typed_monster_action(monster)
    parts = expand_action_to_parts(monster, chosen) if chosen is not None else []
    return dict(slug=slug, chosen=chosen.slug if chosen else None,
                resolved_kinds=[kind for _, activity in parts for kind in [getattr(activity, 'kind', '?')]])


class OnlyAction:
    """Лоадер, у которого у монстра осталось РОВНО одно действие — замер №3."""

    def __init__(self, inner, slug, keep):
        self.inner, self.slug, self.keep = inner, slug, keep

    def get_monster(self, slug):
        monster = self.inner.get_monster(slug)
        if slug != self.slug:
            return monster
        actions = [action for action in monster.actions if action.name == self.keep]
        return monster.model_copy(update={'actions': actions})

    def __getattr__(self, name):
        return getattr(self.inner, name)


def forced_turn(slug, action_name, seed=5):
    """Бой, где у монстра есть только «захватное» действие: что придёт в событиях."""
    configure_lib_loader(OnlyAction(loader, slug, action_name))
    try:
        spec = CharacterBuildSpec(species_slug='human', class_slug='fighter', background_slug='soldier',
                                  level=1, ability_scores=SCORES, selected_choices=('archery', 'skilled'))
        pc = build_party_member(spec, CombatInstance(entity_id='pc1', name='Fighter', zone_id='0,0',
                                                     initiative=20, hp_max=40, hp_current=40),
                                loader=BundledAssetLoader())
        handle = asyncio.run(start_combat(
            session_id='r198', party=[pc],
            encounter=[EncounterMemberSpec(entity_id='m1', name=slug, entity_type='Monster',
                                           initiative=1, hp_current=40, hp_max=40, ac=13,
                                           zone_id='1,0', monster_template_slug=slug, base_speed=30)],
            grid_scene=GridScene(width=8, height=5), rng_seed=seed)).handle
        asyncio.run(O.submit_player_intent(handle, 'pc1', O.PlayerIntent(intent_type='pass')))
        for _ in range(3):
            live = O._get_live(handle)
            if live.ended or live.initiative[live.current_turn_index].entity_id != 'm1':
                break
            asyncio.run(advance_monster_turn(handle))
        events = [event.type + (':' + getattr(event, 'condition', '')
                                if event.type.startswith('condition') else '')
                  for event in O.drain_pending_events(handle)]
        live = O._get_live(handle)
        row = next(c for c in live.initiative if c.entity_id == 'pc1')
        return dict(conditions=[ac.condition for ac in row.conditions], events=events)
    finally:
        configure_lib_loader(None)


def main():
    os.environ.setdefault('SOLO_TEST', '1')
    rows = typed_grapple_actions()
    print('1. корпус: действий с «Grappled» в тексте —', len(rows),
          '| с типизированным состоянием —', sum(1 for row in rows if row['typed_statuses']))
    for row in rows[:6]:
        print('  ', json.dumps(row, ensure_ascii=False))
    print('2. селектор:', json.dumps([selector(slug) for slug in
                                      ('constrictor-snake', 'bugbear-stalker', 'mimic')],
                                     ensure_ascii=False))
    print('3. захват, выбранный принудительно:', json.dumps(
        [forced_turn('bugbear-stalker', 'Quick Grapple'),
         forced_turn('constrictor-snake', 'Constrict')], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
