"""r240: мир собирается из готовых блоков, если процесс умер на сборке.

Живой случай 05–06.11: разбор дошёл до 128 из 128 принятых блоков, все секции
оплачены, а `run()` умер до сборки мира. На экране это «частично готово»,
игра не начинается, и повторить нечего — платить второй раз не за что.

Здесь три сценария приёмки r240:

1. все блоки приняты, `result` пуст → `resume_pending` собирает мир из
   `creation_batch_history`: статус `ready`/`blocked`, `result` заполнен,
   вызовов модели ноль;
2. часть блоков не принята → честный `partial`, `finalize` не зовётся;
3. родитель, разделённый на детей и прерванный, не считается прерванным
   блоком в счётчике: работу сделали принятые дети.

Ключ сценария 3: `remaining()` — единственный счётчик, по которому
`resume_pending` решает «есть ли остаток». Пока прерванный родитель попадал
туда как `pending`, прогон с полностью принятыми детьми уходил в `partial`
мимо сборки мира.
"""
import asyncio,json
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import new
from test_preparation_sections import book,upload,SectionFixture
from test_preparation_resume_r238 import PoisonFixture,started,wait


def died_before_assembly(pipeline,id):
    """Процесс умер между последним принятым блоком и сборкой мира."""
    with pipeline.preparation.connection() as db:
        db.execute('UPDATE creation_runs SET status="interrupted",worker="",heartbeat=0,result="" WHERE id=?',(id,))


def test_run_died_before_assembly_is_assembled_from_history_without_paying(tmp_path,monkeypatch):
    """128 из 128 приняты, `result` пуст → мир собирается сам, модель молчит."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'died-before-assembly')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        died_before_assembly(pipeline,run['id'])
        raised=asyncio.run(pipeline.resume_pending())
        assert raised==[run['id']],raised
        status=pipeline.status(run['id'])
        assert status['status'] in ('ready','blocked'),status
        assert status['done']==status['total'] and status['done']>0,status
        raw=pipeline.raw(run['id'])
        assert raw['result'],'сборка не сохранила мир'
        result=json.loads(raw['result'])
        assert result.get('assembled_from_history') is True,result.keys()
        assert result.get('definition'),'в результате нет мира'
        assert len(llm.payloads)==calls,'сборка из истории сходила в модель'


def test_a_run_with_rejected_blocks_stays_partial_and_is_not_assembled(tmp_path,monkeypatch):
    """Часть блоков не принята → честный `partial`, `finalize` не зовётся."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    llm=PoisonFixture(marker='Дом2')
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=60))
        run=started(client,url,'partial-not-assembled')
        first=wait(client,run['id'])
        assert first['status']=='partial',first
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        finalize_calls=[]
        original=pipeline.finalize
        pipeline.finalize=lambda id:(finalize_calls.append(id),original(id))[1]
        died_before_assembly(pipeline,run['id'])
        raised=asyncio.run(pipeline.resume_pending())
        assert raised==[],raised
        assert finalize_calls==[],finalize_calls
        status=pipeline.status(run['id'])
        assert status['status']=='partial',status
        assert status['done']<status['total'],status
        assert not (pipeline.raw(run['id'])['result'] or ''),'неполный прогон получил мир'
        assert len(llm.payloads)==calls,'закрытие неполного прогона сходило в модель'


def test_blocked_run_with_all_blocks_accepted_is_assembled_by_the_button(tmp_path,monkeypatch):
    """«Структура мира не сошлась» на старом коде: кнопка собирает мир заново."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'blocked-then-assembled')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        stale=json.loads(pipeline.raw(run['id'])['result'])
        stale['ready']=False
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_runs SET status="blocked",worker="",heartbeat=0,result=? WHERE id=?',
                (json.dumps(stale,ensure_ascii=False),run['id']))
        pending=pipeline.status(run['id'])
        assert pending['status']=='blocked' and pending['ready'] is False,pending
        # Кнопка предлагается именно как сборка, а не «добить блоки»: работы по
        # блокам нет, есть сборка из готовых секций.
        assert pending['retry']['assembly'] is True and pending['retry']['can_retry'] is True,pending['retry']
        assert pending['retry']['blocks']==[],pending['retry']
        client.post('/api/creation/runs/'+run['id']+'/retry',json={}).raise_for_status()
        status=wait(client,run['id'])
        assert status['status']=='ready',status
        assert json.loads(pipeline.raw(run['id'])['result']).get('assembled_from_history') is True
        assert len(llm.payloads)==calls,'сборка по кнопке сходила в модель'


def test_all_blocks_read_but_no_world_says_so_instead_of_promising_to_continue(tmp_path,monkeypatch):
    """Итог без остатка не обещает «продолжу сам» — продолжать нечего."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'honest-final')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        text=pipeline.partial_message(run['id'],dict(done=9,total=9,details=[]))
        assert 'не собрался' in text,text
        assert 'остальные в очереди' not in text and 'Продолжу сам' not in text,text


def test_interrupted_parent_with_accepted_children_is_not_an_unfinished_block(tmp_path,monkeypatch):
    """Родитель разделён и прерван, дети приняты → остатка нет, мир собирается."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=SectionFixture(limit=2200)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book())
        run=started(client,url,'split-parent')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        with pipeline.preparation.connection() as db:
            # Обрыв между записью детей и записью статуса родителя: дети
            # приняты, а родитель остался `interrupted`.
            db.execute('UPDATE creation_parts SET status="interrupted" WHERE id IN ('
                'SELECT p.id FROM creation_parts p JOIN creation_part_links l ON l.part=p.id '
                'WHERE l.run=? AND p.status="split" AND p.children NOT IN ("","[]") LIMIT 1)',(run['id'],))
            parents=int(db.execute('SELECT count(*) FROM creation_parts p JOIN creation_part_links l ON l.part=p.id '
                'WHERE l.run=? AND p.status="interrupted" AND p.children NOT IN ("","[]")',(run['id'],)).fetchone()[0])
        assert parents==1,'подмена статуса не сработала'
        left=pipeline.remaining(run['id'])
        # Работу сделали дети: у родителя нет ни принятого, ни прерванного куска.
        assert left['pending']==0,left
        assert left['done']==left['total'] and not left['resumable'],left
        calls=len(llm.payloads)
        died_before_assembly(pipeline,run['id'])
        raised=asyncio.run(pipeline.resume_pending())
        assert raised==[run['id']],raised
        status=pipeline.status(run['id'])
        assert status['status']=='ready',status
        assert json.loads(pipeline.raw(run['id'])['result']).get('assembled_from_history') is True
        assert len(llm.payloads)==calls,'сборка из истории сходила в модель'


def test_blocked_run_with_all_blocks_accepted_is_assembled_by_restart(tmp_path,monkeypatch):
    """«Структура мира не сошлась» на старом коде: старт приложения собирает мир сам."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'blocked-then-restart')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        stale=json.loads(pipeline.raw(run['id'])['result'])
        # Прежний движок собрал мир «в отказ»: блоки приняты, мира нет.
        stale['ready']=False
        stale.pop('assembled_from_history',None)
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_runs SET status="blocked",worker="",heartbeat=0,result=? WHERE id=?',
                (json.dumps(stale,ensure_ascii=False),run['id']))
        assert pipeline.status(run['id'])['status']=='blocked'
        raised=asyncio.run(pipeline.resume_pending())
        assert raised==[run['id']],raised
        status=pipeline.status(run['id'])
        assert status['status']=='ready',status
        stored=json.loads(pipeline.raw(run['id'])['result'])
        assert stored.get('assembled_from_history') is True
        # Собранный мир должен быть подтверждаем: пустая запись режима в
        # результате оставляла «режим неизвестен» и игра не начиналась.
        assert stored['preparation_metadata']['preparation_mode']=='world_structure',stored['preparation_metadata']
        from solo.prototype_prepared import prepared_start_readiness
        readiness=prepared_start_readiness(client.app.state.preparation,draft['id'])
        assert readiness['world_ready'] is True,readiness
        # И мир подтверждается тем же путём, что нажимает игрок: иначе «готов»
        # остаётся словом, а кампания не начинается.
        revision=client.get('/api/creation/drafts/'+draft['id']).json()['revision']
        confirmed=pipeline.confirm_world(draft['id'],expected_revision=revision,run_id=run['id'],confirm=True)
        assert confirmed['body']['prepared'] is True
        assert prepared_start_readiness(client.app.state.preparation,draft['id'])['ready'] is True
        assert len(llm.payloads)==calls,'сборка при старте сходила в модель'


def test_partial_run_with_rejected_blocks_is_not_touched_at_start(tmp_path,monkeypatch):
    """Часть блоков не принята → старт приложения не собирает мир и не зовёт модель."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    llm=PoisonFixture(marker='Дом2')
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=60))
        run=started(client,url,'partial-at-start')
        first=wait(client,run['id'])
        assert first['status']=='partial',first
        pipeline=client.app.state.preparation_pipeline
        calls=len(llm.payloads)
        finalize_calls=[]
        original=pipeline.finalize
        pipeline.finalize=lambda id:(finalize_calls.append(id),original(id))[1]
        raised=asyncio.run(pipeline.resume_pending())
        assert finalize_calls==[],finalize_calls
        assert raised==[],raised
        assert pipeline.status(run['id'])['status']=='partial'
        stored=json.loads(pipeline.raw(run['id'])['result'] or '{}')
        assert not stored.get('ready') and not stored.get('assembled_from_history'),'неполный прогон получил мир'
        assert len(llm.payloads)==calls,'старт приложения сходил в модель за неполным прогоном'


def test_world_assembled_by_this_code_and_refused_is_not_reassembled_at_start(tmp_path,monkeypatch):
    """Сборка тем же кодом не сошлась — второй раз на старте её не повторяют."""
    from solo.preparation_pipeline import current_build
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=12))
        run=started(client,url,'refused-today')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        stale=json.loads(pipeline.raw(run['id'])['result'])
        stale.update(ready=False,assembled_from_history=True,assembly_build=current_build())
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_runs SET status="blocked",worker="",heartbeat=0,result=? WHERE id=?',
                (json.dumps(stale,ensure_ascii=False),run['id']))
        assert pipeline.needs_assembly(pipeline.raw(run['id'])['result']) is False
        finalize_calls=[]
        original=pipeline.finalize
        pipeline.finalize=lambda id:(finalize_calls.append(id),original(id))[1]
        calls=len(llm.payloads)
        raised=asyncio.run(pipeline.resume_pending())
        assert finalize_calls==[],finalize_calls
        assert raised==[],raised
        assert pipeline.status(run['id'])['status']=='blocked'
        assert len(llm.payloads)==calls
