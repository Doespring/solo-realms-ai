"""r237: глава 1 открывает игру, пока остальная книга ещё читается.

Проверяется то, ради чего вход отдаётся раньше полного мира:

* `playable` становится истинным, пока прогон ещё `running`, и в этот момент
  видно честные числа «готово N из M»;
* мир в этот момент можно подтвердить (кнопка «Начать» не заперта), хотя
  книга разобрана не вся;
* полный мир доезжает следом и закрывает прогон уже как `ready`.

Модель синтетическая: ни одного платного вызова. Книга — свой текст с
read-aloud строками и двумя местами на главу, предел секции задан явно,
чтобы блоков было заведомо несколько.
"""
import asyncio,json,re,time
from contextlib import nullcontext

from fastapi.testclient import TestClient

from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.llm import MockLLM


def book_text(chapters=14,lines=60):
    """Главы с двумя местами каждая: текст читается и делится на секции."""
    out=[]
    for chapter in range(chapters):
        out.append(f'## Глава {chapter+1}')
        for name in ('Село','Башня'):
            for number in range(lines):
                out.append(f'Read aloud: {name} стоит на дороге, строка {number}.')
    return '\n'.join(out)+'\n'


class PlayableFixture(MockLLM):
    """Мир по каждой секции: места из её страниц и дорога между первыми двумя."""

    def __init__(self,delay=0.5):
        super().__init__();self.delay=delay;self.payloads=[]

    def configured(self):return routerai_preset(),'fixture-only-never-sent'

    def scope(self,*args):return nullcontext()

    def contract_error(self,code):self.metrics.append(dict(status='invalid_contract',error=code))

    @staticmethod
    def chunks(pages):
        """Куски источника с их идентификаторами: [S001] текст."""
        for page in pages:
            for marker,quote in re.findall(r'\[(S\d+)\]\s*(.*?)(?=\[S\d+\]|\Z)',page['text'],re.S):
                yield marker,quote

    async def stream(self,system,user,**kwargs):
        assert kwargs['mode']=='preparation_world';payload=json.loads(user);self.payloads.append(payload)
        self.metrics.append(dict(mode='preparation_world',mock=True,calls=1))
        await asyncio.sleep(self.delay)
        index=int(payload.get('batch') or 0)
        pieces=list(self.chunks(payload['pages']))
        names=[]
        for _,quote in pieces:
            for name in re.findall(r'(?:Село|Башня)',quote):
                if name not in names:names.append(name)
        def cited(name):
            for marker,quote in pieces:
                if name in quote:return dict(source_span=marker)
            return None
        entities=[]
        for name in names:
            proof=cited(name)
            entities.append(dict(key=name,kind='location',data=dict(name=name,description=f'{name} стоит на дороге, строка 0.'),
                evidence=[proof] if proof else []))
        if 'Село' in names and 'Башня' in names:
            proof=cited('Село')
            # Числа перехода не выдумываются: книгу не цитируют метрами, и
            # проверка справедливо отказывает «числу без цитаты». Мир сам
            # примет минимальный шаг.
            entities.append(dict(key='road',kind='route',data={
                'from':'Село','to':'Башня',
                'description':'Дорога между местами','explorable':True},evidence=[proof] if proof else []))
        start='Село' if 'Село' in names else (names[0] if names else '')
        value=dict(title='Синтетическая книга',ruleset='dnd2014',start=start,entities=entities)
        raw=json.dumps(value,ensure_ascii=False)
        for offset in range(0,len(raw),31):
            yield raw[offset:offset+31];await asyncio.sleep(0)


def run_view(client,run):
    return client.get('/api/creation/runs/'+run).json()


def wait(client,run,deadline=90):
    end=time.monotonic()+deadline
    while time.monotonic()<end:
        value=run_view(client,run)
        if value['status'] not in ('queued','running'):return value
        time.sleep(.02)
    raise AssertionError('Подготовка не завершилась')


def test_chapter_one_opens_the_game_before_the_book_is_finished(tmp_path,monkeypatch):
    # Несколько блоков при короткой книге: предел задаётся явно, чтобы тест
    # проверял вход по главе 1, а не размер секции.
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','8000')
    llm=PlayableFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        client.put(url,json=dict(description='Синтетическая книга, D&D 2014',revision=draft['revision'])).raise_for_status()
        client.post(url+'/files',files={'file':('kniga.txt',book_text().encode())}).raise_for_status()
        end=time.monotonic()+30
        while any(f['job']['status']!='complete' for f in client.get(url).json()['files']):
            assert time.monotonic()<end;time.sleep(.02)
        started=client.post(url+'/prepare',json=dict(request_id='r237-playable',world_structure=True,
            ruleset='dnd2014',consent_to_send=True))
        assert started.status_code==200,started.text
        run=started.json()
        # Ждём именно промежуточное состояние: прогон ещё идёт, а вход уже открыт.
        opened=None;end=time.monotonic()+90
        while time.monotonic()<end:
            view=run_view(client,run['id'])
            if view.get('playable') and view['status']=='running':opened=view;break
            assert view['status'] in ('queued','running'),view
            time.sleep(.02)
        assert opened is not None,'вход не открылся, пока книга читалась'
        assert opened['total']>2,opened
        assert 0<opened['done']<opened['total'],opened
        assert 'Можно играть' in opened['message'] and f"готово {opened['done']} из {opened['total']}" in opened['message']
        # Кнопка «Начать» не заперта: мир подтверждается прямо во время чтения.
        readiness=client.get(url+'/world-readiness')
        assert readiness.status_code==200,readiness.text
        body=readiness.json();assert body['world_ready'],body
        confirmed=client.post(url+'/confirm-world',json=dict(expected_revision=body['revision'],
            run_id=body['run_id'],confirm=True))
        assert confirmed.status_code==200,confirmed.text
        assert client.get(url).json()['preparation']['playable'] is True
        # Книга дочитывается, и полный мир закрывает прогон уже как ready.
        final=wait(client,run['id'])
        assert final['status']=='ready',final
        assert final['done']==final['total']
