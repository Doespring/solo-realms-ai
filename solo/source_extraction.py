"""Small source-only wire contract; no generated stats, prose or executable rules."""
import hashlib,json,re
from typing import Literal
from pydantic import Field,field_validator,model_validator,ValidationError
from .contracts import Strict
from .preparation_contracts import PreparationBatch,PreparedEntity
from .preparation_limits import limits
from .preparation_format import decode,read,source_input,SourceReferenceError,json_value,json_value_lenient
from .json_framing import unframe
from .prepared_world import normalized
from .structured_output import dropped_paths,sanitize,strict_schema

class SourceEntry(Strict):
    kind:Literal['location','npc','chapter','scene','opponent','special_rule','reward']
    name:str=Field(min_length=1,max_length=120)
    source_spans:list[str]=Field(min_length=1,max_length=64)
    location:str=Field(default='',max_length=120)
    start:bool=False

    @field_validator('location',mode='before')
    @classmethod
    def absent_location(cls,value):
        return '' if value is None else value


class SourceExtraction(Strict):
    title:str=Field(default='',max_length=100)
    entries:list[SourceEntry]=Field(max_length=20)

    @model_validator(mode='before')
    @classmethod
    def reference_shapes(cls,value):
        """Repair only shape: a reference object becomes the exact entry name.

        Upstream sometimes emits ``{"name": ...}`` (or key/id) where the
        contract requires the exact string. Rewriting is allowed only when the
        object names exactly one entry of this same transmitted block; anything
        else stays a visible validation failure, never a silent guess.
        """
        if not isinstance(value,dict):return value
        value=dict(value);entries=value.get('entries')
        if not isinstance(entries,list):return value
        names={e['name'] for e in entries if isinstance(e,dict)
               and isinstance(e.get('name'),str) and e.get('kind')=='location'}
        errors=[]
        def resolve(raw,loc):
            if raw is None:return ''
            if isinstance(raw,str):return raw
            if not isinstance(raw,dict) or not raw or set(raw)-{'key','id','name'} \
                    or not all(isinstance(v,str) and v for v in raw.values()):
                errors.append(dict(type='value_error',loc=loc,input=None,ctx={'error':ValueError(
                    'Ссылка локации имеет недопустимую форму; ожидается точное строковое имя.')}))
                return raw
            matches=names & {v for v in raw.values()}
            if len(matches)!=1:
                errors.append(dict(type='value_error',loc=loc,input=None,ctx={'error':ValueError(
                    'Ссылка локации не определена однозначно в проверяемом блоке; ожидается точное строковое имя.')}))
                return raw
            return matches.pop()
        repaired=[]
        for index,entry in enumerate(entries):
            if not isinstance(entry,dict):repaired.append(entry);continue
            entry=dict(entry)
            if 'location' in entry:entry['location']=resolve(entry['location'],('entries',index,'location'))
            raw=entry.get('start')
            if isinstance(raw,dict) or (isinstance(raw,str) and raw):
                # A reference at start marks this entry as the start only when
                # it names this very entry; otherwise it is not a start flag.
                entry['start']=resolve(raw,('entries',index,'start'))==entry.get('name')
            repaired.append(entry)
        if errors:raise ValidationError.from_exception_data(cls.__name__,errors)
        value['entries']=repaired
        return value


def request(pages,description,ruleset,batch,existing):
    wire,spans,_=source_input(pages)
    return dict(task='source-extract-v1',description=description,ruleset=ruleset,pages=wire,batch=batch,existing_keys=existing,max_entries=limits()['entities_per_call']),spans,strict_schema(SourceExtraction.model_json_schema())

def parse(raw,spans,ruleset):return parse_answer(raw,spans,ruleset,clean=False)[:2]

def parse_answer(raw,spans,ruleset,clean=True):
    """Компактный разбор: модель, изменения, отчёт санитайзера.

    `clean` — путь подготовки: повтор ключа берётся по последнему значению, а
    ответ проходит санитайзер по схеме `SourceExtraction`. Строгий `parse`
    оставлен для проверки wire-формата сохранённых блоков.
    """
    framed,_=unframe(raw.lstrip('\ufeff'));format_changes=[]
    report={'schema_dropped_keys':[],'normalized':[],'duplicate_keys':[]}
    try:
        if clean:value,duplicates=json_value_lenient(framed);report['duplicate_keys']+=duplicates
        else:value=json_value(framed)
    except json.JSONDecodeError:
        # A known field accidentally placed inside a reference array. Insert
        # only the missing delimiter; all IDs and scalar values stay unchanged.
        fixed,count=re.subn(r'("source_spans"\s*:\s*\[\s*"(?:S\d+|\[S\d+\])"(?:\s*,\s*"(?:S\d+|\[S\d+\])")*)\s*,\s*"(start|location)"\s*:',r'\1],"\2":',framed,count=8)
        if not count:raise
        value=json_value(fixed);format_changes.append('misclosed_source_spans_array')
    if isinstance(value,dict) and 'entries' not in value:
        saved,changes,inner=read(raw,spans,clean)
        return saved,changes,inner
    if clean and isinstance(value,dict):
        value,notes=sanitize(SourceExtraction,value)
        report['schema_dropped_keys']=dropped_paths(notes)
        report['normalized']=[note['path']+':'+note['reason'] for note in notes if note['action']=='normalized']
        format_changes+=report['normalized']
    value=SourceExtraction.model_validate(value);entities=[];start='';changes=['compact_source_contract',*format_changes,*('duplicate_json_key:'+key for key in report['duplicate_keys'])]
    for i,entry in enumerate(value.entries):
        refs=[]
        for ref in entry.source_spans:
            # The prompt displays [S001] as a delimiter. Accept that exact
            # wrapper only when its ID belongs to this transmitted block.
            if ref not in spans and re.fullmatch(r'\[S\d+\]',ref) and ref[1:-1] in spans:
                ref=ref[1:-1];changes.append('source_span_brackets_removed')
            if ref not in spans:raise SourceReferenceError(f'entries.{i}.source_spans')
            if ref not in refs:refs.append(ref)
        evidence=[spans[s].copy() for s in refs]
        # A wrong span index can be corrected only by an exact literal match in
        # this transmitted block. Never search unrelated book pages or paraphrase.
        if not any(normalized(entry.name) in normalized(p['quote']) for p in evidence):
            matches=[p for p in spans.values() if normalized(entry.name) in normalized(p['quote'])]
            if matches:evidence=[matches[0].copy()];changes.append('exact_name_source_reference')
        # The output contains only an atomic name and optional location. Keep
        # sufficient exact evidence rather than a redundant whole-block ref list.
        named=[p for p in evidence if normalized(entry.name) in normalized(p['quote'])]
        located=[p for p in evidence if entry.location and normalized(entry.location) in normalized(p['quote'])]
        selected=named[:1]+[p for p in located[:1] if p not in named[:1]]
        if selected:
            if len(selected)<len(evidence):changes.append('redundant_source_references_removed')
            evidence=selected
        else:evidence=evidence[:12]
        key='src_'+hashlib.sha256((entry.kind+'|'+entry.location+'|'+entry.name).encode()).hexdigest()[:24]
        kind=entry.kind;data=dict(name=entry.name)
        # Name-only extraction cannot judge obligation. Opponents, rewards and special
        # rules have no registered handler yet, so marking them essential here would
        # veto every book that contains a statblock. World-structure blocks still do.
        entities.append(PreparedEntity(key=key,kind=kind,location=entry.location,data=data,evidence=evidence,
            essential=kind not in ('chapter','scene','opponent','reward','special_rule')))
        if entry.start and kind=='location' and not start:start=entry.name
    return PreparationBatch(title=value.title,ruleset=ruleset,start=start,entities=entities),list(dict.fromkeys(changes)),report
