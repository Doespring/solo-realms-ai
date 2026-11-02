"""A bounded answer to factual questions when the NPC has no known evidence."""
import re

CAUSAL=re.compile(r'что\s+(?:случилось|произошло)|почему|кто\s+(?:это\s+)?(?:сделал|запер|открыл|пришел|пришёл)|откуда\s+(?:ты|вы)\s+зна',re.I)

def unknown_reply(packet):
    # This shortcut answers dialogue only; other engine results must still be narrated.
    if any(packet.get(key) for key in ('outcomes','attempts','deferred','denied')):return None
    people={p['name']:p for p in packet.get('npc',[])};targets=[]
    for words in packet.get('player_words',[]):
        if not CAUSAL.search(words.get('words','')):return None
        name=words.get('to')
        if not name and len(people)==1:name=next(iter(people))
        if name not in people:return None
        person=people[name]
        if person.get('confirmed_knowledge'):return None
        records=[*packet.get('memory',[]),*person.get('memory',[])]
        if any(person.get('id') in r.get('known_by',[]) and r.get('kind') not in ('conversation','promise','intent') for r in records):return None
        if name not in targets:targets.append(name)
    if not targets:return None
    return dict(description='',dialogue=[dict(speaker=name,text='Не знаю.') for name in targets],proposals=[])
