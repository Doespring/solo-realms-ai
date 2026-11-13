"""r227: прогон подготовки на многостраничной книге с ответами модели из лога.

Что это НЕ доказывает: живой вызов RouterAI (на этой машине нет ключа) и
конкретную купленную книгу (её здесь тоже нет). Что доказывает: настоящие
грязные ответы модели из журнала владельца проходят через настоящий пайплайн
подготовки и дают валидные блоки, а в журнал пишутся schema_mode, repairs,
schema_dropped_keys, bytes и duration.

Сырые ответы содержат цитаты из купленной книги, поэтому в git они не лежат:
проба достаёт их из журнала подготовки и раскладывает в приватный каталог
(~/.cache/solo-realms/r227-raw, переопределяется R227_RAW_DIR).

Запуск: PYTHONPATH=. .venv/bin/python scripts/probe_r227_preparation.py
"""
import asyncio,json,os,pathlib,re,sqlite3,sys,tempfile,time
from contextlib import nullcontext

sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.llm import MockLLM
from solo.preparation_format import decode,read,source_input

JOURNAL=pathlib.Path(os.environ.get('SOLO_PREPARATION_JOURNAL','runtime/web-local/preparation.sqlite3'))
RAW_DIR=pathlib.Path(os.environ.get('R227_RAW_DIR',pathlib.Path.home()/'.cache/solo-realms/r227-raw'))


def load_raws():
    """Ответы модели, на которых владелец упёрся: статус failed и есть raw."""
    if not any(RAW_DIR.glob('*.txt')):
        RAW_DIR.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(JOURNAL) as db:
            rows=list(db.execute("SELECT id,raw FROM creation_parts WHERE status='failed' AND raw IS NOT NULL AND length(raw)>200 ORDER BY created"))
        for name,raw in rows:(RAW_DIR/(name[:12]+'.txt')).write_text(raw,encoding='utf8')
    raws=[path.read_text(encoding='utf8') for path in sorted(RAW_DIR.glob('*.txt'))]
    if not raws:raise SystemExit('Нет исходных ответов: '+str(RAW_DIR))
    return raws


def spans_of(payload):
    """Документ и страница — настоящие: иначе проверка цитат забракует мир."""
    spans={}
    for page in payload.get('pages') or []:
        for name,text in re.findall(r'\[(S\d+)\]\s*([^\[]*)',page.get('text','')):
            spans[name]=dict(document=page['document'],page=page['page'],quote=text.strip() or page.get('text','')[:80])
    return spans


def repaired_batch(spans):
    """Ответ, который модель отдала бы после ремонта: только точные факты блока."""
    def proof(name):
        key=next((name_ for name_,value in spans.items() if name in value['quote']),next(iter(spans)))
        return dict(source_span=key)
    return dict(title='Мост',ruleset='dnd2014',start='Мост',entities=[
        dict(key='bridge',kind='location',data=dict(name='Мост',description='Перед вами мост через реку.'),evidence=[proof('Мост')]),
        dict(key='tower',kind='location',data=dict(name='Башня',description='На другом берегу стоит башня.'),evidence=[proof('Башня')]),
        dict(key='guard',kind='npc',location='Мост',data=dict(name='Стражник',character='Стражник у моста проверяет путников.'),evidence=[proof('Стражник')])])


def retarget(raw,spans):
    """Ссылки ответа пересчитаны на фрагменты этого блока, а не чужого."""
    names=list(spans)*4;counter=iter(names)
    return re.sub(r'"S\d+"',lambda match:'"'+next(counter,next(iter(spans)))+'"',raw)


class ReplayLLM(MockLLM):
    """Ответы модели — дословно из журнала владельца, ссылки пересчитаны на блок."""
    def __init__(self,raws):
        super().__init__();self.raws=raws;self.index=0;self.calls=[];self.repairs=0
    def configured(self):return routerai_preset(),'replay-only-never-sent'
    def scope(self,*args):return nullcontext()
    def contract_error(self,code):self.metrics.append(dict(status='invalid_contract',error=code))
    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);spans=spans_of(payload)
        self.metrics.append(dict(mode='preparation_world',mock=True,schema_mode='none'))
        if payload.get('task')=='repair-preparation-block-v1':
            self.repairs+=1
            text=json.dumps(repaired_batch(spans),ensure_ascii=False)
        else:
            self.calls.append(payload.get('batch'))
            raw=self.raws[self.index%len(self.raws)];self.index+=1
            text=retarget(raw,spans)
        for offset in range(0,len(text),257):yield text[offset:offset+257];await asyncio.sleep(0)


def terminal(client,run,deadline=180):
    end=time.monotonic()+deadline
    while time.monotonic()<end:
        value=client.get('/api/creation/runs/'+run).json()
        if value['status'] not in ('queued','running'):return value
        time.sleep(.05)
    raise AssertionError('подготовка не завершилась')


def source_replay():
    """Каждый ответ владельца — против ЕГО страниц: (годных, потерь, отказов).

    Ответы в журнале сохранены вместе с блоком `source`, поэтому ссылки на
    фрагменты разрешаются теми же S-номерами. Это проверка не формы вообще, а
    того, что модель на конкретной книге сказала: сколько ключей придумала и
    какие ответы после чистки всё равно не годятся.
    """
    if not JOURNAL.is_file():return dict(answers=0,valid=0,dropped=0,failures=[])
    from solo.preparation_format import json_value_lenient
    from solo.source_extraction import parse_answer
    valid=dropped=0;failures=[]
    with sqlite3.connect('file:'+str(JOURNAL)+'?mode=ro',uri=True) as db:
        rows=list(db.execute("SELECT id,source,raw FROM creation_parts WHERE status='failed' AND raw IS NOT NULL AND length(raw)>200 ORDER BY created"))
        for name,source,raw in rows:
            pages=json.loads(source).get('pages') or []
            _,spans,_=source_input(pages)
            try:
                value,_=json_value_lenient(raw)
                if isinstance(value,dict) and 'entries' in value:saved,_,report=parse_answer(raw,spans,'dnd2014')
                else:saved,_,report=read(raw,spans)
            except ValueError as exc:failures.append(type(exc).__name__)
            except json.JSONDecodeError:failures.append('json_invalid')
            else:
                valid+=1;dropped+=len(report['schema_dropped_keys'])
    return dict(answers=len(rows),valid=valid,dropped=dropped,failures=sorted(set(failures)))


def main():
    raws=load_raws()
    old_failures=[]
    for raw in raws:
        names=set(re.findall(r'"S\d+"',raw))
        spans={name.strip('"'):dict(document='0'*64,page=1,quote='цитата блока') for name in names} or None
        try:decode(raw,spans)
        except ValueError as exc:old_failures.append(type(exc).__name__+': '+str(exc)[:80])
        except TypeError as exc:old_failures.append('TypeError: '+str(exc)[:80])
    work=pathlib.Path(tempfile.mkdtemp(prefix='r227-probe-'))
    llm=ReplayLLM(raws)
    with TestClient(create_app(work/'solo.sqlite3',llm)) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        client.put(url,json={'description':'Книга пятой редакции, D&D 2014','revision':draft['revision']}).raise_for_status()
        for index in range(6):
            book=('Прочитайте игрокам:\nМост через реку. Башня на берегу. Стражник проверяет путников.\n'
                  f'Страница {index}: место {index} описано здесь. Тропа ведёт дальше.\n')*140
            client.post(url+'/files',files={'file':(f'book{index}.txt',book.encode())}).raise_for_status()
        end=time.monotonic()+90
        while any(f['job']['status']!='complete' for f in client.get(url).json()['files']):
            assert time.monotonic()<end;time.sleep(.03)
        started=client.post(url+'/prepare',json=dict(request_id='r227',ruleset='dnd2014',consent_to_send=True)).json()
        run=terminal(client,started['id'])
        with client.app.state.preparation.connection() as db:
            parts=[dict(status=row['status'],metric=json.loads(row['metric'] or '{}'),diagnostic=json.loads(row['diagnostic'] or '[]'))
                   for row in db.execute('SELECT p.status,p.metric,p.diagnostic FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=? ORDER BY l.ordinal',(run['id'],))]
        report=dict(raws=len(raws),status=run['status'],ready=run['ready'],done=run['done'],total=run['total'],
            structure=run.get('structure'),repair_calls=llm.repairs,model_calls=len(llm.calls),
            old_direct_decode_failures=len(old_failures),old_failure_reasons=old_failures,
            parts=[dict(status=part['status'],schema_mode=part['metric'].get('schema_mode'),
                        repairs=part['metric'].get('repairs'),dropped=part['metric'].get('schema_dropped_keys'),
                        bytes=part['metric'].get('bytes'),duration_ms=part['metric'].get('duration_ms'))
                   for part in parts],
            structural_errors=[item for part in parts for item in part['diagnostic'] if item.get('reason') in ('extra_forbidden','literal_error','model_type','too_long')],
            extra_inputs_present='Extra inputs are not permitted' in json.dumps([part['diagnostic'] for part in parts]))
    report['source_replay']=source_replay()
    out=pathlib.Path('reports/r227');out.mkdir(parents=True,exist_ok=True)
    (out/'preparation-run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({key:report[key] for key in ('status','ready','done','total','raws','old_direct_decode_failures',
        'model_calls','repair_calls','structure','source_replay')}|dict(structural_errors=len(report['structural_errors']),
        extra_inputs=report['extra_inputs_present']),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
