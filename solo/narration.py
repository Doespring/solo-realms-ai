"""Потоковое представление типизированных реплик без второй генерации."""
import json
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Strict

class PlainPresentation:
    """Stream prose while suppressing reasoning control blocks across chunk boundaries."""
    def __init__(self):self.buffer='';self.started=False;self.thinking=False;self.shown=''
    def feed(self,chunk):
        self.buffer+=chunk
        output=''
        while True:
            if self.thinking:
                end=self.buffer.find('</think>')
                if end<0:self.buffer=self.buffer[-7:];break
                self.buffer=self.buffer[end+8:];self.thinking=False;continue
            begin=self.buffer.find('<think>')
            closing=self.buffer.find('</think>')
            if closing>=0 and (begin<0 or closing<begin):
                output+=self.buffer[:closing];self.buffer=self.buffer[closing+8:];continue
            if begin>=0:
                output+=self.buffer[:begin];self.buffer=self.buffer[begin+7:];self.thinking=True;continue
            held=0
            for tag in ('<think>','</think>'):
                for n in range(1,len(tag)):
                    if self.buffer.endswith(tag[:n]):held=max(held,n)
            output+=self.buffer[:-held] if held else self.buffer
            self.buffer=self.buffer[-held:] if held else ''
            break
        if not self.started and output.strip():
            if output.lstrip().startswith(('{','```json')):raise ValueError('Narrator returned a structured response in plain mode')
            self.started=True
        self.shown+=output
        return output
    def finish(self):
        if self.thinking:raise ValueError('Narrator did not close its reasoning channel')
        if self.buffer:raise ValueError('Narrator ended inside a control delimiter')
        if not self.shown.strip():raise ValueError('Narrator returned no visible prose')
        self.shown.encode('utf8')
        return self.shown

class Segment(Strict):
    speaker: str = Field(min_length=1,max_length=120)
    text: str = Field(min_length=1,max_length=5000)

class Proposal(Strict):
    category: Literal['discovery','npc_statement','relationship']
    text: str = Field(min_length=1,max_length=600)

class Narrative(Strict):
    description: str = Field(max_length=5000)
    dialogue: list[Segment] = Field(max_length=3)
    proposals: list[Proposal] = Field(max_length=3)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.description.strip() and not self.dialogue:
            raise ValueError('Художественный ответ пуст')
        return self

def schema(speakers,require_reply=False):
    # Не допускает героя/несуществующего NPC в поле speaker.
    return {'type':'object','properties':{
        'description':{'type':'string'},
        'dialogue':{'type':'array','minItems':1 if require_reply and speakers else 0,'maxItems':3 if speakers else 0,'items':{'type':'object','properties':{
            'speaker':{'type':'string','enum':speakers or ['nobody']},'text':{'type':'string'}},
            'required':['speaker','text'],'additionalProperties':False}},
        'proposals':{'type':'array','maxItems':3,'items':{'type':'object','properties':{
            'category':{'type':'string','enum':['discovery','npc_statement','relationship']},'text':{'type':'string'}},
            'required':['category','text'],'additionalProperties':False}}},
        'required':['description','dialogue','proposals'],'additionalProperties':False}

class StreamPresentation:
    """Разбирает поля JSON по состояниям; строки с экранированием не теряются.

    Представление потоковое. Канон/предложения применяются только после полной
    Pydantic-валидации, никогда из частичного JSON.
    """
    def __init__(self,speakers):
        self.allowed=set(speakers);self.raw='';self.shown=''

    def feed(self,chunk):
        self.raw+=chunk
        text=self.preview()
        try:text.encode('utf8')
        except UnicodeError as exc:raise ValueError('Некорректный Unicode в художественном ответе') from exc
        if not text.startswith(self.shown):
            raise ValueError('Нарушен потоковый порядок реплик')
        delta=text[len(self.shown):];self.shown=text
        return delta

    def preview(self):
        # Простой лексер JSON: вне строк отслеживает ключи и границу массива.
        s=self.raw;decoder=json.JSONDecoder();output=[]
        # Ключи принимаются только в ожидаемом порядке, заданном грамматикой.
        marker=s.find('"description"')
        if marker<0:return ''
        colon=s.find(':',marker);pos=colon+1
        while pos<len(s) and s[pos].isspace():pos+=1
        description,end=self.partial_string(s,pos)
        if description:output.append(description)
        if end is None:return '\n\n'.join(output)
        start=s.find('"dialogue"',end)
        if start<0:return '\n\n'.join(output)
        start=s.find('[',start)
        if start<0:return '\n\n'.join(output)
        i=start+1
        while i<len(s):
            while i<len(s) and s[i] in ' \r\n\t,':i+=1
            if i==len(s) or s[i]==']':break
            if s[i]!='{':break
            end=i+1;speaker=None;body='';closed=False
            while end<len(s):
                while end<len(s) and s[end] in ' \r\n\t,':end+=1
                if end==len(s):break
                if s[end]=='}':end+=1;closed=True;break
                try:key,offset=decoder.raw_decode(s,end)
                except json.JSONDecodeError:break
                end=offset
                while end<len(s) and s[end] in ' \r\n\t:':end+=1
                if end==len(s):break
                if key=='text' and s[end]=='"':
                    # В завершённой и частичной JSON-строке декодируем только
                    # полные escape-последовательности, сохраняя один префикс.
                    body,complete=self.partial_string(s,end)
                    if complete is None:end=len(s);break
                    end=complete
                else:
                    try:value,end=decoder.raw_decode(s,end)
                    except json.JSONDecodeError:break
                    if key=='speaker':speaker=value
            if speaker in self.allowed and body:
                output.append(speaker+': «'+body)
                # Закрывающую кавычку показываем только после завершения объекта.
                if closed:output[-1]+='»'
            if end<=i or not closed:break
            i=end
        return '\n\n'.join(output)

    @staticmethod
    def partial_string(s,start):
        if start>=len(s) or s[start]!='"':return '',None
        pos=start+1;parts=[]
        while pos<len(s):
            if s[pos]=='"':return ''.join(parts),pos+1
            if s[pos]=='\\':
                if pos+1>=len(s):break
                size=6 if s[pos+1]=='u' else 2
                if pos+size>len(s):break
                if size==6:
                    try:high=int(s[pos+2:pos+6],16)
                    except ValueError:break
                    if 0xD800<=high<=0xDBFF:
                        size=12
                        if pos+size>len(s):break
                try:parts.append(json.loads('"'+s[pos:pos+size]+'"'))
                except json.JSONDecodeError:break
                pos+=size
            else:parts.append(s[pos]);pos+=1
        return ''.join(parts),None

    def finish(self):
        from .json_framing import unframe
        result=Narrative.model_validate_json(unframe(self.raw)[0])
        if any(s.speaker not in self.allowed for s in result.dialogue):
            raise ValueError('Недопустимый говорящий в художественном ответе')
        return result
