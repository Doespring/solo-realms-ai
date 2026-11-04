"""Bounded proposals: inference cannot directly approve or mutate game state."""
from typing import Literal
from pydantic import Field,field_validator,model_validator,ValidationError
from .contracts import Strict

class SourceQuote(Strict):
    document:str=Field(pattern=r'^[0-9a-f]{64}$')
    page:int=Field(ge=1,le=2000)
    quote:str=Field(min_length=3,max_length=3000)

class PreparedEntity(Strict):
    key:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    kind:Literal['location','npc','route','object','event','table','tracker','survival','companion','wilderness','chapter','scene','opponent','reward','special_rule','media']
    location:str=Field(default='',max_length=120,description='Exact canonical location name as JSON string; use "" when no link, never null or an object.')
    data:dict=Field(default_factory=dict)
    evidence:list[SourceQuote]=Field(default_factory=list,max_length=12)
    origin:Literal['source','original']='source'
    essential:bool=True

    @field_validator('location',mode='before')
    @classmethod
    def absent_location(cls,value):
        return '' if value is None else value

class PreparationBatch(Strict):
    title:str=Field(default='',max_length=100)
    ruleset:Literal['dnd2014','dnd2024','unknown']='unknown'
    start:str=Field(default='',max_length=120,description='Exact confirmed start location name as JSON string, or "" if this block has no start. Never null/object.')
    minimum_level:int|None=Field(default=None,ge=1,le=20)
    maximum_level:int|None=Field(default=None,ge=1,le=20)
    party_size:int|None=Field(default=None,ge=1,le=12)
    metadata_evidence:list[SourceQuote]=Field(default_factory=list,max_length=12)
    entities:list[PreparedEntity]=Field(default_factory=list,max_length=80)
    problems:list[str]=Field(default_factory=list,max_length=20)

    @model_validator(mode='before')
    @classmethod
    def reference_shapes(cls,value):
        if not isinstance(value,dict):return value
        value=dict(value);entities=value.get('entities',[])
        locations=[(e.get('key'),e['data'].get('name')) for e in entities if isinstance(e,dict)
            and e.get('kind')=='location' and isinstance(e.get('data'),dict) and isinstance(e['data'].get('name'),str)] if isinstance(entities,list) else []
        errors=[]
        def reference(raw,loc):
            if raw is None:return ''  # Absence only; validator still requires mandatory world links.
            if not isinstance(raw,dict):return raw
            valid=bool(raw) and set(raw)<= {'key','id','name'} and all(isinstance(v,str) and v for v in raw.values())
            matches=[name for key,name in locations if valid and all((name if k=='name' else key)==v for k,v in raw.items())]
            if len(matches)==1:return matches[0]
            errors.append(dict(type='value_error',loc=loc,input=None,ctx={'error':ValueError('Ссылка локации не определена однозначно в проверяемом блоке; ожидается точное строковое имя.')}))
            return raw
        if 'start' in value:value['start']=reference(value['start'],('start',))
        if isinstance(entities,list):
            value['entities']=[dict(e,location=reference(e['location'],('entities',i,'location'))) if isinstance(e,dict) and 'location' in e else e for i,e in enumerate(entities)]
        if errors:raise ValidationError.from_exception_data(cls.__name__,errors)
        return value

class PrepareRequest(Strict):
    request_id:str=Field(min_length=1,max_length=80)
    ruleset:Literal['dnd2014','dnd2024']|None=None
    consent_to_send:bool=False
    world_structure:bool=False
    local_repair_only:bool=False


class PreparationResumeMetadata(Strict):
    preparation_mode:Literal['world_structure','source_extract','unknown']='unknown'
    source_ruleset:Literal['dnd2014','dnd2024']|None=None
