"""Rules library and explicit program commands; no model calls."""
from typing import Literal
from fastapi import Request
from pydantic import Field
from .contracts import Strict,Hero
from .rulesets import Ruleset,RulesetEngine,RulesetRepository,active,digest

class ConversionPreview(Strict):
    ruleset: str = Field(min_length=1,max_length=80)
    version: int | None = Field(default=None,ge=1)
class ConversionApproval(Strict):
    approval: str = Field(min_length=1,max_length=80)
    confirm: bool
class HeroUpdate(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    hero: Hero
    expected_version: int | None = Field(default=None,ge=0)
class RestRequest(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    kind: Literal['short','long']
    hit_dice: int = Field(default=0,ge=0,le=20)
    food_water: bool = False

def apply_rest(store,campaign,request_id,kind,hit_dice=0,food_water=False,*,text=None,rng=None):
    command=dict(kind=kind,hit_dice=hit_dice,food_water=food_water)
    with store.connection(True) as c:
        current=store.campaign(campaign,c)
        from .first_playable import require_full_scene
        require_full_scene(current['state'])
        return store.engine_event(c,current,request_id,text or ('Короткий отдых' if kind=='short' else 'Длительный отдых'),
            lambda state:[RulesetEngine(active(state)).rest(state,kind,hit_dice=hit_dice,food_water=food_water,rng=rng)],digest(command))

def register_rules_api(app,store,game):
    library=RulesetRepository(store)
    @app.get('/api/rules/coverage')
    def rule_coverage(edition:str | None=None):
        from .rule_registry import coverage
        return dict(rules=coverage(edition),candidates_can_execute=False)
    @app.get('/api/rulesets')
    def list_rulesets():return library.list()
    @app.post('/api/rulesets')
    async def save_ruleset(body:Ruleset):
        async with game.operation:return library.save(body).model_dump()
    @app.get('/api/rulesets/{ruleset}/history')
    def versions(ruleset:str):return library.history(ruleset)
    @app.get('/api/campaigns/{campaign}/character')
    def sheet(campaign:str):
        current=store.campaign(campaign)
        if 'nat20' in current['state']:
            from .playable_ui import native_character
            return native_character(current)
        result={**RulesetEngine(active(current['state'])).sheet(Hero.model_validate(current['state']['hero'])),'version':current['version']}
        if current['state'].get('character_creation'):
            from .character_catalog import FEATURES,FIGHTING_STYLES
            features=[]
            for key in result['hero']['features']:
                row=FEATURES.get(key)
                if row:features.append(dict(id=key,**row))
                elif key.startswith('fighting_style_'):features.append(dict(id=key,name='Боевой стиль: '+FIGHTING_STYLES.get(key.removeprefix('fighting_style_'),key),status='implemented' if key=='fighting_style_defense' else 'recorded'))
                else:features.append(dict(id=key,name=key,status='recorded'))
            result['creation']=dict(features=features,source=current['state']['character_creation']['source'])
            result['resource_names']={'spell_slots_1':'Ячейки заклинаний 1 круга','second_wind':'Второе дыхание','magic_initiate_cleric':'Посвящение в магию жреца','magic_initiate_wizard':'Посвящение в магию волшебника'}
        return result
    @app.put('/api/campaigns/{campaign}/character')
    async def update_hero(campaign:str,body:HeroUpdate,request:Request):
        from .tabletop_api import require_advanced
        require_advanced(request)
        async with game.operation:
            def transition(state):
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Лист или сцена изменились; откройте лист заново')
                if state.get('combat',{}).get('active'):raise ValueError('Завершите бой до ручного редактирования героя')
                if state.get('hero_death'):raise ValueError('Необратимый исход не отменяется редактированием листа; используйте откат или контрольную точку')
                effects=RulesetEngine(active(state)).exhaustion_effects(body.hero)
                if effects['mechanical_death']:raise ValueError('Смертельное истощение требует отдельного решения о последствиях')
                if body.hero.hp>effects['effective_max_hp']:raise ValueError('Текущие хиты превышают максимум с учётом истощения')
                from .inventory import InventoryEngine
                inventory=InventoryEngine(state) if state.get('economy') else None
                if inventory and (inventory.data.equipment.get('armor') or inventory.data.equipment.get('shield')) and body.hero.ac!=state['hero']['ac']:
                    raise ValueError('Сначала снимите броню/щит, чтобы изменить базовую КБ вручную')
                if inventory:
                    from .inventory import CATALOG
                    for slot in ('armor','shield'):
                        id=inventory.data.equipment.get(slot)
                        if id and CATALOG[inventory.item(id).catalog]['parameters']['armor'] not in body.hero.armor_proficiencies:
                            raise ValueError('Сначала снимите снаряжение, которым новый лист не владеет')
                state['hero']=body.hero.model_dump()
                if inventory:
                    if not inventory.data.equipment.get('armor') and not inventory.data.equipment.get('shield'):inventory.data.base_ac=body.hero.ac
                    inventory.recalculate_ac();inventory.flush()
                return [dict(type='scripted',kind='hero_edit',text='Лист героя обновлён вручную игроком. Названия способностей не дают автоматических эффектов.')]
            with store.connection(True) as c:
                current=store.campaign(campaign,c)
                event=store.engine_event(c,current,body.request_id,'Редактирование листа героя',transition,digest(body.model_dump(exclude={'request_id'})))
            return game.public_event(event)
    @app.post('/api/campaigns/{campaign}/ruleset/preview')
    async def conversion_preview(campaign:str,body:ConversionPreview):
        async with game.operation:return library.preview(campaign,body.ruleset,body.version)
    @app.post('/api/campaigns/{campaign}/ruleset/apply')
    async def conversion_apply(campaign:str,body:ConversionApproval):
        async with game.operation:
            result=library.apply(campaign,body.approval,body.confirm)
            return dict(event=game.public_event(result['event']),checkpoint=result['checkpoint'])
    @app.post('/api/campaigns/{campaign}/rest')
    async def rest(campaign:str,body:RestRequest):
        async with game.operation:return game.public_event(apply_rest(store,campaign,body.request_id,body.kind,body.hit_dice,body.food_water))
