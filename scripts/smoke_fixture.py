"""Static responses exclusively for --smoke-test; no inference or network."""
import asyncio,json
from contextlib import nullcontext
from solo.llm import MockLLM
from solo.ai_roles import routerai_preset

class DesktopFixture(MockLLM):
    def configured(self):return routerai_preset(),'synthetic-smoke-no-provider'
    def scope(self,*args):return nullcontext()
    def contract_error(self,code):self.metrics.append(dict(mock=True,status='invalid_contract',error=code))
    async def stream(self,system,user,**kwargs):
        mode=kwargs.get('mode')
        if mode!='preparation_world':
            async for chunk in super().stream(system,user,**kwargs):yield chunk
            return
        payload=json.loads(user);edition=payload.get('ruleset','dnd2014')
        self.metrics.append(dict(mode=mode,mock=True));await asyncio.sleep(.2)
        if mode=='preparation_world':
            if payload.get('pages'):raise ValueError('Static EXE fixture supports original synthetic descriptions only')
            result=dict(title='EXE smoke · Северный караван',ruleset=edition,start='Перекрёсток',entities=[
                dict(key='crossroads',kind='location',origin='original',data=dict(name='Перекрёсток',description='Снег укрывает дорогу. У столба ждёт проводница.')),
                dict(key='tower',kind='location',origin='original',data=dict(name='Башня',description='Старая башня над рекой.',secrets=['SYNTHETIC_GM_PRIVATE'])),
                dict(key='guide',kind='npc',location='Перекрёсток',origin='original',data=dict(name='Ирина',character='Проводница в плаще.')),
                dict(key='door',kind='object',location='Перекрёсток',origin='original',data=dict(id='smoke-door',name='дверь',kind='door',lock='unlocked',description='Дверь дорожного поста.')),
                dict(key='road',kind='route',origin='original',data={'from':'Перекрёсток','to':'Башня','meters':1000,'minutes':20,'description':'Дорога на север','explorable':True})])
        else:
            # The manual character draft was removed: the single gameplay path
            # builds the hero from prepared materials, not from a free-form sheet.
            raise ValueError('Static fixture does not build a manual character draft')
        raw=json.dumps(result,ensure_ascii=False)
        for offset in range(0,len(raw),64):yield raw[offset:offset+64];await asyncio.sleep(0)


PDF_SMOKE_OPENING='You stand in the Forge. Dorn, a smith, waits here.'
PDF_SMOKE_KNOWLEDGE='The bridge is closed after the rain.'
_PDF_SMOKE_SOURCE=('D&D 2014. Forge and Tower.', 'Read aloud: '+PDF_SMOKE_OPENING,
    'Dorn is in Forge. Dorn knows: '+PDF_SMOKE_KNOWLEDGE,
    'Read aloud: The Tower rises beyond the path.',
    'Path: Forge to Tower, 10 meters, 1 minute.', 'GM_SECRET_KEEP_PRIVATE')


def smoke_pdf_bytes():
    """Real synthetic source PDF. No prepared-world/database injection."""
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject()
    stream.set_data(('BT /F1 11 Tf 30 750 Td 16 TL '+' '.join('('+line.replace('(','\\(').replace(')','\\)')+') Tj T*' for line in _PDF_SMOKE_SOURCE)+' ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output);return output.getvalue()


def _pdf_smoke_proposal(packet):
    import re
    pages=packet.get('pages',[])
    text='\n'.join(p.get('text','') for p in pages)
    spans=re.findall(r'\[(S\d{3,4})\]',text)
    if packet.get('ruleset')!='dnd2014' or len(pages)!=1 or len(set(spans))!=1 or not all(line in text for line in _PDF_SMOKE_SOURCE):
        raise ValueError('Synthetic PDF smoke source/spans mismatch')
    proof=[dict(source_span=spans[0])]
    return dict(title='Forge and Tower',ruleset='dnd2014',start='Forge',entities=[
        dict(key='forge',kind='location',data=dict(name='Forge',description=PDF_SMOKE_OPENING,secrets=['GM_SECRET_KEEP_PRIVATE']),evidence=proof),
        dict(key='tower',kind='location',data=dict(name='Tower',description='The Tower rises beyond the path.'),evidence=proof),
        dict(key='dorn',kind='npc',location='Forge',data=dict(name='Dorn',character='a smith',known=PDF_SMOKE_KNOWLEDGE),evidence=proof),
        dict(key='path',kind='route',data={'from':'Forge','to':'Tower','meters':10,'minutes':1,'description':'Path'},evidence=proof)])


def make_pdf_world_smoke_llm(data,*,smoke_test=False,phase='prepare'):
    """Opt-in new isolated smoke profile; ordinary player/offline/cloud unchanged.

    Host additionally validates its successful prepare marker before continue.
    Every HTTP call terminates in MockTransport, including rejected requests.
    """
    if smoke_test is not True or phase not in ('prepare','continue'):
        raise ValueError('PDF fixture requires explicit isolated smoke_test and phase')
    from pathlib import Path
    from decimal import Decimal
    import httpx
    from solo.cloud import CloudLLM
    from solo.cloud_settings import CloudSettings
    from solo.cloud_cost import CostLedger
    from solo.narrative_grounding import fallback
    root=Path(data).resolve();owned=root/'pdf-world-smoke';marker=owned/'fixture.json'
    owner=dict(fixture='solo-pdf-world-smoke',schema=1)
    if phase=='prepare':
        if not root.is_dir() or (root/'solo.sqlite3').exists():
            raise ValueError('PDF smoke prepare requires a new isolated profile before create_app')
        owned.mkdir()  # Exclusive: never opens/replaces an existing fixture profile.
        marker.write_text(json.dumps(owner),encoding='utf8')
    elif not marker.is_file() or json.loads(marker.read_text(encoding='utf8'))!=owner or not (owned/'profiles.json').is_file():
        raise ValueError('PDF smoke continue requires its previous synthetic fixture profile')
    class MemoryVault:
        def get(self,profile):return 'synthetic-pdf-smoke-memory-only'
        def exists(self,profile):return True
    settings=CloudSettings(owned/'profiles.json',MemoryVault())
    if phase=='prepare':
        profile=routerai_preset();profile.enabled=True
        profile.name='Synthetic PDF smoke only';profile.provider='Synthetic PDF smoke';profile.base_url='https://pdf-smoke.invalid/v1'
        for key in ('max_request_cost','session_budget','daily_budget','monthly_budget','preparation_budget','total_budget'):
            setattr(profile,key,Decimal('100'))
        # Preparation resolves to pdf_analyst through the analyst role, so every
        # role the fixture may select needs a synthetic price.
        for cfg in (profile.interpreter,profile.narrator,profile.pdf_analyst,profile.secondary_narrator):
            if cfg is None:continue
            cfg.input_per_million=Decimal('1');cfg.output_per_million=Decimal('2')
            cfg.reasoning_control='routerai_enabled';cfg.parameters.append('reasoning')
        settings.save(type(profile).model_validate(profile.model_dump(mode='json')))
    elif settings.profile().base_url!='https://pdf-smoke.invalid/v1' or settings.profile().provider!='Synthetic PDF smoke':
        raise ValueError('PDF smoke profile does not match its synthetic owner')
    packet_log=[];packet_path=owned/'packets.jsonl'
    def respond(request):
        try:
            if request.method!='POST' or request.url.host!='pdf-smoke.invalid' or request.url.path!='/v1/chat/completions':raise ValueError('Unsupported synthetic request')
            body=json.loads(request.content);packet=json.loads(body['messages'][-1]['content'])
            if packet.get('task')=='campaign-prepare-v1':
                value=_pdf_smoke_proposal(packet);kind='preparation_world'
            elif 'player_intent' in packet:
                text=packet['player_intent'];context=packet.get('context',{});kind='interpreter'
                if text=='Осматриваюсь':value=dict(status='observe',target='')
                elif text=='Спрашиваю Dorn про bridge':
                    npc=next((n for n in context.get('scene',{}).get('npc',[]) if n['name']=='Dorn'),None)
                    if npc is None:raise ValueError('Synthetic speaker is unavailable')
                    value=dict(status='conversation',target=npc['id'])
                elif text in ('Проверяю ловкость','Проверяю wis'):
                    check='save-dex' if text=='Проверяю ловкость' else 'save-wis'
                    if check not in context.get('game_snapshot',{}).get('available_checks',[]):
                        raise ValueError('Synthetic check is not registered')
                    value=dict(status='command',action=dict(kind='check',check_id=check),message='')
                else:value=dict(status='unsupported',action=None,message='Synthetic PDF smoke supports only its declared utterances.')
            elif ('outcomes' in packet or 'player_words' in packet or 'description_sources' in packet
                  or ('place' in packet and any(k in packet for k in ('npc','atmosphere','observation')))):
                # The narrator packet is built around the scene (place/npc/
                # atmosphere), not only around conflict outcomes; keying the
                # route on outcomes alone sent every look-around to the 400
                # branch and made the fallback narrator unreachable.
                value=fallback(packet);kind='narrator'
            else:raise ValueError('Unsupported synthetic packet; world_structure is required; keys=%r task=%r' % (sorted(packet)[:14], packet.get('task')))
            entry=dict(phase=phase,kind=kind,task='campaign-prepare-v1' if kind=='preparation_world' else None,model=body['model'],message_roles=[m['role'] for m in body['messages']],
                pages=len(packet.get('pages',[])),spans=len({p['source_span'] for e in value.get('entities',[]) for p in e['evidence']}),
                transport='httpx.MockTransport',usage='synthetic',external_calls=0)
            packet_log.append(entry)
            with packet_path.open('a',encoding='utf8') as output:output.write(json.dumps(entry)+'\n')
            frames=[dict(choices=[dict(delta=dict(content=json.dumps(value,ensure_ascii=False)))]),
                dict(choices=[],usage=dict(prompt_tokens=1000,completion_tokens=200))]
            data=''.join('data: '+json.dumps(frame,ensure_ascii=False)+'\n\n' for frame in frames)+'data: [DONE]\n\n'
            return httpx.Response(200,content=data.encode('utf8'),headers={'content-type':'text/event-stream'})
        except (ValueError,TypeError,KeyError):
            return httpx.Response(400,json=dict(error='Synthetic PDF smoke request rejected; no external fallback'))
    llm=CloudLLM(settings,CostLedger(owned/'usage.sqlite3'),client=httpx.AsyncClient(
        transport=httpx.MockTransport(respond),trust_env=False,follow_redirects=False))
    llm.pdf_world_smoke=True;llm.pdf_smoke_packets=packet_log
    llm.smoke_packet_log=packet_log;llm.smoke_packet_log_path=packet_path
    return llm
