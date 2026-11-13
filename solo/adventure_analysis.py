"""Opt-in, bounded source analysis. Cached suggestions never approve a scenario."""
import hashlib,json,time,re
from typing import Literal
from pydantic import Field,StrictInt
from .contracts import Strict

class AnalysisRequest(Strict):
    document: str=Field(pattern=r'^[a-f0-9]{64}$')
    pages: list[int]=Field(min_length=1,max_length=4)
    role: Literal['qwen','gemini']='qwen'
    consent_to_send: bool=False
    include_images:bool=False
    problem: str=Field(default='',max_length=1000)
    request_id: str=Field(min_length=1,max_length=80)
    text_offset:int=Field(default=0,ge=0,le=500000)

class EntityLink(Strict):
    relation:Literal['located_in','leads_to','depicts','requires','rewards','mentions']
    target:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    status:Literal['requires_review','unresolved']='requires_review'

class TableCell(Strict):
    row:StrictInt=Field(ge=0,le=1000)
    column:StrictInt=Field(ge=0,le=100)
    text:str=Field(min_length=1,max_length=500)

class ContentEntity(Strict):
    id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    kind:Literal['table','map','image','handout','npc','opponent','location','scene','chapter','object','event','reward','additional_rule']
    page:int=Field(ge=1,le=2000)
    name:str=Field(min_length=1,max_length=120)
    quote:str=Field(default='',max_length=800)
    source:Literal['text','image']='text'
    source_refs:list[str]=Field(default_factory=list,max_length=16)
    cells:list[TableCell]=Field(default_factory=list,max_length=64)
    links:list[EntityLink]=Field(default_factory=list,max_length=12)
    review:Literal['requires_review']='requires_review'
    executable:Literal[False]=False

class Suggestion(Strict):
    page: int=Field(ge=1,le=2000)
    kind: Literal['rule','procedure','recommendation','alternative','read_aloud','gm_note','unknown','map','handout','mechanic_candidate']
    quote: str=Field(min_length=1,max_length=800)
    interpretation: str=Field(min_length=1,max_length=1600)
    review: Literal['requires_review']='requires_review'

class PageClassification(Strict):
    page:int=Field(ge=1,le=2000)
    kind:Literal['text','map','handout','mechanic','mixed','unknown']
    quote:str=Field(default='',max_length=800)
    needs_visual_review:bool=False
    source:Literal['text','image']='text'
    transcription:str=Field(default='',max_length=1600)

class MechanicCandidate(Strict):
    page:int=Field(ge=1,le=2000)
    quote:str=Field(min_length=1,max_length=800)
    edition:Literal['dnd2014','dnd2024','unknown']='unknown'
    operation:Literal['ability_check']='ability_check'
    ability:Literal['strength','dexterity','constitution','intelligence','wisdom','charisma']
    dc:StrictInt=Field(ge=0,le=40)
    trigger:str=Field(min_length=1,max_length=240)
    status:Literal['requires_review']='requires_review'
    executable:Literal[False]=False

class AnalysisResult(Strict):
    suggestions: list[Suggestion]=Field(max_length=30)
    problems: list[str]=Field(max_length=12)
    classifications:list[PageClassification]=Field(default_factory=list,max_length=4)
    mechanic_candidates:list[MechanicCandidate]=Field(default_factory=list,max_length=12)
    entities:list[ContentEntity]=Field(default_factory=list,max_length=12)

def validate_evidence(result,texts,edition='unknown',image_pages=(),layouts=None):
    pages={p['page']:' '.join(p['text'].split()) for p in texts}
    layouts=layouts or {};ids=set()
    for i,entity in enumerate(result.entities):
        field=f'entities.{i}'
        if entity.id in ids:raise ValueError(field+'.id: повтор идентификатора')
        ids.add(entity.id)
        if entity.page not in pages:raise ValueError(field+'.page: страница не передана')
        layout=layouts.get(entity.page,{})
        allowed={r['id'] for r in [*layout.get('regions',[]),*layout.get('images',[])]}|{f'p{entity.page}.render'}
        if any(ref not in allowed for ref in entity.source_refs):raise ValueError(field+'.source_refs: область отсутствует на странице')
        if entity.source=='image':
            if entity.page not in image_pages:raise ValueError(field+'.source: изображение не передано')
        else:
            quote=' '.join(entity.quote.split())
            if not quote or quote not in pages[entity.page]:raise ValueError(field+'.quote: цитата не подтверждена переданным текстом')
            if entity.source_refs and f'p{entity.page}.render' not in entity.source_refs:
                window=next(p for p in texts if p['page']==entity.page);offset=window.get('offset',0)
                refs={r['id']:r for r in layout.get('regions',[])}
                if any(ref not in refs for ref in entity.source_refs):raise ValueError(field+'.source_refs: у текстовой цитаты нет текстовой области')
                selected=[refs[ref] for ref in entity.source_refs]
                if any(r['start']<offset or r['end']>offset+len(window['text']) for r in selected):raise ValueError(field+'.source_refs: область вне переданного фрагмента')
                cited=' '.join(window['text'][r['start']-offset:r['end']-offset] for r in sorted(selected,key=lambda r:r['start']))
                if quote not in ' '.join(cited.split()):raise ValueError(field+'.source_refs: цитата отсутствует в выбранных областях')
            if ' '.join(entity.name.split()).casefold() not in quote.casefold():raise ValueError(field+'.name: имя отсутствует в цитате')
            if any(' '.join(c.text.split()) not in quote for c in entity.cells):raise ValueError(field+'.cells: текст ячейки не подтверждён цитатой')
        if entity.cells and entity.kind!='table':raise ValueError(field+'.cells: ячейки допустимы только у таблицы')
        coordinates=[(c.row,c.column) for c in entity.cells]
        if len(coordinates)!=len(set(coordinates)):raise ValueError(field+'.cells: повтор координат ячейки')
        if not entity.source_refs:entity.source_refs=[f'p{entity.page}.render']
    for entity in result.entities:
        for link in entity.links:
            if link.target not in ids:link.status='unresolved'
    entries=[(group,i,item) for group,items in [('suggestions',result.suggestions),('classifications',result.classifications),('mechanic_candidates',result.mechanic_candidates)] for i,item in enumerate(items)]
    for group,i,item in entries:
        field=f'{group}.{i}'
        if isinstance(item,PageClassification) and item.source=='image':
            if item.page not in image_pages or not item.needs_visual_review:raise ValueError(field+'.source: визуальный вывод требует переданного изображения и ручной сверки')
            continue
        if not item.quote.strip():raise ValueError(field+'.quote: текстовая цитата отсутствует')
        if ' '.join(item.quote.split()) not in pages.get(item.page,''):raise ValueError(field+'.quote: неподтверждённая цитата или страница')
    for item in result.classifications:
        if item.kind in ('map','handout') and not item.needs_visual_review:raise ValueError('Геометрия/вид раздатки не проверяются по одному тексту')
    names=dict(strength='сил',dexterity='ловк',constitution='телослож',intelligence='интеллект',wisdom='мудрост',charisma='харизм')
    for item in result.mechanic_candidates:
        if edition not in ('unknown',item.edition) and item.edition!='unknown':raise ValueError('Редакция кандидата не совпадает с источником')
        if not re.search(r'(?:Сл|DC)\s*'+str(item.dc)+r'\b',item.quote,re.I):raise ValueError('Сложность не подтверждена цитатой')
        if not (names[item.ability] in item.quote.casefold() or re.search(r'\b'+item.ability+r'\b',item.quote,re.I)) or ' '.join(item.trigger.split()) not in ' '.join(item.quote.split()):raise ValueError('Параметр или условие не подтверждены')
    return result

def image_kind(library,document,pages):
    """A printed map is read as a map, not as a page preview."""
    from .analysis_images import map_materials
    return 'map' if map_materials(library,document,pages) else 'page'

class AdventureAnalysis:
    def __init__(self,library,llm):self.library=library;self.llm=llm
    async def analyze(self,bundle,request):
        from .ai_roles import select_role
        with self.library.db() as db:
            belongs=db.execute('SELECT 1 FROM bundle_files WHERE bundle=? AND document=?',(bundle,request.document)).fetchone()
        if not belongs:raise ValueError('Источник не принадлежит комплекту')
        pages=sorted(set(request.pages));texts=[];coverage=[];layouts={}
        if any(page<1 or page>2000 for page in pages):raise ValueError('Недопустимый номер страницы')
        if request.include_images and (request.role!='gemini' or len(pages)>2):raise ValueError('Изображения — только Gemini, максимум две страницы')
        image_scale=image_kind(self.library,request.document,pages) if request.include_images else 'none'
        with self.library.db() as db:
            document=db.execute('SELECT format,status FROM documents WHERE id=?',(request.document,)).fetchone()
            for page in pages:
                row=db.execute('SELECT content FROM pages WHERE document=? AND number=?',(request.document,page)).fetchone()
                image_only=(request.include_images and page==1 and document and document['status']=='extracted' and document['format'] in ('.png','.jpg','.jpeg','.webp'))
                if row is None and not image_only:raise ValueError('Страница не извлечена локальным импортом')
                full=row[0] if row else '';start=request.text_offset
                if start and start>=len(full):raise ValueError('Смещение текста за границей страницы')
                selected=full[start:start+10000]
                texts.append(dict(page=page,text=selected,offset=start))
                coverage.append(dict(page=page,start=start,end=start+len(selected),total=len(full),next_offset=start+len(selected) if start+len(selected)<len(full) else None))
        from .pdf_pipeline import PDFPipeline
        index=PDFPipeline(self.library)
        for page in pages:
            try:layouts[page]=index.page(request.document,page)
            except ValueError:pass
        # Route by the work itself: a plain-text document or pages that are clean
        # text need no premium reader, so they go to the cheap interpreter. A PDF
        # with layout, tables and maps goes to the analyst model, which supports
        # machine-checked json_schema output and therefore cannot exceed limits.
        text_format=bool(document and str(document['format']).lower() in ('.txt','.md','.markdown'))
        mode='preparation_qwen' if (request.role=='qwen' or text_format) else 'pdf_analyst'
        profile,_=self.llm.configured();cfg,prompt_version=select_role(profile,mode)
        if request.include_images:
            from .ai_roles import vision_config
            # Which model saw the page and at what image tariff is the vision
            # role's answer, not the text analyst's: it also enters the cache
            # identity below, so the answer names the model that actually read it.
            cfg=vision_config(profile)
        from .ai_roles import prompt
        metadata=self.library.get(bundle).get('metadata',{});edition=str(metadata.get('edition','unknown'))
        edition={'2014':'dnd2014','2024':'dnd2024'}.get(edition,edition)
        if edition not in ('dnd2014','dnd2024'):edition='unknown'
        visual_context=[]
        if request.role=='qwen':
            with self.library.db() as db:
                rows=db.execute("SELECT key,body FROM analysis_cache WHERE json_extract(body,'$.identity.sha256')=? AND json_extract(body,'$.mode')='pdf_analyst' ORDER BY created DESC LIMIT 8",(request.document,)).fetchall()
            for row in rows:
                cached_visual=json.loads(row['body'])
                for item in cached_visual['result'].get('classifications',[])+cached_visual['result'].get('entities',[]):
                    if item['page'] in pages and item.get('source')=='image' and len(visual_context)<4:
                        visual_context.append(dict(analysis=row['key'],page=item['page'],kind=item['kind'],name=item.get('name','')[:120],transcription=item.get('transcription','')[:400],status='requires_review'))
                if visual_context:break
        parameters=cfg.model_dump(mode='json',exclude={'input_per_million','output_per_million','image_per_million','price_checked_at'})
        if 'supported_parameters' in parameters:parameters['supported_parameters']=sorted(parameters['supported_parameters'])
        # The pixels a map arrives as are part of what the model saw: a cached
        # analysis of the old shrunken picture must not answer for this one.
        from .analysis_images import VISION_VERSION
        identity=dict(sha256=request.document,pages=pages,text_offset=request.text_offset,visual_context_sha256=hashlib.sha256(json.dumps(visual_context,sort_keys=True).encode()).hexdigest(),model=cfg.model,parameters=parameters,provider=profile.base_url,endpoint=cfg.endpoint,prompt=prompt_version,prompt_sha256=hashlib.sha256(prompt(prompt_version).encode()).hexdigest(),wire_contract='pdf-pipeline-v1',include_images=request.include_images,image_scale=image_scale,**({'image_version':VISION_VERSION} if image_scale=='map' else {}),task='source-instructions-v1',problem=request.problem,ruleset=edition)
        cachekey=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.library.db() as db:
            cached=db.execute('SELECT body FROM analysis_cache WHERE key=?',(cachekey,)).fetchone()
            if cached:return dict(**json.loads(cached[0]),cached=True)
            if request.role=='gemini':
                previous=[json.loads(r[0]) for r in db.execute("SELECT body FROM analysis_cache WHERE json_extract(body,'$.identity.sha256')=?",(request.document,))]
                local_text_empty=request.include_images and not any(t['text'].strip() for t in texts)
                prior_qwen=any(v.get('identity',{}).get('sha256')==request.document and v.get('identity',{}).get('pages')==pages and v.get('mode')=='preparation_qwen' for v in previous)
                local_visual_ready=request.include_images and all(page in layouts for page in pages)
                if not request.problem.strip() or not (prior_qwen or local_text_empty or local_visual_ready):
                    raise ValueError('Gemini требует конкретную проблему и предыдущий локальный/Qwen-разбор этих страниц')
        if not request.consent_to_send:raise ValueError('Подтвердите отправку выбранных страниц провайдеру и возможную стоимость')
        if not request.include_images and not any(t['text'].strip() for t in texts):raise ValueError('Текст страниц пуст. Нужна локальная OCR либо явно разрешённая отправка изображений Gemini')
        payload=json.dumps(dict(task='source-instructions-v1',pages=texts,problem=request.problem,visual_context=visual_context),ensure_ascii=False,separators=(',',':'))
        if request.include_images:
            # vision_config already refused a missing image tariff.
            from .analysis_images import render_selected
            payload=[dict(type='text',text=payload),*render_selected(self.library,request.document,pages,image_scale)]
        with self.llm.scope('preparation:'+bundle,request.request_id):
            raw=''.join([c async for c in self.llm.stream('',payload,mode=mode,grammar=AnalysisResult.model_json_schema())])
        try:
            from .json_framing import unframe
            framed,normalization=unframe(raw)
            from .preparation_format import json_value
            result=AnalysisResult.model_validate(json_value(framed))
            validate_evidence(result,texts,identity['ruleset'],pages if request.include_images else (),layouts)
        except ValueError as exc:
            from .prepared_world import validation_reason
            self.llm.contract_error('invalid_source_analysis');raise ValueError('AI вернул непроверяемый разбор: '+validation_reason(exc)+'. Сценарий не изменён') from None
        value=dict(identity=identity,mode=mode,result=result.model_dump(),status='requires_review',coverage=coverage,executable=False,format_normalization=normalization,metric=self.llm.metrics[-1].copy())
        with self.library.db() as db:db.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?)',(cachekey,json.dumps(value,ensure_ascii=False),time.time()))
        return dict(**value,cached=False)
