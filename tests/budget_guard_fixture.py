"""Synthetic durable budget fixture; no client or real API runner."""
import json,time
from pathlib import Path
from decimal import Decimal
QA=Path("unused-synthetic-profile")
CAP=Decimal("50")

class Grant:
    def __init__(self,rates):
        self.path=QA/'grant.json';self.rates=rates;self.current=None
        self.value=json.loads(self.path.read_text(encoding='utf8')) if self.path.exists() else dict(authorized_rub='50',authorization='synthetic test authorization',calls=[])
    def save(self):
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(self.value,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(self.path)
    def held(self):return sum((Decimal(c.get('provider_cost_rub',c['bound_rub'])) for c in self.value['calls']),Decimal(0))
    async def request(self,request):
        if request.method!='POST':return
        if str(request.url)!='https://routerai.ru/api/v1/chat/completions':raise ValueError('Unexpected paid destination')
        body=json.loads(request.content);model=body['model'];inp,out=self.rates[model]
        if any(k in body for k in ('tools','plugins','web_search','modalities')):raise ValueError('Unbudgeted tools/media are forbidden')
        # Conservative byte reservation + framing margin, worst current endpoint
        # tariff (including variable prices), and 2x output margin for reasoning.
        bound=(Decimal(len(request.content)+4096)*inp+Decimal(body.get('max_tokens',body.get('max_completion_tokens',0)))*out*2)*Decimal('1.25')
        if self.held()+bound>CAP:raise ValueError('Aggregate 50 RUB authorization would be exceeded')
        self.current=dict(index=len(self.value['calls'])+1,model=model,bound_rub=str(bound),started=time.time(),output_limit=body.get('max_tokens',body.get('max_completion_tokens')),reasoning=body.get('reasoning'))
        self.value['calls'].append(self.current);self.save()
        print(json.dumps(dict(sending=model,bound_rub=str(round(bound,4)),grant_held_rub=str(round(self.held(),4)))),flush=True)
    async def response(self,response):
        if response.request.method=='POST' and self.current is not None:
            self.current['http']=response.status_code
            identifier=response.headers.get('x-generation-id')
            if identifier and len(identifier)<200:self.current['generation_id']=identifier
            self.save()
    def finish(self,metric):
        if self.current is None:return
        self.current.update(status=metric.get('status'),usage_source=metric.get('cost_source'))
        if metric.get('cost_source')=='provider' and metric.get('currency')=='RUB':self.current['provider_cost_rub']=metric['cost']
        self.save()
