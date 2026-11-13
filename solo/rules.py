"""Поддерживаемая часть SRD 5.2.1: проверки характеристик (стр. 6–8)."""
import secrets
from .contracts import Hero, Step

SOURCE = 'SRD 5.2.1, стр. 6–8; https://www.dndbeyond.com/srd'
ABILITIES = dict(strength='Сила', dexterity='Ловкость', constitution='Телосложение', intelligence='Интеллект', wisdom='Мудрость', charisma='Харизма')

def modifier(score):
    return (score-10)//2

def proficiency(level):
    return 2+(level-1)//4

def check(hero: Hero, step: Step, rng=None,*,preset=None):
    if step.ability is None or step.dc is None:
        raise ValueError('Для проверки выберите характеристику и Сл. Это временное решение игрока/Мастера.')
    from .rulesets import RulesetEngine,BUILTINS
    result=RulesetEngine(preset or BUILTINS['dnd2024']).check(hero,step,rng)
    if preset is None:result['source']=SOURCE
    return result

def validate_plan(plan):
    if plan.unclear:
        raise ValueError('Сначала исправьте неясные детали плана: '+'; '.join(plan.unclear))
    for s in plan.steps:
        if s.disposition != 'now':
            continue
        if s.kind in ('attack','use','other'):
            raise ValueError('Механика этого действия в проходе 1 не поддерживается: '+s.text+'. Исправьте план; нельзя подменять результат выдумкой модели.')
        if s.kind == 'check' and (s.ability is None or s.dc is None):
            raise ValueError('Укажите характеристику и Сл для проверки в редакторе плана.')

def resolve(state, plan, rng=None):
    validate_plan(plan)
    hero = Hero.model_validate(state['hero'])
    results = []
    for step in plan.steps:
        if step.disposition != 'now':
            results.append(dict(type='deferred',disposition=step.disposition,text=step.text,condition=step.condition,
                                note='Не выполнено. Для исполнения в будущем нужно новое действие и подтверждение.'))
        elif step.kind == 'check':
            from .rulesets import active
            results.append(check(hero,step,rng,preset=active(state)))
        else:
            results.append(dict(type=step.kind,text=step.text,target=step.target,
                                note='Объявленное действие без расчёта проверки; не гарантирует успех спорного взаимодействия.'))
    state['turn'] += 1
    return results
