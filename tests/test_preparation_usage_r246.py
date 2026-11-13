"""r246: сколько стоил разбор книги — счётчик по прогону.

Владелец платит за вызовы модели и хочет видеть цену подготовки, а не
догадываться. Счётчик берётся из леджера расходов по `calls.turn_ref`
(`<id прогона>:<часть>`), поэтому проверяется ровно три вещи:

1. числа совпадают с прямым запросом к базе;
2. отказ и повтор считаются отдельно от успешных вызовов, а отмена игрока
   отказом не считается;
3. прогон без записей в леджере (старая подготовка) отдаёт пустое поле, и
   экран подготовки от этого не ломается.
"""
import json,sqlite3
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.cloud_cost import CostLedger
from solo.preparation_usage import human_tokens,run_usage,usage_text
from test_preparation_pipeline import PreparationFixture,new,terminal

CALLS=('id', 'campaign', 'session', 'turn_ref', 'provider', 'model', 'purpose', 'currency', 'day', 'created',
       'reserved', 'status', 'error_code', 'input_tokens', 'output_tokens', 'retry_of')


def ledger(tmp_path):
    """Леджер с одним прогоном: успех, отказ, повтор отказа и отмена игрока."""
    path=tmp_path/'cloud-usage.sqlite3'
    CostLedger(path)
    rows=[('a1','ok','',None,1000,200),
          ('a2','error','http_502_mirror_down',None,900,0),
          ('a3','ok','','a2',900,150),
          ('a4','cancelled','client_cancelled',None,300,0),
          ('a5','ok','',None,1200,300)]
    with sqlite3.connect(path) as db:
        for call,status,error,retry,input_tokens,output_tokens in rows:
            values=(call,'preparation:draft','session','run-a:'+call,'routerai','deepseek/deepseek-v4.1-flash',
                'preparation_world','RUB','2026-11-06',1.0,0,status,error,input_tokens,output_tokens,retry)
            db.execute('INSERT INTO calls('+','.join(CALLS)+') VALUES('+','.join('?'*len(CALLS))+')',values)
    return path


def test_numbers_match_direct_query_and_refusals_are_counted_apart(tmp_path):
    path=ledger(tmp_path)
    value=run_usage(path,'run-a',blocks=3,total=4)
    with sqlite3.connect(path) as db:
        direct=db.execute("SELECT COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM calls WHERE turn_ref LIKE 'run-a:%'").fetchone()
        refusals=db.execute("SELECT COUNT(*) FROM calls WHERE status IN ('error','invalid_contract','interrupted')").fetchone()[0]
    assert value['calls']==direct[0]==5
    assert value['input_tokens']==direct[1]==4300
    assert value['output_tokens']==direct[2]==650
    # Отказ и повтор — разные числа: повтор после отказа тоже вызов, но не отказ.
    assert value['failed']==refusals==1 and value['retries']==1
    assert value['blocks']==3 and value['total']==4
    assert set(value)=={'calls','input_tokens','output_tokens','failed','retries','blocks','total','text'}
    assert value['text']=='3 блока, 5 вызовов, 4 300 токенов на вход, 1 в отказ'


def test_old_run_without_ledger_records_is_empty_not_an_error(tmp_path):
    path=ledger(tmp_path)
    assert run_usage(path,'run-older-than-the-counter',blocks=128,total=128) is None


def test_missing_ledger_or_run_id_is_empty_not_a_crash(tmp_path):
    assert run_usage(tmp_path/'not-created-yet.sqlite3','run-a') is None
    assert run_usage(None,'run-a') is None and run_usage(tmp_path,'') is None


def test_line_reads_like_the_card(tmp_path):
    assert usage_text(dict(blocks=128,calls=95,input_tokens=1300000,failed=0))=='128 блоков, 95 вызовов, 1,3 млн токенов на вход'
    assert usage_text(dict(calls=1,input_tokens=700))=='1 вызов, 700 токенов на вход'
    assert human_tokens(6461237)=='6,5 млн' and human_tokens(189081)=='189 081'


def test_api_reports_usage_and_survives_a_run_without_records(tmp_path):
    llm=PreparationFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client)
        started=client.post(url+'/prepare',json=dict(request_id='usage',consent_to_send=True,ruleset='dnd2014')).json()
        status=terminal(client,started['id'])
        assert status['status']=='ready' and status['usage'] is None
        # MockLLM в леджер не пишет: старая подготовка отдаёт пустое поле, экран живёт.
        view=client.get('/api/creation/runs/'+started['id']).json()
        assert view['usage'] is None and view['ready']
        # Появилась запись по этому прогону — счётчик говорит те же числа, что база.
        path=client.app.state.preparation_pipeline.ledger_path
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO calls('+','.join(CALLS)+') VALUES('+','.join('?'*len(CALLS))+')',
                ('c1','preparation:'+draft['id'],'session',started['id']+':part-1','routerai','deepseek/deepseek-v4.1-flash',
                 'preparation_world','RUB','2026-11-06',1.0,0,'ok','',1500,320,None))
        view=client.get('/api/creation/runs/'+started['id']).json()
        assert view['usage']['calls']==1 and view['usage']['input_tokens']==1500
        assert view['usage']['blocks']==view['done']==status['done']
        assert view['usage']['text'].startswith(str(view['done'])+' блок')
        assert view['usage']['text'].endswith(', 1 вызов, 1 500 токенов на вход')
        # Ни ключей, ни адресов провайдера: наружу уходят только числа и фраза.
        assert 'routerai' not in json.dumps(view['usage']) and 'http' not in json.dumps(view['usage'])
