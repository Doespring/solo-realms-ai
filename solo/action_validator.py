"""AI intent is a proposal. This validator cannot write state or execute mechanics."""
import re
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Plan,Step

Category=Literal['ACTION_ATTEMPT','CONDITIONAL_ACTION','NPC_COMMAND','STATE_CLAIM','DESIRED_OUTCOME','SPEECH','INFORMATION_REQUEST']

class IntentAction(Strict):
    id: str=Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    category: Category
    kind: Literal['speak','observe','check','attack','move','use','wait','other']
    text: str=Field(min_length=1,max_length=800)
    target: str=Field(max_length=120)
    item: str=Field(max_length=120)
    disposition: Literal['now','negated','intended','conditional']
    condition: str=Field(max_length=400)
    condition_state: Literal['unknown','confirmed','not_applicable']
    after: list[str]=Field(max_length=8)
    @model_validator(mode='after')
    def coherent(self):
        if self.category=='CONDITIONAL_ACTION' and (self.disposition!='conditional' or not self.condition):raise ValueError('Условие потеряно')
        if self.disposition=='conditional' and not self.condition:raise ValueError('Неизвестно условие попытки')
        if self.condition and self.disposition not in ('conditional','negated'):raise ValueError('Условие нельзя исполнять без проверки')
        if self.condition_state=='confirmed':raise ValueError('LLM не подтверждает условие; это делает Game Engine')
        if self.category in ('SPEECH','NPC_COMMAND') and self.kind!='speak':raise ValueError('Речь не выполняет действие NPC')
        return self

class IntentPacket(Strict):
    summary: str=Field(min_length=1,max_length=1200)
    actions: list[IntentAction]=Field(min_length=1,max_length=8)
    unclear: list[str]=Field(max_length=8)
    branches: list[str]=Field(max_length=8)

class ActionValidator:
    @staticmethod
    def validate(raw,original,scene):
        packet=IntentPacket.model_validate_json(raw) if isinstance(raw,str) else IntentPacket.model_validate(raw)
        byid={a.id:a for a in packet.actions}
        if len(byid)!=len(packet.actions):raise ValueError('Повтор идентификатора попытки')
        positions={a.id:i for i,a in enumerate(packet.actions)};visiting=set();visited=set()
        def visit(id):
            if id not in byid:raise ValueError('Неизвестная зависимость попытки')
            if id in visiting:raise ValueError('Цикл попыток')
            if id in visited:return
            visiting.add(id)
            for parent in byid[id].after:visit(parent)
            visiting.remove(id);visited.add(id)
        for a in packet.actions:visit(a.id)
        location=scene.get('location',{})
        if isinstance(location,str):location={'name':location,'npc':scene.get('npc',[]),'objects':scene.get('objects',[])}
        known=set(scene.get('destinations',[]));known.add(location.get('name',''))
        def names(rows):
            for row in rows:
                if isinstance(row,str):known.add(row)
                elif isinstance(row,dict):
                    for key in ('id','name'):
                        if isinstance(row.get(key),str):known.add(row[key])
        names(location.get('npc',[]));names(location.get('objects',[]))
        names(scene.get('items',[]))
        names(scene.get('inventory',{}).get('items',[]));names(scene.get('combat',{}).get('actors',[]))
        unclear=list(packet.unclear);steps=[]
        normalized=' '.join(original.casefold().split())
        for a in packet.actions:
            optional=bool(re.match(r'^(?:необязательно|по возможности)\b',a.text.casefold())) and a.kind in ('other','use','attack')
            if ' '.join(a.text.casefold().split()) not in normalized:raise ValueError('Модель добавила отсутствующее намерение')
            if a.condition and ' '.join(a.condition.casefold().split()) not in normalized:raise ValueError('Модель изменила условие игрока')
            if any(positions[parent]>=positions[a.id] for parent in a.after):raise ValueError('Зависимость не соответствует порядку попыток')
            if a.target and a.target not in known and not optional:raise ValueError('Цель не подтверждена сценой')
            if a.item and a.item not in known and not optional:raise ValueError('Предмет не подтверждён сценой')
            disposition=a.disposition;kind=a.kind
            if a.category in ('STATE_CLAIM','DESIRED_OUTCOME'):
                disposition='intended';kind='other';unclear.append('Требуется сверка заявления: '+a.text[:200])
            if kind in ('attack','move','use') and not a.target and not a.item and not optional:unclear.append('Не подтверждена цель: '+a.text[:200])
            steps.append(Step(kind=kind,text=a.text,target=a.target,item=a.item,disposition=disposition,condition=a.condition if disposition=='conditional' else '',action_id=a.id,after=a.after))
        return Plan(summary=packet.summary,steps=steps,unclear=unclear[:8],branches=packet.branches,needs_confirmation=True)
