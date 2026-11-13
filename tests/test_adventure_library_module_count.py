import json
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.adventure_library import AdventureLibrary

def test_library_card_counts_modules_from_the_table_not_the_import_message(tmp_path):
    # The import writes «Модулей: 1» into the job message and stops there: a person
    # then splits, re-splits and deletes modules, and the message keeps its snapshot.
    # The card has to answer with what the library holds now, not with that sentence.
    library=AdventureLibrary(tmp_path);bundle=library.create('Сборник из трёх приключений')
    with library.db() as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',('doc-1','.pdf',1000,'extracted',9,'{}'))
        db.execute('INSERT INTO jobs(id,bundle,document,original_name,purpose,status,created,message) VALUES(?,?,?,?,?,?,?,?)',('job-1',bundle['id'],'doc-1','сборник.pdf','book','complete',0.0,'Извлечено страниц: 9. Модулей: 1.'))
        for number in range(3):
            body=dict(id='module-'+str(number),title='Приключение '+str(number),reviewed=False,unsupported=[],ruleset='dnd2024',ruleset_version='5.2.1')
            db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)',(bundle['id'],body['id'],json.dumps(body)))
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        card=next(row for row in client.get('/api/adventure/library').json() if row['id']==bundle['id'])
    assert 'Модулей: 1' in card['jobs'][0]['message'],card['jobs']
    assert card['modules']==3,card
    # The count is asked of the table at answer time, so a later edit moves it.
    with library.db() as db:db.execute('DELETE FROM adventure_modules WHERE bundle=? AND id=?',(bundle['id'],'module-2'))
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        card=next(row for row in client.get('/api/adventure/library').json() if row['id']==bundle['id'])
    assert (card['modules'],card['jobs'][0]['message'].endswith('Модулей: 1.'))==(2,True),card
