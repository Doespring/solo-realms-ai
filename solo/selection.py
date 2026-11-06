"""Read-only player selection: always resolve against public, campaign-owned data."""
from typing import Literal
from pydantic import Field
from .contracts import Strict

class Selection(Strict):
    kind: Literal['item','merchant_item','npc','location','actor','cell','object','skill','feature','spell','handout']
    id: str=Field(default='',max_length=160)
    owner: str=Field(default='',max_length=160)
    x: int | None=Field(default=None,ge=1,le=1000)
    y: int | None=Field(default=None,ge=1,le=1000)
    z: int | None=Field(default=None,ge=-6,le=20)

def resolve(state,selection):
    if selection is None:return None
    from .combat_view import public_campaign
    public=public_campaign({'state':state})['state'];s=selection
    rows=[]
    if s.kind=='skill':
        from .contracts import Hero
        from .rulesets import RulesetEngine,active
        skill=RulesetEngine(active(state)).sheet(Hero.model_validate(state['hero']))['skills'].get(s.id)
        if skill:return dict(kind=s.kind,id=s.id,**skill)
    if s.kind in ('feature','spell'):
        if s.id in state['hero'].get('features' if s.kind=='feature' else 'spells',[]):return dict(kind=s.kind,id=s.id,name=s.id,source='Ручная запись листа; отдельный эффект должен быть зарегистрирован')
    if s.kind=='handout':rows=(public.get('adventure') or {}).get('materials',[])
    elif s.kind=='item':rows=public.get('economy',{}).get('items',[])
    elif s.kind=='merchant_item':
        merchant=next((m for m in public.get('economy',{}).get('merchants',[]) if m['id']==s.owner),None)
        rows=merchant.get('stock',[]) if merchant else []
    elif s.kind=='npc':rows=[n for p in public['locations'] if p['name']==public['location'] for n in p['npc'] if n.get('present',True)]
    elif s.kind=='location':rows=public.get('world',{}).get('nodes',[])
    elif s.kind in ('actor','object'):
        battle=public.get('combat') or {}
        rows=battle.get('actors',[]) if s.kind=='actor' else battle.get('map',{}).get('objects',[])
    elif s.kind=='cell':
        cell=next((c for c in (public.get('combat') or {}).get('map',{}).get('cells',[]) if (c['x']+1,c['y']+1,c['z'])==(s.x,s.y,s.z) and c.get('visible')),None)
        if cell:return dict(kind='cell',x=s.x,y=s.y,z=s.z,terrain=cell['terrain'])
    row=next((r for r in rows if str(r.get('id',r.get('name')))==s.id),None)
    if not row:raise ValueError('Выбранный контекст больше не доступен герою. Выберите его заново.')
    result=dict(kind=s.kind,id=s.id,name=row.get('name',s.id))
    if s.owner:result['owner']=s.owner
    if row.get('position'):result['position']=row['position']
    if s.kind=='handout' and row.get('public_text'):result.update(text=row['public_text'][:1500],known_by=['hero'])
    return result

def player_text(text):
    """An address to the GM is not a game action; first-person intentions still are."""
    import re
    prefix=re.match(r'^\s*Мастер\s*[:,—]\s*',text,re.I)
    if not prefix:return text,False
    rest=text[prefix.end():]
    question=re.match(r'(?:я\s+)?(?:могу\s+ли|можно\s+ли|почему|как\s+|что\s+|объясни|подскажи|не\s+понимаю|правильно\s+ли)\b',rest,re.I)
    action=re.match(r'(?:я\s+)?(?:иду|подхожу|осматриваю|атакую|пробую|пытаюсь|говорю|спрашиваю|беру|надеваю|снимаю|выпиваю|пью|передаю|покупаю|продаю|использую|выбрасываю|экипирую|начинаю|устраиваю|хочу\s+(?:осмотреть|атаковать|купить|продать|передать|пойти))\b',rest,re.I)
    intention=action or re.match(r'я\s+',rest,re.I)
    return (rest,False) if intention and not question else (text,True)
