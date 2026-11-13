"""r244: три беды подготовки не возвращаются.

Каждая из них уже была в живых прогонах этой недели и лечится в
`solo/preparation_pipeline.py` — но держится на одном-двух `if`, которые
снимаются при следующей правке молча. Здесь четыре сценария приёмки:

1. повторный прогон того же черновика берёт план из базы (`stored_roots`) и
   не плодит куски: план пересобирался на каждом перезапуске (291 -> 682
   части), прогресс прыгал назад, принятые блоки читались заново;
2. прогон, у которого не восстановился вход, закрывается `failed` с понятным
   текстом — иначе сторож поднимает его каждую минуту и пишет в журнал
   «поднял», а работы нет;
3. кусок `split` без детей (обрыв между записью статуса и записью детей)
   читается как обычный блок, а не висит в очереди вечно;
4. `interrupted`-родитель с принятыми детьми не считается прерванным: работу
   сделали дети, и остатка в счётчике нет.

Числа приёмки (сколько кусков до и после) доказывают пункт 1: счётчик строк
`creation_part_links` не растёт от повторного вызова.
"""
import asyncio,json
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import new
from test_preparation_sections import book,upload,SectionFixture
from test_preparation_resume_r238 import started,wait


def counts(pipeline,id):
    """Строки плана и кусков: их число не должно меняться от повторного вызова."""
    with pipeline.preparation.connection() as db:
        links=db.execute('SELECT count(*) FROM creation_part_links WHERE run=?',(id,)).fetchone()[0]
        parts=db.execute('SELECT count(*) FROM creation_parts').fetchone()[0]
    return links,parts


def first_part(pipeline,id,*,with_children=False):
    sql=('SELECT p.id,p.children FROM creation_parts p JOIN creation_part_links l ON l.part=p.id '
        'WHERE l.run=? ORDER BY l.ordinal')
    with pipeline.preparation.connection() as db:
        rows=[dict(row) for row in db.execute(sql,(id,))]
    for row in rows:
        if bool(json.loads(row['children'] or '[]'))==with_children:return row['id']
    raise AssertionError('подходящего куска в плане нет')


def test_second_run_of_the_same_draft_reuses_the_stored_plan(tmp_path,monkeypatch):
    """План из базы: принятые куски не пересобираются и не оплачиваются снова."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=40))
        run=started(client,url,'stored-plan')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        before=counts(pipeline,run['id']);roots=pipeline.stored_roots(run['id'])
        assert len(roots)>=2,'на книге из нескольких секций план должен быть не из одного куска'
        calls=len(llm.payloads)
        value,private,documents,ruleset,identity=pipeline.resume_state(run['id'])
        # Перезапуск мог прийти с другими настройками нарезки (или с правкой
        # группировки секций): пересчитанный план нарезал бы ту же книгу иначе,
        # и на живом прогоне так появлялись новые куски — 291 часть после
        # первого прогона, 682 после второго, прогресс прыгал назад.
        monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2000')
        asyncio.run(pipeline.run(run['id'],value,private,documents,ruleset,identity))
        assert counts(pipeline,run['id'])==before,'повторный прогон пересобрал план и создал новые куски'
        assert pipeline.stored_roots(run['id'])==roots
        status=pipeline.status(run['id'])
        assert status['done']==status['total'] and status['done']>0,status
        assert len(llm.payloads)==calls,'принятые блоки прочитаны и оплачены повторно'


def test_run_without_recoverable_input_is_closed_failed_instead_of_raised_again(tmp_path,monkeypatch):
    """Входа нет — честный `failed`; сторож больше не поднимает этот прогон."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=40))
        run=started(client,url,'lost-input')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        with pipeline.preparation.connection() as db:
            # Снимок входа потерян (старая база/обрыв до записи `resume`), а
            # работа осталась: так выглядит прогон, который сторож поднимал
            # каждую минуту без единого шанса дочитать книгу.
            db.execute('UPDATE creation_runs SET status="interrupted",worker="",heartbeat=0,resume="",result="" WHERE id=?',(run['id'],))
            db.execute('UPDATE creation_parts SET status="queued" WHERE id=?',(first_part(pipeline,run['id']),))
        calls=len(llm.payloads)
        assert asyncio.run(pipeline.resume(run['id'],'тест: вход потерян')) is None
        row=pipeline.raw(run['id'])
        assert row['status']=='failed',row['status']
        assert 'восстановить' in row['message'] and 'Загрузите файл ещё раз' in row['message'],row['message']
        assert len(llm.payloads)==calls,'без входа прогон всё равно сходил в модель'
        # Повторный старт приложения его не трогает: терминальный статус.
        assert asyncio.run(pipeline.resume_pending())==[]
        assert pipeline.raw(run['id'])['status']=='failed'


def test_split_part_without_children_is_read_again_as_an_ordinary_block(tmp_path,monkeypatch):
    """`split` без детей — это потерянная работа, а не сделанная."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','2600')
    llm=SectionFixture(limit=10**9)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=40))
        run=started(client,url,'split-without-children')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        part=first_part(pipeline,run['id'])
        with pipeline.preparation.connection() as db:
            # Обрыв между записью статуса `split` и записью детей.
            db.execute('UPDATE creation_parts SET status="split",children="[]",diagnostic="[]" WHERE id=?',(part,))
        # В счётчике такой кусок — ни сделанный, ни остаток: без приведения к
        # `queued` прогон закрылся бы как «работа кончилась», а блок не прочтён.
        assert pipeline.remaining(run['id'])['resumable'] is False
        seen={}
        real=pipeline.run
        async def fake_run(id,*args):
            seen['status']=pipeline.part(part)['status']
        async def settle():
            pipeline.run=fake_run
            try:
                await pipeline.resume(run['id'],'тест: обрыв между статусом и детьми')
                for _ in range(5):await asyncio.sleep(0)
            finally:
                pipeline.run=real
        asyncio.run(settle())
        assert seen.get('status')=='queued',seen
        # И блок действительно дочитывается, а не остаётся «в очереди» навсегда.
        async def drive():
            await pipeline.resume(run['id'],'тест: дочитать потерянный блок')
            task=pipeline.tasks.get(run['id'])
            if task is not None:await task
        asyncio.run(drive())
        assert pipeline.part(part)['status']=='validated',pipeline.part(part)['status']
        status=pipeline.status(run['id'])
        assert status['done']==status['total'],status


def test_interrupted_parent_with_accepted_children_is_not_counted_as_interrupted(tmp_path,monkeypatch):
    """Работу сделали дети: прерванный родитель не остаток и не «прерванный»."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=SectionFixture(limit=2200)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book())
        run=started(client,url,'split-parent-counted')
        assert wait(client,run['id'])['ready']
        pipeline=client.app.state.preparation_pipeline
        parent=first_part(pipeline,run['id'],with_children=True)
        with pipeline.preparation.connection() as db:
            # Дети приняты, а родитель остался `interrupted` — обрыв между
            # записью детей и записью статуса.
            db.execute('UPDATE creation_parts SET status="interrupted",diagnostic="[]" WHERE id=?',(parent,))
        left=pipeline.remaining(run['id'])
        assert left['pending']==0,left
        assert left['done']==left['total'] and not left['resumable'],left
        assert left['details']==[],left['details']
        # Для контраста: прерванный ЛИСТ — это настоящий остаток работы.
        leaf=first_part(pipeline,run['id'])
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_parts SET status="interrupted",diagnostic="[]" WHERE id=?',(leaf,))
        left=pipeline.remaining(run['id'])
        assert left['pending']==1 and left['resumable'] is True,left
