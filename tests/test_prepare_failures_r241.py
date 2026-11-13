"""r241: полоса подготовки называет отказавший блок и причину словами.

Владелец видел «Готово 9 из 12» и не понимал, ждать или добивать: причина
отказа жила в базе (`creation_parts.diagnostic`), а на экран не попадала.
Здесь проверяется контракт, из которого живёт список под полосой:

1. у каждого отказавшего блока есть номер, страницы и короткая причина
   по-русски — без кода диагностики и без английских полей pydantic;
2. принятые блоки в список не попадают;
3. «добить» спрашивает ровно отказавшие блоки: принятые не перечитываются и
   не оплачиваются.
"""
import json,re,time
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.preparation_pipeline import one_line,refusal_text
from test_preparation_pipeline import new
from test_preparation_sections import book,upload
from test_preparation_resume_r238 import PoisonFixture,started,wait


def test_refusal_text_speaks_russian_not_field_codes():
    """Код диагностики остаётся в `reason`, на экран идёт фраза."""
    assert refusal_text(dict(reason='unknown_source_span',message='Ссылка на несуществующий фрагмент S999'))=='цитата не подтверждена страницей'
    # pydantic-типы приходят английскими именами полей: их нельзя показывать.
    assert refusal_text(dict(reason='missing',message='Field required'))=='ответ модели не по схеме'
    assert refusal_text(dict(reason='string_type',message='Input should be a valid string'))=='ответ модели не по схеме'
    assert refusal_text(dict(reason='section_structure_error',message='Ошибка'))=='структура блока не сошлась'
    assert refusal_text({})=='ответ не прошёл проверку'
    assert refusal_text({},'interrupted')=='разбор прервался, блок не дочитан'
    long=refusal_text(dict(reason='своя_причина',message='а'*400))
    assert '\n' not in long and len(long)<=140 and long.endswith('…'),long


def test_failed_blocks_are_named_with_pages_and_plain_reason(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','1')
    llm=PoisonFixture(marker='Дом2')
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=3,paragraphs=60))
        run=started(client,url,'r241')
        status=wait(client,run['id'])
        assert status['status']=='partial',status
        blocks=status['retry']['blocks']
        assert blocks,status['retry']
        with client.app.state.preparation.connection() as db:
            rows=[dict(row) for row in db.execute(
                'SELECT p.status,p.source,l.ordinal FROM creation_parts p '
                'JOIN creation_part_links l ON l.part=p.id WHERE l.run=?',(run['id'],))]
        failed={int(row['ordinal'])//1000+1 for row in rows if row['status']=='failed'}
        assert {block['block'] for block in blocks}==failed,(blocks,failed)
        # Принятые блоки в списке отказов не появляются: иначе владелец снова
        # не поймёт, что именно не вышло.
        assert len(blocks)==len(failed)==len(status['retry']['blocks'])
        for block in blocks:
            assert block['pages'].startswith(('страница','страницы')),block
            assert block['short'] and '\n' not in block['short'] and len(block['short'])<=140,block
            assert not block['short'].isascii(),block
            assert 'unknown_source_span' not in block['short'] and '_' not in block['short'],block
            assert block['attempts']==3 and block['exhausted'],block

        # «Добить» — только отказавшие блоки. Сравниваем точные наборы страниц
        # секций: принятая секция не должна попасть в повтор ни одним куском.
        spans={}
        for row in rows:
            pages=json.loads(row['source'] or '{}').get('pages') or []
            spans.setdefault(row['status'],set()).add(tuple(sorted(p['page'] for p in pages if isinstance(p.get('page'),int))))
        before=len(llm.payloads)
        client.post('/api/creation/runs/'+run['id']+'/retry',json={}).raise_for_status()
        again=wait(client,run['id'])
        fresh=llm.payloads[before:]
        asked={tuple(sorted(p['page'] for p in payload['pages'] if isinstance(p.get('page'),int))) for payload in fresh}
        assert asked==spans['failed'],(asked,spans)
        assert fresh and len(fresh)==3*len(blocks),(len(fresh),len(blocks))
        assert again['status']=='partial' and again['retry']['blocks'],again
