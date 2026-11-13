"""Bounded proposals: inference cannot directly approve or mutate game state."""
from typing import Literal
from pydantic import Field
from .contracts import Strict

class SourceQuote(Strict):
    document:str=Field(pattern=r'^[0-9a-f]{64}$')
    page:int=Field(ge=1,le=2000)
    quote:str=Field(min_length=3,max_length=3000)

class PreparedEntity(Strict):
    key:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    kind:Literal['location','npc','route','object','event','table','tracker','survival','companion','wilderness','chapter','scene','opponent','reward','special_rule','media']
    location:str=Field(default='',max_length=120)
    data:dict=Field(default_factory=dict)
    evidence:list[SourceQuote]=Field(default_factory=list,max_length=12)
    origin:Literal['source','original']='source'
    essential:bool=True

class PreparationBatch(Strict):
    title:str=Field(default='',max_length=100)
    ruleset:Literal['dnd2014','dnd2024','unknown']='unknown'
    start:str=Field(default='',max_length=120)
    minimum_level:int|None=Field(default=None,ge=1,le=20)
    maximum_level:int|None=Field(default=None,ge=1,le=20)
    party_size:int|None=Field(default=None,ge=1,le=12)
    metadata_evidence:list[SourceQuote]=Field(default_factory=list,max_length=12)
    entities:list[PreparedEntity]=Field(default_factory=list,max_length=80)
    problems:list[str]=Field(default_factory=list,max_length=20)

class PrepareRequest(Strict):
    request_id:str=Field(min_length=1,max_length=80)
    ruleset:Literal['dnd2014','dnd2024']|None=None
    consent_to_send:bool=False
    world_structure:bool=False


class PreparationResumeMetadata(Strict):
    preparation_mode:Literal['world_structure','source_extract','unknown']='unknown'
    source_ruleset:Literal['dnd2014','dnd2024']|None=None
