"""Only representation repairs; source facts always pass the existing validator."""
import hashlib,json,re
from .json_framing import unframe
from .preparation_contracts import PreparationBatch
from .structured_output import dropped_paths,sanitize,strict_schema

class SourceReferenceError(ValueError):
    def __init__(self,field):self.field=field;super().__init__('Неизвестный идентификатор фрагмента источника')

def wire_schema(strict=False):
    """Схема ответа из pydantic; `strict` — версия для провайдера.

    Провайдеру нужны все поля в `required` и без значений по умолчанию, поэтому
    ему уходит `strict_schema(...)`. Санитайзер же работает по обычной схеме
    модели: в ней видно, какие поля действительно обязательные, и необязательное
    поле с мусором можно выбросить, не теряя всю сущность.

    Единственное расширение — `SourceQuote` принимает либо полную цитату, либо
    ссылку на фрагмент переданного блока (`{"source_span":"S001"}`): цитату
    модель выбирает, а не переписывает. Расширение описано здесь один раз, чтобы
    отправленная схема и санитайзер ответа не разъехались.
    """
    schema=strict_schema(PreparationBatch.model_json_schema()) if strict else PreparationBatch.model_json_schema()
    legacy=schema['$defs']['SourceQuote']
    schema['$defs']['SourceQuote']={'anyOf':[legacy,dict(type='object',properties=dict(source_span=dict(type='string',pattern=r'^S[0-9]{3,4}$')),required=['source_span'],additionalProperties=False)]}
    return schema

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
    return rendered,spans,wire_schema()

def json_value(raw):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Повтор JSON-поля: '+key[:80])
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=unique)

def json_value_lenient(raw):
    """Повтор ключа не повод терять весь блок: последнее значение и след в отчёте."""
    duplicates=[]
    def last_wins(pairs):
        value={}
        for key,item in pairs:
            if key in value:duplicates.append(key[:80])
            value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=last_wins),duplicates

def decode(raw,spans=None):
    """Строгая проверка wire-формата: любой лишний ключ или повтор — отказ.

    Это не путь подготовки (там ответ модели чистится по полям, см. `read`),
    а проверка, что сохранённый блок действительно лёг в контракт целиком.
    """
    return read(raw,spans,clean=False)[:2]

def read(raw,spans=None,clean=True):
    """Прочитать ответ модели: (PreparationBatch, изменения, отчёт).

    `clean` — путь подготовки. Схема провайдеру уходит, но применил ли он её,
    зависит от модели и маршрута, поэтому ответ всё равно проходит санитайзер
    по схеме pydantic: неизвестные ключи выбрасываются по полям, типы
    приводятся, и каждый такой случай попадает в отчёт с путём. Молча теряется
    только то, что перечислено в `schema_dropped_keys`.
    """
    raw,framing=unframe(raw.lstrip('\ufeff'));changes=[framing] if framing else []
    report={'schema_dropped_keys':[],'normalized':[],'duplicate_keys':[]}
    if clean:value,duplicates=json_value_lenient(raw);report['duplicate_keys']+=duplicates
    else:value=json_value(raw)
    if isinstance(value,str):
        if clean:
            value,duplicates=json_value_lenient(value);report['duplicate_keys']+=duplicates
        else:value=json_value(value)
        changes.append('encoded_json_string')
    if report['duplicate_keys']:
        changes+=['duplicate_json_key:'+key for key in dict.fromkeys(report['duplicate_keys'])]
        report['duplicate_keys']=list(dict.fromkeys(report['duplicate_keys']))[:20]
    if clean and isinstance(value,dict):
        value,notes=sanitize(wire_schema(),value)
        report['schema_dropped_keys']=dropped_paths(notes)
        report['normalized']=[note['path']+':'+note['reason'] for note in notes if note['action']=='normalized']
        changes+=report['normalized']
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
                    if isinstance(ref,str) and spans and ref not in spans:
                        bare=ref.strip()
                        if re.fullmatch(r'\[S\d+\]',bare) and bare[1:-1] in spans:
                            ref=bare[1:-1];changes.append('source_span_brackets_removed')
                    if not spans or not isinstance(ref,str) or ref not in spans:raise SourceReferenceError(field+'.'+str(i)+'.source_span')
                    evidence[i]=spans[ref].copy();changes.append('verbatim_source_span')
    try:return PreparationBatch.model_validate(value),list(dict.fromkeys(changes)),report
    except ValueError as exc:
        # Отчёт нужен и когда блок не прошёл: он говорит, какие ключи модель
        # придумала, ещё до ремонтного запроса.
        exc.report=report;raise

def diagnostics(exc):
    if isinstance(exc,SourceReferenceError):return [dict(field=exc.field,reason='unknown_source_span',message=str(exc))]
    if isinstance(exc,json.JSONDecodeError):return [dict(field='$',reason='json_invalid',message=f'{exc.msg}; строка {exc.lineno}, столбец {exc.colno}')]
    if hasattr(exc,'errors'):
        return [dict(field='.'.join(map(str,e['loc'])) or '$',reason=e['type'],message=e['msg'][:240]) for e in exc.errors(include_input=False,include_url=False,include_context=False)[:8]]
    # Never echo arbitrary model text or provider response bodies to the player.
    return [dict(field='$',reason=type(exc).__name__,message='Ответ не прошёл проверку структуры')]

#: Заголовок главы/раздела как его печатает книга. После ключевого слова
#: обязан идти номер или имя собственное («Глава 2. Башня», «Приложение А»,
#: «Книга первая»): без этого «Часть добычи он оставил себе» и «Part of the
#: treasure» становились границей секции, и платный вызов тратился на прозу.
_HEADING_KEYWORD=r'(?:Глава|Часть|Раздел|Книга|Акт|Приложение|Chapter|Part|Section|Act|Appendix)'
_HEADING_QUALIFIER=(r'(?:[^\w\n]*[0-9IVXLCА-ЯЁA-Z]'
    r'|[^\w\n]+(?:перв|втор|трет|четв|пят|шест|седьм|восьм|девят|десят|одиннадцат|двенадцат)'
    r'|[^\w\n]+(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth))')
HEADING=re.compile(r'(?m)^[^\S\n]*(?i:'+_HEADING_KEYWORD+r')\b'+_HEADING_QUALIFIER+r'[^\n]{0,70}(?<![.,;:])\s*$')

def starts_heading(text):
    """Кусок начинается с заголовка главы: с него начинается новая секция."""
    return bool(HEADING.match(text.lstrip('\ufeff\r\n\t ')))

def section_pieces(text,limit):
    """Режет текст страницы на куски не длиннее `limit`, держась глав.

    Граница главы — первое предпочтение: кусок начинается с заголовка и
    кончается там, где начинается следующий. Внутри слишком длинной главы
    режем по границе абзаца, и только если абзацев нет — по символу: терять
    текст нельзя, а склейка соседних глав ломает сущности на стыке.
    Возвращает список (offset, piece) в координатах страницы.
    """
    limit=max(1,int(limit))
    if not text:return []
    bounds=[0]+[m.start() for m in HEADING.finditer(text) if m.start()>0]+[len(text)]
    pieces=[]
    for start,end in zip(bounds,bounds[1:]):
        while start<end:
            cut=min(end,start+limit)
            if cut<end:
                newline=text.rfind('\n',start+max(1,limit//2),cut)
                if newline>start:cut=newline+1
            pieces.append((start,text[start:cut]))
            start=cut
    return pieces

def _page_parts(pages,limit):
    """Куски страниц по границам глав, не длиннее `limit` каждый."""
    for page in pages:
        text=page.get('text') or ''
        if not text.strip():continue
        for offset,piece in section_pieces(text,limit):
            if not piece.strip():continue
            yield dict(document=page.get('document'),page=page.get('page'),printed_page=page.get('printed_page'),offset=offset,text=piece)


def group_sections(parts,limit,floor=None):
    """Собирает куски в блоки одного платного вызова.

    Блок наполняется до `limit`: это вход одного ответа, и делить книгу мельче
    предела ответа — значит платить за заведомо неполный разбор. Граница главы
    предпочтительна, но только когда блок уже набрал `floor` (по умолчанию
    половину предела): иначе каждая глава в три тысячи знаков становится
    отдельным вызовом, и книга на 1,2 млн знаков превращается в триста вызовов
    вместо тридцати.

    Хвост короче `floor` не становится отдельным вызовом: он приклеивается к
    предыдущему блоку, если тот остаётся в пределе.
    """
    limit=max(1,int(limit));floor=int(floor if floor is not None else limit//2)
    batches=[];current=[];size=0
    for part in parts:
        piece=part['text']
        # Пустая заготовка — не работа, а мусор в плане: страница без текста
        # (карта, титул, колонтитул) не должна становиться отдельным платным
        # вызовом и не должна попадать в «готово N из M».
        if not piece.strip():continue
        if current and (size+len(piece)>limit or (starts_heading(piece) and size>=floor)):
            batches.append(current);current=[];size=0
        current.append(part);size+=len(piece)
    if current:
        tail=sum(len(p['text']) for p in current)
        if batches and tail<floor and sum(len(p['text']) for p in batches[-1])+tail<=limit:
            batches[-1].extend(current)
        else:batches.append(current)
    return batches

def split_pages(pages,minimum=2000):
    """Disjoint children preserve every character and physical-page provenance.

    `minimum` — порог «дальше дробить бессмысленно»: короче него секция
    отказывает честно, называя страницы, вместо того чтобы резать её на
    куски, которые модель уже не сможет собрать в связную сцену.
    """
    length=sum(len(p['text']) for p in pages)
    if length<minimum:return []
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
