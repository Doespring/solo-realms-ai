"""Project pinned monster to-hit through public Native stat-block resolution.

The encounter seam accepts one fixed bonus, not per-activity magnitudes. Only
uniform constant profiles can be represented. Probes use private disposable
actors/events/RNG, never the canonical combat or its random stream.
"""
from functools import lru_cache
import hashlib
import json
import random

from dnd5e_engine.activities.attack import resolve_attack
from dnd5e_engine.activities.build_context import build_activity_context
from dnd5e_engine.activities.conjuration import StatBlockMagnitudes
from dnd5e_engine.types.combat import Combatant
from dnd5e_srd_data.loader import BundledAssetLoader

from .nat20_prototype import DATA


def attack_profile(slug):
    if not slug or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in slug):
        raise ValueError('Invalid monster slug')
    path=DATA/'monsters'/(slug+'.json')
    if not path.is_file():raise ValueError('Unknown canonical monster')
    raw=path.read_bytes()
    return json.loads(_project(slug,hashlib.sha256(raw).hexdigest()))


@lru_cache(maxsize=1024)
def _project(slug,sha):
    loader=BundledAssetLoader(root=DATA)
    rows=[]
    reason=None
    try:
        monster=loader.get_monster(slug)
        for action in [*monster.actions,*monster.legendary_actions,*monster.lair_actions]:
            for activity in action.activities:
                if activity.kind!='attack':continue
                # A scalar encounter bonus cannot represent a random or other
                # nonliteral to-hit expression. No expression from a model is read.
                if activity.attack.bonus.strip():
                    try:int(activity.attack.bonus.strip())
                    except ValueError:
                        raise ValueError('Nonliteral attack bonus cannot be represented by one native encounter scalar')
                actor=Combatant(entity_id='projection',entity_type='Monster',name=monster.name,
                    initiative=1,hp_current=monster.hp,hp_max=monster.hp,attack_bonus=None)
                target=Combatant(entity_id='projection-target',entity_type='Character',name='Projection target',
                    initiative=0,hp_current=1,hp_max=1,ac=1000)
                events=[]
                context=build_activity_context(actor,[target],rng=random.Random(0),event_emitter=events.append,
                    slot_level=None,base_spell_level=None,spellcasting_ability=monster.spellcasting_ability,
                    concentration=False,source_passive_effects=[],spell_book={},passive_damage_modifiers={},save_modifiers={},
                    stat_block_magnitudes=StatBlockMagnitudes(ability_scores=monster.ability_scores.model_dump(),
                        proficiency_bonus=monster.proficiency_bonus))
                weapon=loader.get_weapon(action.slug)
                resolve_attack(activity,context,weapon=weapon)
                rolls=[e for e in events if e.type=='attack_rolled']
                if len(rolls)!=1:raise ValueError('Native projection did not produce exactly one attack modifier')
                rows.append(dict(action=action.slug,activity=activity.id,weapon=weapon.slug if weapon else None,
                                 modifier=rolls[0].modifier))
        values={r['modifier'] for r in rows}
        if len(values)>1:
            raise ValueError('Native encounter scalar cannot represent differing action modifiers: '+str(sorted(values)))
        # No attack activity needs no physical to-hit; native cast wrappers
        # determine spell attacks themselves. Zero is unused, never a guessed attack.
        bonus=next(iter(values)) if values else 0
    except Exception as error:
        # One unusable canonical profile never makes the whole catalogue fail.
        # This resolver is a disposable projection, not an engine transaction.
        reason=str(error)[:600];bonus=None
    return json.dumps(dict(monster=slug,monster_sha256=sha,method='native-stat-block-v1',
        attack_bonus=bonus,status='native_derived' if reason is None else 'unbound',reason=reason,activities=rows),sort_keys=True)


def binding_source(profile):
    from .nat20_campaign import CampaignSource
    return CampaignSource(kind='native_derived',id='nat20-stat-block-v1',version='0.7.0/e187c813',
        note='Pinned canonical SHA256 '+profile['monster_sha256']+
             '; public StatBlockMagnitudes + build_activity_context + resolve_attack; isolated actors/events/RNG; uniform fixed modifier, no Solo D&D formula')
