import io,json,copy,sqlite3
from pathlib import Path
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.preparation import Preparation,Draft,extract
from solo.storage import Store
from solo.game import SceneCompiler
from solo.user_data import backup

MARKUP='''## Старт: Перекрёсток
Перед вами лесная дорога. У столба ждёт проводница.
## NPC: Ирина @ Перекрёсток
Описание: Женщина в дорожном плаще.
Секрет: ЗАПРЕТНЫЙ_ПАРОЛЬ и тайное поручение.
## Локация: Убежище злодея
СКРЫТЫЙ_ЗЛОДЕЙ ожидает здесь. Это не известно герою.
## Дорога: Перекрёсток -> Убежище злодея
{"meters":100,"minutes":3,"description":"лесная тропа","known":true,"explorable":true}
## Событие: откроется-позже
{"after_minutes":100,"kind":"reveal","target":"Убежище злодея","text":"Найдено убежище."}
'''
def prepared(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');prep=Preparation(tmp_path,store);d=prep.create()
    d=prep.add_source(d['id'],'тайный-злодей.md',MARKUP.encode());body=Draft.model_validate(d['body']);body.name='Тестовая хроника';body.source_kind='ready';body.prepared=True;body.background_confirmed=True;body.hero=Hero(name='Лира',background='Я ищу пропавшего гонца.',languages=['Общий']).model_dump();body.equipment=['rope'];d=prep.save(d['id'],body,d['revision']);return store,prep,d

def test_markup_creates_private_preparation_and_safe_campaign_idempotently(tmp_path):
    store,prep,d=prepared(tmp_path);assert d['sources'][0]['quality']['npc']==1
    raw=json.dumps(d,ensure_ascii=False);assert 'ЗАПРЕТНЫЙ_ПАРОЛЬ' not in raw and 'Убежище злодея' not in raw and 'тайный-злодей' not in raw
    c=prep.launch(d['id'],True);assert c['state']['location']=='Перекрёсток' and len(c['state']['locations'])==2
    assert prep.launch(d['id'],True)['id']==c['id'] and len(store.list_campaigns())==1
    with TestClient(create_app(store.path)) as client:
        public=json.dumps(client.get('/api/campaigns/'+c['id']).json(),ensure_ascii=False)
        assert 'ЗАПРЕТНЫЙ_ПАРОЛЬ' not in public and 'СКРЫТЫЙ_ЗЛОДЕЙ' not in public and 'Убежище злодея' not in public
    assert prep.retrieve(c['id'],'ЗАПРЕТНЫЙ_ПАРОЛЬ',audience='gm')
    assert not prep.retrieve(c['id'],'ЗАПРЕТНЫЙ_ПАРОЛЬ',audience='hero')
    scene=json.dumps(SceneCompiler(store).interpreter(c),ensure_ascii=False)
    assert 'ЗАПРЕТНЫЙ_ПАРОЛЬ' not in scene and 'Убежище злодея' not in scene
    assert 'Ирина' in scene and c['state']['hero']['background']=='Я ищу пропавшего гонца.'
    dest=backup(tmp_path,'prepared-test');assert (dest/'preparation.sqlite3').exists()
    with sqlite3.connect(dest/'preparation.sqlite3') as db:assert db.execute('SELECT count(*) FROM chunks').fetchone()[0]>0

def test_failed_pdf_never_claims_read_and_launch_requires_all_consents(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');prep=Preparation(tmp_path,store);d=prep.create();writer=PdfWriter();writer.add_blank_page(200,200);raw=io.BytesIO();writer.write(raw)
    d=prep.add_source(d['id'],'scan.pdf',raw.getvalue());assert d['sources'][0]['quality']['status']=='failed'
    with pytest.raises(ValueError):prep.launch(d['id'],False)
    body=Draft.model_validate(d['body']);body.prepared=True;body.background_confirmed=True;body.hero=Hero(name='Лира').model_dump();body.opening='Дорога.';d=prep.save(d['id'],body,d['revision'])
    with pytest.raises(ValueError,match='не прочитан'):prep.launch(d['id'],True)
    assert not store.list_campaigns()

def test_real_pdf_text_extraction_and_page_provenance(tmp_path):
    writer=PdfWriter();page=writer.add_blank_page(200,200);font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')});page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})});stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 150 Td (This is a real text source with a location and an old bridge.) Tj ET');page[NameObject('/Contents')]=writer._add_object(stream);raw=io.BytesIO();writer.write(raw)
    pages,quality,pkg=extract('source.pdf',raw.getvalue());assert quality['status']=='usable' and pages[0][0]==1 and 'old bridge' in pages[0][1] and pkg is None
    store=Store(tmp_path/'solo.sqlite3');p=Preparation(tmp_path,store);d=p.create();d=p.add_source(d['id'],'source.pdf',raw.getvalue());assert d['sources'][0]['quality']['characters']>40

def test_draft_conflict_unknown_item_and_bad_sources_never_create_partial_campaign(tmp_path):
    store,p,d=prepared(tmp_path);before=copy.deepcopy(d);body=Draft.model_validate(d['body']);body.equipment=['invented'];d=p.save(d['id'],body,d['revision'])
    with pytest.raises(ValueError):p.save(d['id'],body,before['revision'])
    with pytest.raises(ValueError,match='экипировка'):p.launch(d['id'],True)
    assert not store.list_campaigns()
    with pytest.raises(ValueError):p.add_source(d['id'],'script.exe',b'x'*100)
    with pytest.raises(ValueError):p.add_source(d['id'],'bad.json',b'{"secret":"HIDDEN"}')
    duplicate=p.add_source(d['id'],'copy.md',MARKUP.encode());assert len(duplicate['sources'])==1

def test_api_persists_unfinished_hero_and_derives_custom_rules_values(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        options=client.get('/api/preparation/options').json();d=client.post('/api/preparation/drafts').json();d['body']['hero']={**options['hero'],'name':'Незавершённый герой','subclass':'Ручной подкласс'}
        saved=client.put('/api/preparation/drafts/'+d['id'],json={'body':d['body'],'revision':0});assert saved.status_code==200
        sheet=client.post('/api/preparation/drafts/'+d['id']+'/sheet',json={});assert sheet.status_code==200 and sheet.json()['initiative_bonus']==2
        assert client.get('/api/preparation/drafts/'+d['id']).json()['body']['hero']['subclass']=='Ручной подкласс'
        assert client.post('/api/preparation/drafts/'+d['id']+'/launch',json={'confirm':True}).status_code==409
        assert client.get('/api/campaigns').json()==[]

def test_excluded_bad_source_retains_provenance_and_rules_version_is_pinned(tmp_path):
    store,p,d=prepared(tmp_path);writer=PdfWriter();writer.add_blank_page(200,200);raw=io.BytesIO();writer.write(raw)
    d=p.add_source(d['id'],'scan.pdf',raw.getvalue());bad=next(s for s in d['sources'] if s['quality']['status']=='failed');d=p.exclude_source(d['id'],bad['id'])
    assert len(d['sources'])==1 and not d['body']['prepared']
    with p.connection() as c:assert c.execute('SELECT length(original) FROM sources WHERE id=?',(bad['id'],)).fetchone()[0]==len(raw.getvalue())
    from solo.rulesets import RulesetRepository
    library=RulesetRepository(store);preset=library.get('dnd2024').model_copy(update={'id':'versioned-test','edition':'custom','name':'Авторский пресет'});library.save(preset)
    body=Draft.model_validate(d['body']);body.prepared=True;body.ruleset='versioned-test';body.ruleset_version=1;d=p.save(d['id'],body,d['revision']);assert d['body']['ruleset_version']==1
    library.save(preset.model_copy(update={'proficiency_by_level':[5]*20}))
    created=p.launch(d['id'],True);assert created['state']['ruleset']['version']==1 and created['state']['ruleset']['proficiency_by_level'][0]==2

def test_pdf_decompression_limit_prevents_large_stream_before_indexing(tmp_path):
    writer=PdfWriter();page=writer.add_blank_page(200,200);stream=DecodedStreamObject();stream.set_data(b' '*(13*1024*1024));page[NameObject('/Contents')]=writer._add_object(stream.flate_encode());raw=io.BytesIO();writer.write(raw)
    p=Preparation(tmp_path,Store(tmp_path/'solo.sqlite3'));d=p.create()
    with pytest.raises(ValueError):p.add_source(d['id'],'compressed.pdf',raw.getvalue())
    assert p.get(d['id'])['sources']==[]
