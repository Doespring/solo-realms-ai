"""Строгая JSON Schema из pydantic и санитайзер для отката.

Зачем оба механизма. Провайдер, который честно применяет `response_format`
со схемой, не даёт модели придумать ключ вообще. Валидатор при этом строгий:
`PreparedEntity` и контейнер объявлены с `extra='forbid'`, поэтому один
выдуманный ключ роняет весь блок, а не одно поле. Провайдер, который схему
не применяет, по-прежнему вернёт что захочет — такой ответ правится по полям
локально, и каждый выброшенный путь попадает в отчёт, а не в тишину.
"""
import copy,re

NULL_SCHEMA={'type':'null'}
INVALID=object()
#: Поля, которые strict-режим OpenAI не принимает: значения по умолчанию.
DROP_KEYS=('default','title','examples','$comment','discriminator')
#: Ключи, которых strict-режим не знает. Схема с ними уходит провайдеру как
#: json_schema без `strict: true`: она всё равно объясняет модели форму, но
#: провайдер не вправе отвергнуть её как «невалидную strict-схему».
UNSUPPORTED_KEYS=('pattern','format','minLength','maxLength','minimum','maximum',
    'exclusiveMinimum','exclusiveMaximum','multipleOf','minItems','maxItems','uniqueItems','oneOf','not')

#: Один и тот же смысл модель называет десятком слов. Это перевод, а не
#: догадка: без него `kind` вне списка роняет всю сущность.
KIND_ALIASES={
    'location':'location','place':'location','city':'location','town':'location','village':'location',
    'room':'location','area':'location','region':'location','building':'location','settlement':'location',
    'site':'location','dungeon':'location','level':'location','map_area':'location',
    'npc':'npc','person':'npc','character':'npc','creature':'npc','monster':'npc','humanoid':'npc',
    'being':'npc','group':'npc','faction':'npc','people':'npc',
    'route':'route','path':'route','road':'route','passage':'route','trail':'route','corridor':'route',
    'connection':'route','link':'route','exit':'route',
    'object':'object','item':'object','door':'object','chest':'object','weapon':'object','armor':'object',
    'tool':'object','container':'object','treasure':'object','scenery':'object','prop':'object',
    'structure':'object','vehicle':'object','trap':'object','furniture':'object',
    'event':'event','quest':'event','encounter':'event','timed_event':'event','hook':'event',
    'table':'table','rolltable':'table','roll_table':'table','random_table':'table','loot_table':'table',
    'tracker':'tracker','counter':'tracker','clock':'tracker',
    'survival':'survival','wilderness':'wilderness','companion':'companion','ally':'companion',
    'follower':'companion','chapter':'chapter','scene':'scene','opponent':'opponent','enemy':'opponent',
    'villain':'opponent','reward':'reward','loot':'reward','special_rule':'special_rule','rule':'special_rule',
    'media':'media','image':'media','illustration':'media','handout':'media','picture':'media'}


def _resolve(schema,defs):
    seen=0
    while isinstance(schema,dict) and '$ref' in schema and seen<8:
        ref=schema['$ref'];name=ref.rsplit('/',1)[-1]
        target=defs.get(name)
        if not isinstance(target,dict):break
        schema=target;seen+=1
    return schema


def _nullable(branches):
    return any(b==NULL_SCHEMA or b.get('type')=='null' for b in branches if isinstance(b,dict))


def _strict(node,defs):
    if isinstance(node,list):return [_strict(value,defs) for value in node]
    if not isinstance(node,dict):return node
    out={}
    for key,value in node.items():
        if key in DROP_KEYS:continue
        if key in ('properties','patternProperties'):
            out[key]={name:_strict(sub,defs) for name,sub in value.items()}
        elif key=='$defs':
            out[key]={name:_strict(sub,defs) for name,sub in value.items()}
        elif key=='anyOf' and _nullable(value):
            # strict принимает `type:["integer","null"]`; `anyOf` с null-веткой
            # он тоже понимает, но type-массив короче и не раздувает схему.
            rest=[branch for branch in value if branch!=NULL_SCHEMA and branch.get('type')!='null']
            if len(rest)==1 and isinstance(rest[0],dict) and isinstance(rest[0].get('type'),str):
                merged=_strict(rest[0],defs);merged['type']=[merged['type'],'null'];out.update(merged)
            else:out[key]=[_strict(branch,defs) for branch in value]
        else:
            out[key]=_strict(value,defs)
    if out.get('type')=='object' and isinstance(out.get('properties'),dict):
        # strict: каждое поле обязательное, лишние запрещены.
        out['required']=list(out['properties'])
        out['additionalProperties']=False
    return out


def strict_schema(schema):
    """Схема из pydantic в виде, который принимает strict-режим провайдера.

    Ничего не пишется руками: вход — `Model.model_json_schema()`. Свободный
    словарь (`data: dict`) strict выразить не умеет — такой узел остаётся с
    `additionalProperties: true`, а `strict_problems` называет его вслух.
    Провайдеру схема всё равно уходит (json_schema без `strict: true`), чтобы
    модель видела форму, а не угадывала её.
    """
    root=_strict(copy.deepcopy(schema),schema.get('$defs') or schema.get('definitions') or {})
    return root


def strict_problems(schema,limit=50):
    """Почему схема не годится для `strict: true`: пути, а не догадки.

    Пустой список — схему можно отправлять со `strict: true`. Непустой —
    отправлять её strict-провайдеру нельзя (он честно ответит 400), значит она
    уходит без strict-флага и ответ всё равно проходит санитайзер.
    """
    defs=schema.get('$defs') or schema.get('definitions') or {} if isinstance(schema,dict) else {}
    found=[]
    def walk(node,path,depth=0):
        if len(found)>=limit or depth>12:return
        if isinstance(node,list):
            for i,item in enumerate(node):walk(item,path+'.'+str(i),depth+1)
            return
        if not isinstance(node,dict):return
        node=_resolve(node,defs)
        if not isinstance(node,dict):return
        if node.get('type')=='object' and node.get('additionalProperties') is not False:
            found.append(path+':additionalProperties')
        for key in UNSUPPORTED_KEYS:
            if key in node:found.append(path+':'+key)
        for key,value in node.items():
            if key in ('properties','$defs','definitions','patternProperties'):
                for name,sub in value.items():walk(sub,path+'.'+name,depth+1)
            elif key in ('items','additionalProperties','anyOf','oneOf','allOf','not'):
                walk(value,path+'.'+key,depth+1)
    walk(schema,'$')
    return found[:limit]


def schema_name(schema,fallback='game_contract'):
    """Имя схемы для response_format: только [A-Za-z0-9_-], как требует wire."""
    title=schema.get('title') if isinstance(schema,dict) else None
    name=re.sub(r'[^A-Za-z0-9_-]','_',str(title or fallback))[:64]
    return name or fallback


def _types(node):
    kind=node.get('type')
    if isinstance(kind,str):return [kind]
    if isinstance(kind,list):return [value for value in kind if isinstance(value,str)]
    return []


def _allowed(node):
    values=node.get('enum')
    if isinstance(values,list):return values
    values=node.get('const')
    if values is not None:return [values]
    return None


def _normalize_enum(value,allowed):
    """Значение по списку из самой схемы: точное, регистр, синоним, вхождение."""
    text=str(value).strip().casefold()
    for candidate in allowed:
        if isinstance(candidate,str) and candidate.casefold()==text:return candidate
    alias=KIND_ALIASES.get(text) or KIND_ALIASES.get(re.sub(r'[\s\-]+','_',text))
    if alias in allowed:return alias
    hits=[candidate for candidate in allowed if isinstance(candidate,str) and candidate.casefold() in text]
    if len(hits)==1:return hits[0]
    hits=[candidate for candidate in allowed if isinstance(candidate,str) and text in candidate.casefold()]
    if len(hits)==1:return hits[0]
    return INVALID


def _choose_branch(branches,value,defs):
    resolved=[_resolve(branch,defs) for branch in branches]
    if isinstance(value,dict):
        exact=[node for node in resolved if isinstance(node.get('properties'),dict)
               and set(node['properties'])==set(value)]
        if exact:return exact[0]
        covering=[node for node in resolved if isinstance(node.get('properties'),dict)
                  and set(value)<=set(node['properties'])]
        if covering:return max(covering,key=lambda node:len(node['properties']))
    return resolved[0] if resolved else {}


def _walk(node,value,path,defs,notes):
    node=_resolve(node,defs)
    if not isinstance(node,dict):return value
    branches=node.get('anyOf') or node.get('oneOf')
    if isinstance(branches,list) and branches:
        if value is None and _nullable(branches):return None
        return _walk(_choose_branch(branches,value,defs),value,path,defs,notes)
    if value is None:return None
    allowed=_allowed(node)
    if allowed is not None:
        if value not in allowed:
            normalized=_normalize_enum(value,allowed)
            if normalized is INVALID:
                notes.append(dict(path=path,action='dropped',reason='enum_out_of_list'))
                return INVALID
            notes.append(dict(path=path,action='normalized',reason='enum:'+str(value)[:40]))
            return normalized
        return value
    kinds=_types(node)
    if 'object' in kinds:
        if not isinstance(value,dict):
            notes.append(dict(path=path,action='dropped',reason='not_object'))
            return INVALID
        properties=node.get('properties') or {}
        # Нет `required` — значит обязательных полей нет (это семантика JSON
        # Schema). strict-схема, наоборот, перечисляет их все явно.
        required=node.get('required')
        must=set(required) if isinstance(required,list) else set()
        extra=node.get('additionalProperties')
        cleaned={}
        for key,item in value.items():
            child=str(key) if not path else path+'.'+str(key)
            if key in properties:
                walked=_walk(properties[key],item,child,defs,notes)
                if walked is INVALID:
                    # Обязательное поле чинить нечем — блок уйдёт в ремонт с
                    # точной ошибкой pydantic. Необязательное просто выброшено.
                    if key in must:return INVALID
                    notes.append(dict(path=child,action='dropped',reason='invalid_value'))
                    continue
                cleaned[key]=walked
            elif isinstance(extra,dict):
                walked=_walk(extra,item,child,defs,notes)
                if walked is not INVALID:cleaned[key]=walked
            elif extra is False:
                # Это и есть галлюцинация ключа. Путь уходит в метрику, чтобы
                # было видно, что именно придумывает модель.
                notes.append(dict(path=child,action='dropped',reason='unknown_key'))
            else:cleaned[key]=item
        return cleaned
    if 'array' in kinds:
        if isinstance(value,dict):
            notes.append(dict(path=path,action='normalized',reason='single_object_to_list'));value=[value]
        elif not isinstance(value,list):
            notes.append(dict(path=path,action='normalized',reason='scalar_to_list'));value=[value]
        items=node.get('items') if isinstance(node.get('items'),dict) else None
        cleaned=[]
        for index,item in enumerate(value):
            child=path+'.'+str(index)
            walked=_walk(items,item,child,defs,notes) if items else item
            if walked is INVALID:
                # Элемент списка, который нельзя починить (не объект, `kind`
                # вне списка при обязательном поле) — потеря, и она названа.
                notes.append(dict(path=child,action='dropped',reason='invalid_item'))
                continue
            cleaned.append(walked)
        limit=node.get('maxItems')
        if isinstance(limit,int) and len(cleaned)>limit:
            # Список длиннее контракта — это тоже потеря, и она называется.
            for index in range(limit,len(cleaned)):notes.append(dict(path=path+'.'+str(index),action='dropped',reason='over_max_items'))
            cleaned=cleaned[:limit]
        return cleaned
    if 'integer' in kinds and isinstance(value,str) and re.fullmatch(r'-?\d+',value.strip()):
        notes.append(dict(path=path,action='normalized',reason='string_to_integer'));return int(value.strip())
    if 'number' in kinds and isinstance(value,str):
        try:number=float(value.strip())
        except ValueError:return value
        notes.append(dict(path=path,action='normalized',reason='string_to_number'));return number
    if 'boolean' in kinds and isinstance(value,str):
        text=value.strip().casefold()
        if text in ('true','да','yes','1'):
            notes.append(dict(path=path,action='normalized',reason='string_to_boolean'));return True
        if text in ('false','нет','no','0'):
            notes.append(dict(path=path,action='normalized',reason='string_to_boolean'));return False
        return value
    if 'string' in kinds and isinstance(value,(int,float)) and not isinstance(value,bool):
        notes.append(dict(path=path,action='normalized',reason='number_to_string'));return str(value)
    return value


def sanitize(model,value):
    """Привести ответ модели к схеме модели: (значение, отчёт).

    Отчёт — список `{path,action,reason}`. `action='dropped'` значит, что без
    этой потери ответ невалиден (выдуманный ключ, не-объект в списке сущностей,
    `kind` вне списка); `normalized` — приведение типа. Молча ничего не теряется.

    Сентинел наружу не выходит: если починить корень нельзя (обязательное поле
    с недопустимым значением), возвращается исходный словарь — тогда pydantic
    назовёт точный путь, и этот путь уйдёт в ремонт.
    """
    schema=model if isinstance(model,dict) else model.model_json_schema()
    defs=schema.get('$defs') or schema.get('definitions') or {}
    notes=[]
    cleaned=_walk(schema,copy.deepcopy(value),'',defs,notes)
    if cleaned is INVALID:
        notes.append(dict(path='$',action='dropped',reason='root_invalid'))
        cleaned=copy.deepcopy(value)
    return cleaned,notes


def dropped_paths(notes):
    """Только пути потерь — то, что уходит в метрику `schema_dropped_keys`."""
    return [note['path'] or '$' for note in notes if note['action']=='dropped']


def error_lines(exc,limit=20):
    """Ошибки pydantic построчно — вход ремонтного запроса, не текст для игрока."""
    if hasattr(exc,'field'):return ['$.'+str(exc.field)[:200]+': '+str(exc)[:240]]
    if not hasattr(exc,'errors'):return [type(exc).__name__+': '+str(exc)[:200]]
    lines=[]
    for error in exc.errors(include_input=False,include_url=False,include_context=False)[:limit]:
        where='.'.join(str(part) for part in error['loc']) or '$'
        lines.append(where+': '+error['msg'][:240])
    return lines
