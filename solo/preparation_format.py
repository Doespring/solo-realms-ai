"""Only representation repairs; source facts always pass the existing validator."""
import hashlib,json,re
from .json_framing import unframe
from .preparation_contracts import PreparationBatch

class SourceReferenceError(ValueError):
    def __init__(self,field):self.field=field;super().__init__('Неизвестный идентификатор фрагмента источника')

def source_input(pages):
    """Stable verbatim spans: the model selects evidence, never rewrites it.

    A page with no extractable text used to be sent as an empty span: the model
    was asked to find evidence in nothing, spent a request on it, and the
    emptiness was never reported. Such pages are left out of the request and
    named instead, so the material's incompleteness stays visible to the caller
    and the pages that do have text are not delayed by the ones that cannot.
    """
    readable=[p for p in pages if str(p.get('text') or '').strip()]
    skipped=[dict(document=p.get('document'),page=p.get('page'),reason='Нет текста на странице') for p in pages
             if not str(p.get('text') or '').strip()]
    rendered=[];spans={}
    for page in readable:
        text=page['text'];offset=0;parts=[]
        while offset<len(text):
            end=min(len(text),offset+1400)
            if end<len(text):
                newline=text.rfind('\n',offset+600,end)
                if newline>=0:end=newline+1
            quote=text[offset:end];name='S'+str(len(spans)+1).zfill(3)
            spans[name]=dict(document=page['document'],page=page['page'],quote=quote)
            parts.append('['+name+'] '+quote);offset=end
        rendered.append(dict(page,text=''.join(parts)))
    # The caller keeps this beside the batch: an unreadable page is a fact about
    # the material, not a silent gap in the analysis.
    for page in rendered:page['skipped_pages']=skipped
    schema=PreparationBatch.model_json_schema();legacy=schema['$defs']['SourceQuote']
    schema['$defs']['SourceQuote']={'anyOf':[legacy,dict(type='object',properties=dict(source_span=dict(type='string',pattern=r'^S[0-9]{3,4}$')),required=['source_span'],additionalProperties=False)]}
    return rendered,spans,schema

def json_value(raw):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Повтор JSON-поля: '+key[:80])
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=unique)

def decode(raw,spans=None):
    raw,framing=unframe(raw.lstrip('\ufeff'));changes=[framing] if framing else []
    value=json_value(raw)
    if isinstance(value,str):
        value=json_value(value);changes.append('encoded_json_string')
    if isinstance(value,dict) and isinstance(value.get('entities'),list):
        for entity in value['entities']:
            if not isinstance(entity,dict):continue
            key=entity.get('key')
            if isinstance(key,str) and key and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key):
                entity['key']='entity_'+hashlib.sha256((str(entity.get('kind'))+'|'+key).encode()).hexdigest()[:24]
                changes.append('entities.key_ascii_identifier')
    if isinstance(value,dict) and isinstance(value.get('problems'),list) and len(value['problems'])>20:
        # "problems" is the analysis's own gap report, not a source fact. A long list must
        # not discard an otherwise valid block; the block stays incomplete either way.
        value['problems']=value['problems'][:20];changes.append('problems_truncated')
    if isinstance(value,dict):
        groups=[('metadata_evidence',value.get('metadata_evidence'))]
        if isinstance(value.get('entities'),list):groups += [(f'entities.{i}.evidence',e.get('evidence')) for i,e in enumerate(value['entities']) if isinstance(e,dict)]
        for field,evidence in groups:
            if not isinstance(evidence,list):continue
            for i,proof in enumerate(evidence):
                if isinstance(proof,dict) and set(proof)=={'source_span'}:
                    ref=proof['source_span']
                    # The transmitted text marks spans as [S001], so the model sometimes
                    # echoes that wrapper. It names the same fragment of this block; any
                    # other unknown ID stays a reference error.
                    if isinstance(ref,str) and ref not in spans:
                        bare=ref.strip()
                        if re.fullmatch(r'\[S\d+\]',bare) and bare[1:-1] in spans:
                            ref=bare[1:-1];changes.append('source_span_brackets_removed')
                    if not spans or not isinstance(ref,str) or ref not in spans:raise SourceReferenceError(field+'.'+str(i)+'.source_span')
                    evidence[i]=spans[ref].copy();changes.append('verbatim_source_span')
    return PreparationBatch.model_validate(value),list(dict.fromkeys(changes))

def diagnostics(exc):
    if isinstance(exc,SourceReferenceError):return [dict(field=exc.field,reason='unknown_source_span',message=str(exc))]
    if isinstance(exc,json.JSONDecodeError):return [dict(field='$',reason='json_invalid',message=f'{exc.msg}; строка {exc.lineno}, столбец {exc.colno}')]
    if hasattr(exc,'errors'):
        return [dict(field='.'.join(map(str,e['loc'])) or '$',reason=e['type'],message=e['msg'][:240]) for e in exc.errors(include_input=False,include_url=False,include_context=False)[:8]]
    # Never echo arbitrary model text or provider response bodies to the player.
    return [dict(field='$',reason=type(exc).__name__,message='Ответ не прошёл проверку структуры')]

def split_pages(pages):
    """Disjoint children preserve every character and physical-page provenance."""
    length=sum(len(p['text']) for p in pages)
    if length<2000:return []
    pivot=length//2;left=[];right=[];used=0
    for page in pages:
        text=page['text'];cut=max(0,min(len(text),pivot-used))
        # Prefer a nearby paragraph boundary, keeping both sides verbatim.
        if 0<cut<len(text):
            newline=text.rfind('\n',max(0,cut-400),cut)
            if newline>0:cut=newline+1
        if cut:left.append(dict(page,text=text[:cut]))
        if cut<len(text):right.append(dict(page,text=text[cut:],offset=page.get('offset',0)+cut))
        used+=len(text)
    return [left,right] if left and right else []
