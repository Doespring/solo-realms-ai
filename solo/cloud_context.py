"""Conservative planning estimates, deliberately NOT a model tokenizer.

ASCII / 3.2 and Cyrillic / 2.4 character baselines, plus 25% headroom.
Other scripts use their UTF-8 bytes conservatively. Provider
templates and vision encoding remain unknown: this is not a proven upper bound.
Never interpret base64 transport bytes as text tokens, or predict a cache hit.
"""
import json
import re

ESTIMATE_METHOD = 'unicode-char-3.2-2.4-plus25pct-v2'
REQUEST_OVERHEAD = 128
IMAGE_ALLOWANCE = 4096  # Existing allowance for each validated <=384px PNG.


def text_estimate(text):
    """Planning heuristic, not a tokenizer or measured error bound."""
    ascii_n=sum(ord(c)<128 for c in text)
    cyrillic_n=sum('\u0400'<=c<='\u052f' for c in text)
    other_bytes=sum(len(c.encode('utf8')) for c in text if ord(c)>=128 and not '\u0400'<=c<='\u052f')
    # Exact integer ceil of ASCII/3.2 + Cyrillic/2.4 + other bytes.
    return (15*ascii_n+20*cyrillic_n+48*other_bytes+47)//48


def estimate_context(messages, *, response_format=None, image_count=0):
    if not isinstance(image_count, int) or isinstance(image_count, bool) or image_count < 0:
        raise ValueError('Invalid image count')
    texts=[]
    for message in messages:
        content = message['content']
        if isinstance(content, str):
            texts.append(content)
        else:
            for part in content:
                if part['type'] == 'text':
                    texts.append(part['text'])
                elif part['type'] != 'image_url':
                    raise ValueError('Unsupported content in context estimate')
    # Native structured-output schema also occupies provider context, even when
    # it is not repeated in the system message. Count its JSON exactly once.
    schema_text = json.dumps(response_format, ensure_ascii=False, separators=(',', ':')) if response_format else ''
    text_bytes=sum(len(t.encode('utf8')) for t in texts)
    text_tokens=sum(text_estimate(t) for t in texts)
    schema_tokens=text_estimate(schema_text)
    margin = (text_tokens + schema_tokens + 3) // 4
    overhead = REQUEST_OVERHEAD + 16 * max(0, len(messages) - 2)
    images = image_count * IMAGE_ALLOWANCE
    return dict(method=ESTIMATE_METHOD, exact=False, text_utf8_bytes=text_bytes,
                text_characters=sum(len(t) for t in texts),schema_utf8_bytes=len(schema_text.encode('utf8')),
                text_tokens_estimate=text_tokens, schema_tokens_estimate=schema_tokens,
                safety_margin_tokens=margin, request_overhead_tokens=overhead,
                image_count=image_count, image_tokens_estimate=images,
                input_tokens_estimate=text_tokens + schema_tokens + margin + overhead + images)


def move_turn_limits(system, user, output_tokens):
    """Normalize legacy length markers; append current limits after turn data."""
    marker = r'Всего до \d+ слов:'
    if not re.search(marker, system):
        return system, user
    system = re.sub(marker, 'Форма ответа:', system)
    system += '\nОграничение объёма текущего ответа задано в конце контекста хода.'
    instruction = f'Всего до {max(18, (output_tokens - 60) // 5)} слов. Сохрани заданную форму ответа и закончи мысль до лимита.'
    if isinstance(user, str):
        try:
            packet = json.loads(user)
        except ValueError:
            packet = None
        if isinstance(packet, dict):
            packet.pop('_solo_turn_response_limits', None)
            packet['_solo_turn_response_limits'] = instruction
            user = json.dumps(packet, ensure_ascii=False, separators=(',', ':'))
        else:
            user += '\n\nОграничение текущего ответа: ' + instruction
    else:
        user = [*user, dict(type='text', text='Ограничение текущего ответа: ' + instruction)]
    return system, user
