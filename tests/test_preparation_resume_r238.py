"""r238: разбор книги живёт в базе и продолжается без вкладки.

Проверяется то, из-за чего живой прогон 05.11 умер на 23-м блоке из 281:
задача внутри процесса приложения пропала вместе с процессом, а в базе
остался `running`, которого нет. Здесь четыре сценария приёмки и логика
сторожа:

1. обрыв на середине (закрытие страницы = отмена задачи) — тот же прогон
   продолжается с того же места, готовые блоки не оплачиваются;
2. рестарт процесса — новое приложение на той же базе поднимает прогон само
   и пишет в лог «поднял после перезапуска»;
3. ядовитый блок — три попытки, `failed`, остальные блоки доезжают до
   результата, в конце честный итог со списком;
4. повторный запуск после успеха не делает ни одного вызова модели;
5. сторож: `running` без воркера дольше 90 с — поднять, свежее сердцебиение
   чужого живого воркера — не трогать, `interrupted` с остатком — продолжить.

Ключевая защита от двойной оплаты — отпечаток секции: `ensure_part` находит
уже проверенную часть по `signature` и `run()` её пропускает.
"""
import asyncio,contextlib,json,re,sqlite3,time
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.preparation_watchdog import PreparationWatchdog
from test_preparation_pipeline import PreparationFixture,new
from test_preparation_sections import book,upload,SectionFixture

FINAL=('ready','partial','failed','blocked','cancelled')


def wait(client,id,timeout=90):
    """Ждать настоящий финал, а не «не running»: interrupted — не финал."""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=client.get('/api/creation/runs/'+id).json()
        if value['status'] in FINAL:return value
        time.sleep(.02)
    raise AssertionError(value)


def started(client,url,request_id):
    response=client.post(url+'/prepare',json=dict(request_id=request_id,ruleset='dnd2014',consent_to_send=True))
    assert response.status_code==200,response.text
    return response.json()


class GatedFixture(SectionFixture):
    """Отвечает по секциям, а на N-й замирает: это и есть «вкладку закрыли».

    Секция, на которой воркер умер, видна в `blocked_on`: её страницы попадают
    в payload, ответа нет.
    """

    def __init__(self,freeze_after=1):
        super().__init__(limit=10**9);self.freeze_after=freeze_after;self.answered_sections=[]
        self.gate=asyncio.Event();self.blocked_on=None

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);page=payload['pages'][0];name=re.search(r'Дом\d+',page['text']).group()
        if len(self.answered_sections)>=self.freeze_after:
            self.blocked_on=name;self.payloads.append(payload)
            await self.gate.wait()
            return
        self.payloads.append(payload);self.answered_sections.append(name)
        result=dict(title='Книга',ruleset='dnd2014',start=name,entities=[dict(
            key='house'+name[3:],kind='location',data=dict(name=name),
            evidence=[dict(source_span=re.search(r'\[(S\d+)\]',page['text'])[1])])])
        yield json.dumps(result,ensure_ascii=False)


class PoisonFixture(SectionFixture):
    """Один блок отвечает недоказуемым фактом всегда: три попытки — и падает.

    Не битым JSON: обрыв формы лечится делением секции, а «цитата не
    подтверждена» — отказ ответа, который повтором с урезанной задачей как раз
    и проверяется. Ссылка на несуществующий фрагмент даёт ValueError без
    списка полей pydantic, поэтому деления не будет.
    """

    def __init__(self,marker='Дом2'):
        super().__init__(limit=10**9);self.marker=marker;self.calls={};self.poisoned=[]

    def _name(self,payload):
        return re.search(r'Дом\d+',payload['pages'][0]['text']).group()

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);name=self._name(payload)
        self.calls[name]=self.calls.get(name,0)+1
        self.payloads.append(payload)
        # Маркер ищем целым словом: «Дом2» не должен совпасть с «Дом21»,
        # иначе ядовитым оказывается не тот блок.
        hit=any(re.search(self.marker+r'(?!\d)',page.get('text') or '') for page in payload['pages'])
        if hit:
            self.poisoned.append(name);self.metric(streamed=10)
            yield json.dumps(dict(title='Книга',ruleset='dnd2014',start=name,entities=[dict(
                key='house'+name[3:],kind='location',data=dict(name=name),
                evidence=[dict(source_span='S999')])]),ensure_ascii=False)
            return
        self.metric(streamed=10)
        result=dict(title='Книга',ruleset='dnd2014',start=name,entities=[dict(
            key='house'+name[3:],kind='location',data=dict(name=name),
            evidence=[dict(source_span=re.search(r'\[(S\d+)\]',payload['pages'][0]['text'])[1])])])
        yield json.dumps(result,ensure_ascii=False)


def payload_documents(llm):
    return ' '.join(json.dumps(payload,ensure_ascii=False) for payload in llm.payloads)


def test_page_closed_midway_the_same_run_continues_without_paying_twice(tmp_path,monkeypatch):
    """Вкладку закрыли: прогон остаётся в базе и поднимается с того же блока.

    Готовые блоки не оплачиваются второй раз — их нет в запросах нового
    воркера, потому что отпечаток секции совпал с сохранённой частью.
    """
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    first=GatedFixture(freeze_after=1)
    with TestClient(create_app(tmp_path/'solo.sqlite3',first)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=20))
        run=started(client,url,'page-closed')
        deadline=time.monotonic()+30
        while not first.blocked_on and time.monotonic()<deadline:time.sleep(.02)
        assert first.blocked_on,first.payloads
        # Это и есть закрытие вкладки: процесс живёт, а задачи прогона больше нет.
        # Отмену ставим в потоке событий приложения, как это делает shutdown.
        task=client.app.state.preparation_pipeline.tasks[run['id']]
        client.portal.call(task.cancel)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            interrupted=client.app.state.preparation_pipeline.status(run['id'])
            if interrupted['status']=='interrupted':break
            time.sleep(.02)
        assert interrupted['status']=='interrupted',interrupted
        done_before=interrupted['done'];total=interrupted['total']
        assert 0<done_before<total,(done_before,total)
        answered=set(first.answered_sections)

    # Следующий воркер — снова «открытая страница»: продолжение с того же куска.
    second=GatedFixture(freeze_after=10**6)
    with TestClient(create_app(tmp_path/'solo.sqlite3',second)) as client:
        resumed=wait(client,run['id'])
        assert resumed['id']==run['id'],'продолжение обязано быть тем же прогоном'
        assert resumed['ready'] and resumed['done']==resumed['total']>done_before,resumed
        sent=payload_documents(second)
        for name in answered:
            assert name not in sent,('оплаченный блок перечитан заново: '+name)
        assert second.payloads


def test_restart_of_the_process_raises_the_run_and_says_so_in_the_log(tmp_path,monkeypatch,caplog):
    """Рестарт `solo-app`: на старте прогон поднимается сам, в логе это видно."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    first=GatedFixture(freeze_after=1)
    with TestClient(create_app(tmp_path/'solo.sqlite3',first)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=20))
        run=started(client,url,'restart')
        deadline=time.monotonic()+30
        while not first.blocked_on and time.monotonic()<deadline:time.sleep(.02)
        assert first.blocked_on
        answered=set(first.answered_sections)
    # Выход из контекста — это и есть перезапуск: lifespan гасит задачи, прогон
    # остаётся в базе в `interrupted`.
    with sqlite3.connect(tmp_path/'preparation.sqlite3') as db:
        status,worker=db.execute('SELECT status,worker FROM creation_runs WHERE id=?',(run['id'],)).fetchone()
    assert status=='interrupted' and worker=='',(status,worker)

    caplog.set_level('WARNING')
    second=GatedFixture(freeze_after=10**6)
    with TestClient(create_app(tmp_path/'solo.sqlite3',second)) as client:
        resumed=wait(client,run['id'])
        assert resumed['ready'] and resumed['done']==resumed['total'],resumed
        # Сторож тоже живёт в приложении, а не в чьей-то вкладке.
        assert getattr(client.app.state,'preparation_watchdog',None) is not None
        assert client.app.state.preparation_watchdog.pipeline is client.app.state.preparation_pipeline
    messages='\n'.join(record.getMessage() for record in caplog.records)
    assert 'поднял после перезапуска' in messages,messages
    sent=payload_documents(second)
    for name in answered:assert name not in sent,('оплаченный блок перечитан заново: '+name)


def test_poison_block_fails_thrice_and_the_rest_still_arrives(tmp_path,monkeypatch):
    """Ядовитый блок: три попытки, `failed`, остальные доезжают, итог честный."""
    # Блоки крупные: ядовитая секция не режется на огрызки, и у неё есть что
    # урезать во второй и третьей попытке.
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    llm=PoisonFixture(marker='Дом2')
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=60))
        run=started(client,url,'poison')
        status=wait(client,run['id'])
        assert status['status']=='partial',status
        assert 0<status['done']<status['total'],(status['done'],status['total'])
        assert status['retry']['can_retry'] and status['retry']['blocks'],status['retry']
        assert all(block['attempts']==3 and block['exhausted'] for block in status['retry']['blocks']),status['retry']
        assert 'не разобрал' in status['message'] and 'Добить' in status['message'],status['message']
        # Три попытки у каждого отказавшего блока — по истории попыток в базе,
        # а не по счётчику в фейковой модели: там же видно, что принятые блоки
        # прочитаны один раз и сосед не перечитывался.
        with client.app.state.preparation.connection() as db:
            rows=[dict(row) for row in db.execute(
                'SELECT p.id,p.status,p.attempts,count(a.attempt) failures FROM creation_parts p '
                'JOIN creation_part_links l ON l.part=p.id LEFT JOIN creation_part_attempts a '
                'ON a.part=p.id AND a.status="failed" WHERE l.run=? GROUP BY p.id',(run['id'],))]
        failed=[row for row in rows if row['status']=='failed']
        validated=[row for row in rows if row['status']=='validated']
        assert failed and validated,failed
        assert all(row['attempts']==3 and row['failures']==3 for row in failed),failed
        assert all(row['attempts']==1 for row in validated),validated
        # «Добить эти три» — осознанный повтор: счётчик попыток обнуляется,
        # и модель спрашивают ровно три раза, а не «сколько получится».
        before=len(llm.payloads)
        client.post('/api/creation/runs/'+run['id']+'/retry',json={}).raise_for_status()
        again=wait(client,run['id'])
        assert len(llm.payloads)-before==3,(len(llm.payloads)-before,llm.calls)
        assert again['status']=='partial',again


def test_a_second_run_of_a_prepared_book_makes_no_model_call(tmp_path,monkeypatch):
    """Повторный запуск после успеха: мир собирается из сохранённых секций."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'first')
        status=wait(client,run['id'])
        assert status['ready'],status
        calls=len(llm.payloads)
        again=started(client,url,'second')
        assert again['reused'],again
        assert len(llm.payloads)==calls,('повторный запуск сходил в модель: %s'%(len(llm.payloads)-calls,))
        assert again['status'] in ('ready','blocked'),again


class FakePreparation:
    """Минимальная база прогонов: сторожу нужны только эти четыре поля."""

    def __init__(self,path):self.path=path

    @contextlib.contextmanager
    def connection(self):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()


class FakePipeline:
    worker='me-1'

    def __init__(self,path,left):
        self.preparation=FakePreparation(path);self.left=left;self.resumed=[];self.closed=[];self.tasks={}

    def remaining(self,id):return self.left[id]

    async def resume(self,id,reason=''):
        self.resumed.append((id,reason));return {}

    def close_leftover(self,id,left=None):
        self.closed.append(id);return {}


def fake_run(path,id,status,worker,heartbeat):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS creation_runs(id TEXT PRIMARY KEY,status TEXT,worker TEXT,heartbeat REAL,created REAL)')
        db.execute('INSERT OR REPLACE INTO creation_runs VALUES(?,?,?,?,?)',(id,status,worker,heartbeat,time.time()))


def left(done=3,total=5,resumable=True):
    return dict(done=done,pending=0 if done>=total else total-done,failed=0,exhausted=0,total=total,
        resumable=resumable,blocks=[],details=[])


def test_watchdog_raises_only_what_is_really_abandoned(tmp_path):
    """Сторож: брошенное поднимает, чужой живой воркер не трогает."""
    path=tmp_path/'preparation.sqlite3'
    now=time.time()
    fake_run(path,'stale','running','dead-1',now-300)
    fake_run(path,'fresh-foreign','running','other-process',now-5)
    fake_run(path,'stopped','interrupted','',0)
    fake_run(path,'finished','running','dead-2',now-300)
    pipeline=FakePipeline(path,{'stale':left(),'fresh-foreign':left(),'stopped':left(),
        'finished':left(done=5,resumable=False)})
    watchdog=PreparationWatchdog(pipeline,interval=60,stale=90,log_path=tmp_path/'watchdog.log')

    raised=asyncio.run(watchdog.tick())

    assert set(raised)=={'stale','stopped'},raised
    reasons=dict(pipeline.resumed)
    assert 'молчит 300 с' in reasons['stale'] or 'воркера нет' in reasons['stale'],reasons
    assert 'перезапуска' in reasons['stopped'],reasons
    # Живой чужой воркер и прогон без остатка не поднимаются.
    assert 'fresh-foreign' not in reasons and 'finished' not in reasons,reasons
    assert pipeline.closed==['finished'],pipeline.closed
    assert watchdog.ticks==1
    log=(tmp_path/'watchdog.log').read_text(encoding='utf8')
    assert 'поднял после перезапуска' in log,log


def test_resume_pending_leaves_a_run_without_remainder_closed(tmp_path,monkeypatch):
    """Прогон, у которого всё готово, не поднимают заново — его закрывают."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'closed-leftover')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_runs SET status="interrupted",worker="",heartbeat=0 WHERE id=?',(run['id'],))
        raised=asyncio.run(pipeline.resume_pending())
        assert raised==[],raised
        assert len(llm.payloads)==calls,'прогон без остатка сходил в модель заново'
        assert pipeline.status(run['id'])['status'] in ('ready','partial'),pipeline.status(run['id'])
