"""Evidence-only presentation: unverified prose never reaches the player.

This intentionally limits paraphrasing. It is a literal verifier, not a claim
that a semantic hallucination detector can prove arbitrary prose true.
"""
import json,re

UNKNOWN='Не знаю.'
class CanonViolation(ValueError):pass
def sentences(text):
    if not isinstance(text,str):return []
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+',text) if s.strip()]

def sources(packet):
    description=[]
    dynamic=[word[:4].casefold() for obj in packet.get('object_states',[]) for word in obj.get('name','').split() if len(word)>=4]
    for text in [packet.get('atmosphere',''),*packet.get('confirmed',[])]:
        description.extend(s for s in sentences(text) if len(s)<=700 and not any(stem in s.casefold() for stem in dynamic))
    outcomes=[]
    for item in packet.get('outcomes',[]):
        value=item.get('outcome','')
        if value in ('успех','неудача'):value='Действие удалось.' if value=='успех' else 'Попытка не удалась.'
        outcomes.extend(s for s in sentences(value) if len(s)<=700)
    # With no scenery, name the place rather than asking a meta question: the
    # question is scaffolding and the name is the answer to 'where am I'.
    place=packet.get('place')
    description=list(dict.fromkeys([*outcomes,*description]))[:16] or [place or 'Что вы делаете?']
    dialogue={}
    for npc in packet.get('npc',[]):
        known=npc.get('confirmed_knowledge','')
        texts=[s for s in sentences(known) if len(s)<=700] if isinstance(known,str) else []
        shared=[m for m in packet.get('memory',[]) if npc.get('id') and npc['id'] in m.get('known_by',[])]
        for memory in [*shared,*npc.get('memory',[])]:
            if memory.get('truth')=='fact' and memory.get('kind') not in ('conversation','promise','intent'):
                texts.extend(s for s in sentences(memory.get('content','')) if len(s)<=700)
        dialogue[npc['name']]=list(dict.fromkeys(texts))[:8] or [UNKNOWN]
    return dict(description_sources=description,dialogue_sources=dialogue,
                player_words=packet.get('player_words',[]),outcomes_present=bool(outcomes),
                observation=bool(packet.get('observation')))

def wire_packet(packet):return sources(packet)

OBSERVATION='Нового ничего не заметно.'
META_PROMPT=re.compile(r'что\s+(?:вы|ты)\s+дела|что\s+делать|как\s+поступить', re.IGNORECASE)
# Words a sentence does not end on: a source that stops here was cut off.
FRAGMENT_TAIL=re.compile(r'\b(?:и|а|но|или|по|на|в|с|у|к|от|до|за|под|над|о|об|из|что|как|когда|лениво|медленно|тихо)\s*$', re.IGNORECASE)

def fallback(packet):
    allowed=sources(packet)
    # An observation that produced no outcome has nothing new to say. Replaying
    # the whole scene made every look-around store the same paragraph, so the
    # transcript read as if the hero kept re-arriving. Say plainly that nothing
    # new was noticed instead of repeating the description.
    if allowed.get('observation') and not allowed['outcomes_present']:
        # Keep the place in the sentence: a look-around answers 'ГДЕ я и что
        # вижу', and dropping the name left the table without an answer.
        # The first source can be a fragment ('... по дороге, лениво'): take the
        # first COMPLETE sentence, or the place name alone if none is complete.
        where=''
        for candidate in allowed['description_sources']:
            text=str(candidate or '').strip()
            # A bare place name ('Кузница') carries no full stop and is exactly
            # what the player needs; a long fragment with no stop is a cut-off.
            # A bare place name has no verb ending and is short; a cut-off
            # fragment ends on a word that asks for more ('... по дороге,
            # лениво'). Length alone let a 53-character fragment through.
            complete=bool(re.search(r'[.!?…]$', text)) or (len(text)<=40 and not FRAGMENT_TAIL.search(text))
            whole=text if text and not META_PROMPT.search(text) and complete else ''
            # '…' is a terminal mark too; leaving it out dropped whole sentences.
            pieces=[s.strip() for s in re.findall(r'[^.!?…]*[.!?…]+', text) if not META_PROMPT.search(s)]
            where=pieces[0] if pieces else whole
            if where:
                break
        # 'Кузница Нового ничего не заметно' runs two sentences together;
        # the dash separates the place from the verdict without declining it.
        description=where.rstrip('.…!?')+' — '+OBSERVATION if where else OBSERVATION
    else:
        description=' '.join(allowed['description_sources'][:2])
    dialogue=[]
    if packet.get('player_words'):
        for words in packet['player_words']:
            who=words.get('to')
            if not who and len(allowed['dialogue_sources'])==1:who=next(iter(allowed['dialogue_sources']))
            if who in allowed['dialogue_sources'] and not any(x['speaker']==who for x in dialogue):dialogue.append(dict(speaker=who,text=allowed['dialogue_sources'][who][0]))
    return dict(description=description,dialogue=dialogue[:3],proposals=[])

def render(value):return '\n\n'.join([*([value['description']] if value['description'] else []),*[x['speaker']+': «'+x['text']+'»' for x in value['dialogue']]])

# Ending marks are prose, not content: 'Кузница открыта!' and 'Кузница открыта.'
# say the same thing, and the host used to reject the lively version while
# accepting the flat one. The model_artistic policy exists precisely so the
# narrator may choose its own delivery; what it may not do is say something the
# sources do not say.
_TERMINAL=re.compile(r'[\s.!?…,;:—–"«»\'’]+$')

def _content(text):
    """A sentence reduced to what it claims, with delivery marks dropped."""
    value=re.sub(r'\s+',' ',str(text or '')).strip()
    return _TERMINAL.sub('',value).casefold()

def _known(sentence,pool):
    """True when the sentence restates a source sentence and adds no claim."""
    claim=_content(sentence)
    return bool(claim) and any(claim==_content(known) for known in pool)

def verify(value,packet):
    allowed=sources(packet)
    if value.get('proposals'):raise CanonViolation('Narrator cannot introduce unverified proposals')
    for sentence in sentences(value.get('description','')):
        # Content equality, never a word-overlap ratio: a sentence either
        # restates a confirmed source or it states something new, and a new
        # claim from the narrator is exactly what must not pass.
        if not _known(sentence,allowed['description_sources']):raise CanonViolation('Unconfirmed description or hero action')
    if value.get('description','').strip() and not sentences(value['description']):raise CanonViolation('Unverifiable description')
    for segment in value.get('dialogue',[]):
        parts=sentences(segment['text']);known=allowed['dialogue_sources'].get(segment['speaker'],[])
        if not parts or any(not _known(s,known) for s in parts):raise CanonViolation('Unconfirmed NPC knowledge')
    return value

def plain_value(text,packet):
    dialogue=[]
    def take(match):
        dialogue.append(dict(speaker=match.group(1).strip(),text=match.group(2)));return ''
    description=re.sub(r'([^\n:]{1,120}):\s*«([^»]*)»',take,text).strip()
    return dict(description=description,dialogue=dialogue,proposals=[])
