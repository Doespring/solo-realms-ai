"""r236: готовые миры — пакет .soloworld.json, библиотека, кэш по отпечатку.

Проверяем то, за что платит игрок: мир, разобранный один раз, поднимается из
файла без единого вызова модели; битый файл объясняет причину, а не отдаёт
500; та же книга во втором черновике не оплачивается второй раз.
"""
import json,os,sqlite3,time
os.environ.setdefault('SOLO_TEST','1')
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.llm import MockLLM
from solo.storage import Store
from solo.world import ensure,observe,project
from solo.world_service import (WORLD_SUFFIX,WorldCommand,WorldPackageProblem,WorldService,
    export_package,import_world_package,library,library_package,system_label,validate_package)

FOOT='Переправа'


def world_campaign(store,name='Письмо у фонаря'):
    """Кампания Nat20 с готовым миром: два места, дорога, НИП и часы мира.

    Ровно такой мир собирает запуск подготовленного приключения (локации + узлы
    карты + дороги + наблюдения). Здесь он записан прямо в состояние, чтобы герой
    и движок остались настоящими: пакет должен переносить живую кампанию, а не
    тестовую заглушку.
    """
    from solo.nat20_api import Nat20GameEngine
    campaign=Nat20GameEngine(store).create_campaign(name)['id']
    with store.connection(True) as db:
        row=db.execute('SELECT state FROM campaigns WHERE id=?',(campaign,)).fetchone()
        state=json.loads(row['state']);start=state['location']
        state['locations']=[dict(name=start,description='Дозор на тракте: снег, фонарь и молчаливая стража.',
                known_to_hero=True,npc=[dict(name='Проводница',character='Знает дорогу, но не любит вопросы.')]),
            dict(name=FOOT,description='Узкий мост через холодную реку.',known_to_hero=True)]
        state['geography']=dict(
            nodes=[dict(id='start',name=start,coordinates=[1,1],known=True),
                   dict(id='perev',name=FOOT,coordinates=[2,3],known=True)],
            routes=[dict(**{'from':'start'},to='perev',meters=300,minutes=5,
                description='Дорога к мосту',known=True,modes=['story','sandbox','mixed'])])
        state['world']=dict(settings=dict(mode='sandbox',map_travel=True,width=12,height=8))
        ensure(state);observe(state)
        db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),campaign))
    return campaign


def travelled_world(store,campaign):
    """Пройденный путь: часы мира и «посещено» тоже должны пережить перенос."""
    WorldService(store).execute(campaign,WorldCommand(request_id='walk',kind='travel',target=FOOT))
    return campaign


def test_exported_world_imports_as_the_same_world(tmp_path):
    store=Store(tmp_path/'solo.sqlite3')
    campaign=travelled_world(store,world_campaign(store))
    before=store.campaign(campaign)['state']

    package=export_package(store,campaign)
    assert package['format']=='soloworld' and package['format_version']==1
    assert package['short_name'] and package['created_at'] and package['system']['id']=='dnd2024'
    # Книга в этом мире не разбиралась: отпечатка нет, и пакет от этого не битый.
    assert package['book']['fingerprint']==''
    assert package['counts']==dict(locations=2,routes=1,npcs=1)
    validate_package(package) # пакет, собранный приложением, проходит свою же проверку

    imported=import_world_package(store,json.loads(json.dumps(package)))
    after=store.campaign(imported['id'])['state']
    assert imported['id']!=campaign and imported['backend']=='nat20'
    assert after['nat20']['definition']==before['nat20']['definition']
    assert after['location']==before['location']==FOOT and after['turn']==0
    assert [place['name'] for place in after['locations']]==[place['name'] for place in before['locations']]
    assert [place['description'] for place in after['locations']]==[place['description'] for place in before['locations']]
    assert after['geography']==before['geography'] and after['world']['settings']==before['world']['settings']
    assert after['world']['visited']==before['world']['visited'] and after['elapsed_minutes']==before['elapsed_minutes']==5
    assert after['hero']['name']==before['hero']['name'] and after['hero']['hp']==before['hero']['hp']
    assert project(after)==project(before) # карта и знания героя совпадают целиком
    # Черновик не переезжает: импорт не выглядит как «эта кампания начата из X».
    assert 'draft' not in (after.get('preparation') or {}) and after['preparation']['source']=='world-package'


def test_package_keeps_the_fingerprint_of_the_source_book(tmp_path):
    """Пакет помнит книгу, из которой собран мир: отпечаток прогона подготовки.

    Отпечаток читается из `creation_runs` только на чтение — по нему вторая
    машина понимает, что эта книга уже разобрана, и не платит за разбор снова.
    """
    store=Store(tmp_path/'solo.sqlite3')
    campaign=world_campaign(store)
    with store.connection(True) as db:
        row=db.execute('SELECT state FROM campaigns WHERE id=?',(campaign,)).fetchone()
        state=json.loads(row['state']);state['preparation']=dict(draft='draft-forge',source_ruleset='dnd2014')
        db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),campaign))
    runs=sqlite3.connect(tmp_path/'preparation.sqlite3')
    runs.execute('CREATE TABLE creation_runs(id TEXT PRIMARY KEY,draft TEXT NOT NULL,request TEXT NOT NULL,'
        'fingerprint TEXT NOT NULL,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,'
        'message TEXT NOT NULL DEFAULT "",result TEXT,created REAL NOT NULL,book TEXT NOT NULL DEFAULT "")')
    runs.execute('INSERT INTO creation_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',('run-1','draft-forge','req-1','sha256:книга-кузница',
        'ready',3,3,'',json.dumps(dict(title='Кузница',start_contract=dict(documents=['a','b']))),1.0,''))
    runs.commit();runs.close()

    package=export_package(store,campaign)
    assert package['book']['fingerprint']=='sha256:книга-кузница'
    assert package['book']['title']=='Кузница' and package['book']['documents']==2
    # книга 2014 при механике 2024 называется честно, а не выдаётся за 2024
    assert package['system']['id']=='dnd2024' and 'книга D&D 2014' in system_label(package)
    imported=import_world_package(store,json.loads(json.dumps(package)))
    after=store.campaign(imported['id'])['state']
    assert after['preparation']['book']['fingerprint']=='sha256:книга-кузница'


def test_package_limits_stop_a_huge_file(tmp_path,monkeypatch):
    """Пределы пакета: слишком много мест и слишком большой файл — отказ с причиной."""
    store=Store(tmp_path/'solo.sqlite3')
    good=json.loads(json.dumps(export_package(store,travelled_world(store,world_campaign(store)))))
    huge=json.loads(json.dumps(good))
    huge['world']['locations']=[dict(name='Место '+str(index)) for index in range(401)]
    huge['world']['location']='Место 0';huge['world']['geography']=dict(nodes=[],routes=[])
    with pytest.raises(WorldPackageProblem) as error:import_world_package(store,huge)
    assert 'больше 400 мест' in str(error.value)
    folder=tmp_path/'worlds';folder.mkdir()
    (folder/('большой'+WORLD_SUFFIX)).write_bytes(b'x'*(1024*1024+10))
    monkeypatch.setattr('solo.world_service.WORLD_MAX_BYTES',1024*1024)
    rows=library(store)
    assert rows[0]['broken'] and 'больше 1 МБ' in rows[0]['problem']
    with pytest.raises(WorldPackageProblem):library_package(store,'большой'+WORLD_SUFFIX)


def test_import_broken_package_names_the_reason_and_never_answers_500(tmp_path):
    store=Store(tmp_path/'solo.sqlite3')
    good=json.loads(json.dumps(export_package(store,travelled_world(store,world_campaign(store)))))
    broken=[]
    broken.append(({**good,'format':'other'},'не пакет мира'))
    broken.append(({**good,'format_version':99},'более новой версией'))
    broken.append(({key:value for key,value in good.items() if key!='world'},'нет мира'))
    broken.append(({**good,'world':{**good['world'],'locations':[]}},'ни одного места'))
    broken.append(({**good,'world':{**good['world'],'location':'Нигде'}},'отсутствует среди мест'))
    broken.append(({**good,'characters':{'hero':{}}},'нет состояния героя'))
    broken.append(({**good,'world':{**good['world'],'geography':{'nodes':[],'routes':[
        {'from':'x','to':'y','meters':1,'minutes':1,'description':'Дорога в никуда'}]}}},'ведёт в место, которого нет'))
    duplicated=json.loads(json.dumps(good));duplicated['world']['locations'].append(dict(duplicated['world']['locations'][0]))
    broken.append((duplicated,'повторяются места'))
    for raw,expected in broken:
        with pytest.raises(WorldPackageProblem) as error:import_world_package(store,raw)
        assert expected in str(error.value),str(error.value)
    assert len(store.list_campaigns())==1 # отклонённый пакет не создаёт кампанию
    assert 'JSON-объектом' in str(pytest.raises(WorldPackageProblem,import_world_package,store,'не json').value)
    with TestClient(create_app(tmp_path/'solo.sqlite3',MockLLM())) as client:
        response=client.post('/api/worlds/import',content=b'{"format":"soloworld",')
        assert response.status_code==409 and 'JSON не разбирается' in response.json()['detail']
        response=client.post('/api/worlds/import',json={**good,'format_version':99})
        assert response.status_code==409 and 'более новой версией' in response.json()['detail']
        response=client.post('/api/worlds/import',content=b'\xff\xfe\x00')
        assert response.status_code==409 and 'UTF-8' in response.json()['detail']
        assert client.get('/api/worlds/../../etc/passwd').status_code in (404,405)


def test_http_export_import_and_library_round_trip(tmp_path):
    store=Store(tmp_path/'solo.sqlite3')
    campaign=travelled_world(store,world_campaign(store))
    with TestClient(create_app(tmp_path/'solo.sqlite3',MockLLM())) as client:
        exported=client.get('/api/campaigns/'+campaign+'/world/export')
        assert exported.status_code==200
        disposition=exported.headers['content-disposition']
        assert 'attachment' in disposition and WORLD_SUFFIX in disposition
        package=exported.json();assert package['format']=='soloworld' and package['world']['location']==FOOT
        imported=client.post('/api/worlds/import',json=package)
        assert imported.status_code==200,imported.text
        created=imported.json();assert created['id']!=campaign and created['backend']=='nat20'
        # загруженный мир открывается как обычная кампания: экран и мир отвечают
        assert client.get('/api/campaigns/'+created['id']).status_code==200
        assert client.get('/api/campaigns/'+created['id']+'/world').json()['current']==FOOT
        saved=client.post('/api/campaigns/'+campaign+'/world/save',json={}).json()
        assert saved['file'].endswith(WORLD_SUFFIX) and saved['size']>0 and saved['system'].startswith('D&D 2024')
        assert saved['folder']=='runtime/worlds' and saved['counts']['locations']==2
        listing=client.get('/api/worlds').json()
        assert [row['file'] for row in listing['worlds']]==[saved['file']]
        assert listing['worlds'][0]['name'] and listing['worlds'][0]['created_at'] and not listing['worlds'][0]['broken']
        played=client.post('/api/worlds/'+saved['file']+'/play')
        assert played.status_code==200,played.text
        assert played.json()['world']['location']==FOOT
        assert client.get('/api/worlds/'+saved['file']+'/download').status_code==200
        assert client.post('/api/worlds/not-a-package.soloworld.json/play').status_code==404
    stored=library(store)
    assert stored and stored[0]['file']==saved['file'] and stored[0]['counts']['locations']==2


def test_broken_file_in_the_library_is_listed_with_its_reason(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');folder=tmp_path/'worlds';folder.mkdir()
    (folder/('порча'+WORLD_SUFFIX)).write_text('{',encoding='utf-8')
    rows=library(store)
    assert rows[0]['broken'] and 'JSON не разбирается' in rows[0]['problem']
    with TestClient(create_app(tmp_path/'solo.sqlite3',MockLLM())) as client:
        assert client.get('/api/worlds').json()['worlds'][0]['broken'] is True
        # Битый файл в библиотеке не запускается, и причина — та же, что в списке.
        broken_play=client.post('/api/worlds/'+('порча'+WORLD_SUFFIX)+'/play')
        assert broken_play.status_code==409 and 'JSON не разбирается' in broken_play.json()['detail']
        assert client.post('/api/worlds/'+('нет-такого'+WORLD_SUFFIX)+'/play').status_code==404


def book_draft(client,description='Книга про кузницу, D&D 2014'):
    """Черновик с книгой: id документа — хэш содержимого, поэтому книга одна и та же."""
    from scripts.smoke_fixture import smoke_pdf_bytes
    draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
    client.put(url,json=dict(description=description,revision=draft['revision'])).raise_for_status()
    uploaded=client.post(url+'/files',files={'file':('synthetic-forge.pdf',smoke_pdf_bytes(),'application/pdf')})
    assert uploaded.status_code==200,uploaded.text
    job=uploaded.json()['files'][0]['job']['id'];deadline=time.monotonic()+60
    while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':
        assert time.monotonic()<deadline,client.get('/api/adventure/jobs/'+job).json();time.sleep(.02)
    return url


def prepare_world(client,url,request_id):
    started=client.post(url+'/prepare',json=dict(request_id=request_id,ruleset='dnd2014',
        world_structure=True,consent_to_send=True))
    assert started.status_code==200,started.text
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        value=client.get('/api/creation/runs/'+started.json()['id']).json()
        if value['status'] not in ('queued','running'):return value
        time.sleep(.03)
    raise AssertionError(value)


def test_same_book_is_not_prepared_twice(tmp_path):
    """Вторая загрузка той же книги поднимает готовый разбор, а не новый счёт.

    Мок считает СВОИ запросы (`pdf_smoke_packets` — журнал HTTP, ушедших в
    MockTransport): у повторного черновика их должно быть ноль.
    """
    from scripts.smoke_fixture import make_pdf_world_smoke_llm
    model=make_pdf_world_smoke_llm(tmp_path,smoke_test=True,phase='prepare')
    calls=lambda:sum(row['kind']=='preparation_world' for row in model.pdf_smoke_packets)
    with TestClient(create_app(tmp_path/'solo.sqlite3',model,experimental=True,offline_mock=True)) as client:
        first_url=book_draft(client)
        first=prepare_world(client,first_url,'first')
        assert first['ready'],first
        assert first['preparation_mode']=='world_structure'
        assert calls()==1 and first['reused'] is False
        # Та же книга, другой черновик и другое описание: модель не должна зваться.
        second_url=book_draft(client,'Тот же текст, другой замысел')
        second=prepare_world(client,second_url,'second')
        assert second['reused'] is True and second['status']=='ready',second
        assert 'Эта книга уже разобрана' in second['message']
        assert calls()==1,model.pdf_smoke_packets
        assert second['summary']==first['summary'] and second['total']==first['total']
        # мир второго черновика подтверждается и открывает кампанию без нового разбора
        readiness=client.get(second_url+'/world-readiness').json()
        assert readiness['world_ready'] or readiness['ready'],readiness
        confirmed=client.post(second_url+'/confirm-world',json=dict(
            expected_revision=readiness['revision'],run_id=readiness['run_id'],confirm=True))
        assert confirmed.status_code==200,confirmed.text
        assert calls()==1
