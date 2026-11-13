"""Объяснение реализованной механики из программного источника, без LLM-арифметики."""
from typing import Literal
from pydantic import Field
from .contracts import Strict,Hero
from . import rules
import re

def local_rule_answer(question,state,knowledge):
    from .rule_knowledge import query_topics,normalize
    from .rulesets import active
    q=normalize(question);keys=query_topics(q)
    if not keys and not re.search(r'правил|модификатор|механик|как работает|по d[&n]d',q):return None
    preset=active(state);reference=knowledge.search(question,'dnd'+preset.edition,budget=1000) if preset.edition in ('2014','2024') else dict(found=False,excerpts=[])
    topic=next((name for key,name in [('advantage','advantage'),('check','ability_check'),('proficiency','proficiency')] if key in keys),None)
    if 'модификатор' in q:topic='modifier'
    if topic:text=render(GmAnswer(topic=topic,answer=''),Hero.model_validate(state['hero']),preset)
    elif reference['found']:text='Справочный текст; он не включает новую механику автоматически.'
    else:return 'В доступных проверенных источниках '+preset.id+' конкретное правило не найдено. Отсутствующее правило не выдумывается; состояние игры не изменено.'
    for row in reference['excerpts']:
        text+='\n\n'+row['text']+'\nИсточник: '+row['source']+', физическая страница '+str(row['page'])+(' (доступный неполный источник)' if row['partial_source'] else '')+'.'
    return text

TOPICS={
    'advantage':'Преимущество, помеха и их отмена; отличие от владения',
    'ability_check':'Проверка характеристики, Сл, натуральные 1/20',
    'modifier':'Модификатор характеристики героя',
    'proficiency':'Бонус владения героя',
    'features':'Свободный ввод, путешествия, сохранения, частичные правила, тактические карты/бой, инвентарь и подтверждённая торговля',
    'unimplemented':'Полные классы/заклинания, импорт книг, полный живой мир, голосовой ввод',
    'other':'Другой внеигровой вопрос',
}

class GmAnswer(Strict):
    topic:Literal['advantage','ability_check','modifier','proficiency','features','unimplemented','other']
    answer:str=Field(max_length=3000)

def render(value:GmAnswer,hero:Hero,preset=None):
    from .rulesets import RulesetEngine,BUILTINS,SOURCES
    preset=preset or BUILTINS['dnd2024'];engine=RulesetEngine(preset)
    if value.topic=='other':
        if not value.answer.strip():raise ValueError('Мастер не ответил на вопрос')
        return value.answer
    guide={
        'advantage':'При преимуществе бросаются два d20 и выбирается больший; при помехе — меньший. Если присутствуют оба, бросается один d20. Модификатор характеристики и применимый бонус владения прибавляются отдельно; владение не даёт преимущество.',
        'ability_check':'Проверка: d20 + модификатор характеристики + бонус владения, если оно применимо. Итог не ниже Сл означает успех. Натуральные 1 и 20 не дают автоматического провала или успеха проверки характеристики. В текстовом игровом цикле характеристику, Сл и владение задаёт сценарий, а не модель. Без заданного испытания результат не выдумывается.',
        'modifier':'Модификатор = floor((значение − 10) / 2). Ваш герой: '+', '.join(f"{rules.ABILITIES[k]} {v} → {rules.modifier(v):+d}" for k,v in hero.abilities.items())+'.',
        'proficiency':f'На уровне {hero.level} бонус владения равен {engine.proficiency(hero.level):+d}. Он прибавляется к проверке при применимом владении. Используется таблица активного набора правил: {preset.name}, версия {preset.version}. Это числовой бонус, а не преимущество.',
        'features':'Пишите слова и действия героя в одном поле. Доступны разговор, осмотр, известные пешие путешествия, проверки, отдых, базовый тактический бой/карта, зарегистрированное снаряжение и торговые предложения. Можно использовать соответствующие панели. Сложное или неподдержанное намерение уточняется. Повтор описания не повторяет броски. Контрольные точки сохраняют состояние и ветвь истории; покупки требуют подтверждения конкретных условий.',
        'unimplemented':'Полные эффекты всех классов, видов и заклинаний, ношение брони без владения, импорт приключений и полный World Engine ещё не реализованы. Зарегистрированные базовый бой, урон/спасброски, инвентарь и торговля работают. Без ранее объявленного смертельного риска необратимый исход останавливается. Голосовой ввод исключён; произвольное название способности не даёт эффекта.',
    }
    text=guide[value.topic]
    if value.topic in ('advantage','ability_check','modifier','proficiency'):text+='\n\nИсточник: '+SOURCES.get(preset.edition,'Пользовательский пресет')+'.'
    return text
