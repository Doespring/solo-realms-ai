"""Wire-only projections. Authoritative validators always retain their full models."""
import copy,json

ANNOTATIONS={'title','description','examples','default','$comment'}
VERSION='tuning-1'

def compact_schema(schema):
    if isinstance(schema,list):return [compact_schema(v) for v in schema]
    if not isinstance(schema,dict):return schema
    # Property / definition NAMES are not schema keywords.
    return {k:({name:compact_schema(v) for name,v in value.items()} if k in ('properties','$defs','definitions','patternProperties') else compact_schema(value))
            for k,value in schema.items() if k not in ANNOTATIONS}

def shared_contracts(schemas):
    definitions={};models={}
    for name,schema in schemas.items():
        schema=compact_schema(copy.deepcopy(schema))
        for key,value in schema.pop('$defs',{}).items():
            if key in definitions and definitions[key]!=value:raise ValueError('Conflicting shared contract '+key)
            definitions[key]=value
        models[name]=schema
    return {'$defs':definitions,'models':models}

def character_catalog(choices):
    result=copy.deepcopy(choices)
    for key in ('source','coverage'):result.pop(key,None)
    for group in ('features','items'):
        result[group]={id:{k:v for k,v in record.items() if k in ('name','status')} for id,record in result.get(group,{}).items()}
    for group in ('classes','species','backgrounds'):
        for record in result.get(group,{}).values():record.pop('page',None)
    return result

def compact_user(user,mode):
    if not isinstance(user,str):return user
    try:value=json.loads(user)
    except (ValueError,TypeError):return user
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))
