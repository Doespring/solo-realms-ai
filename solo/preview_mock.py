"""Explicit offline preview. No provider profiles, keys or network fallback."""
import json
import re
from .llm import CommandMockLLM, MockLLM
from .contracts import Plan, Step


class PreviewMock(CommandMockLLM):
    def __init__(self):
        super().__init__({})
        self.plan = Plan(summary='Осмотр', steps=[Step(kind='observe', text='Осмотр сцены')])

    def scripted_intent(self, text):
        if text in self.intents:
            return super().scripted_intent(text)
        packet = self.packets[-1]['packet'] if self.packets else {}
        context = packet.get('context', {})
        scene, snapshot = context.get('scene', {}), context.get('game_snapshot', {})
        original, _, latest = text.rpartition('\nУточнение игрока: ')
        words = (latest or text).strip().casefold().rstrip('.!?')
        if original and not re.match(r'^(осматр|оглядыв|опиши|говор|поговор|расспраш|спраш|привет|здравств|атак|бью|ищу|провер|иду|двиг|перехож|начина|заверша|заканч|рывок|коротк|длит|использ|примен|наклад|каст|активир)',words):
            words = original.casefold() + ' ' + words
        def command(**action):return dict(status='command', action=action, message='')
        def ask(message):return dict(status='clarification', action=None, message=message)
        if re.match(r'^(осматриваюсь|оглядываюсь|опиши|смотрю вокруг|осматриваю (комнату|место|окрестности))', words):
            return dict(status='observe', target='')
        if words.startswith('осматриваю ') and any(str(obj).casefold() in words for obj in scene.get('objects',[])):
            return dict(status='observe', target='')
        people = scene.get('npc', [])
        if re.match(r'^(говорю|поговор|расспраш|спрашива|привет|здравств)', words) or (original and any(words in (str(n.get('id','')).casefold(), n['name'].casefold()) for n in people)):
            selected = [n for n in people if n['name'].casefold() in words or n.get('id') and str(n['id']).casefold() in words.split()]
            return dict(status='conversation', target=selected[0].get('id', selected[0]['name']) if len(selected)==1 else '')
        if words.startswith(('атакую', 'бью')):
            candidates = [a for a in snapshot.get('actors', []) if a['id'] != 'hero' and (a['id'].casefold() in words or a['name'].casefold() in words)]
            return command(kind='attack', target=candidates[0]['id']) if len(candidates)==1 else ask('Укажите одну видимую цель атаки.')
        capabilities = context.get('capabilities', {})
        if words.startswith(('применяю','накладываю','кастую','использую','активирую')):
            groups = [('prepared_spells','cast_spell','spell_id'),('features','use_feature','feature_id'),('equipment','use_item','item_id')]
            for key, operation, parameter in groups:
                matches = [identity for identity in capabilities.get(key,[]) if identity.casefold() in words or identity.replace('-',' ').casefold() in words]
                if len(matches)!=1:continue
                action = dict(kind='native_action',operation=operation,**{parameter:matches[0]})
                targets = [a['id'] for a in snapshot.get('actors',[]) if a['id'].casefold() in words or a['name'].casefold() in words]
                if operation=='cast_spell':
                    slot=re.search(r'(?:ячейк\w*|уров\w*)\s*(\d)',words)
                    if not slot or not targets:return ask('Укажите ID цели и уровень ячейки заклинания; параметры не угадываются.')
                    action.update(slot_level=int(slot[1]),target_ids=targets)
                elif targets:action['target_id']=targets[0]
                return command(**action)
            if len(words.split()) == 1:
                return ask('Укажите ID способности, предмета или подготовленного заклинания из текущего листа.')
            return dict(status='unsupported', action=None, message='Эта возможность не объявлена в текущем листе для offline mock. Выберите доступное действие.')
        if words in ('уклоняюсь','уклонение','отход','прячусь','встаю'):
            return command(kind='native_action',operation={'уклоняюсь':'dodge','уклонение':'dodge','отход':'disengage','прячусь':'hide','встаю':'stand_up'}[words])
        if words.startswith(('проверяю', 'проверить', 'проверка', 'ищу', 'осматриваю')):
            checks = snapshot.get('available_checks', [])
            matching = [c for c in checks if c.casefold() in words]
            if len(matching)==1:return command(kind='check', check_id=matching[0])
            # A small language adapter for registered checks, not an action/DC
            # resolver. Bare "Осматриваю" still needs its object; joined answers
            # can name visible tracks even when the engine ID is "perception".
            if 'perception' in checks and re.search(r'\b(?:след\w*|вниматель\w*)\b', words):
                return command(kind='check', check_id='perception')
            return ask('Укажите проверку: ' + ', '.join(checks))
        move = re.fullmatch(r'(?:иду|перехожу|двигаюсь) на клетку (\d+)[, ]+(\d+)(?:[, ]+(-?\d+))?', words)
        if move:return command(kind='move', position=dict(x=int(move[1]),y=int(move[2]),z=int(move[3] or 0)))
        if words in ('начинаю бой','завершаю бой','заканчиваю ход','рывок'):
            kind = {'начинаю бой':'start_combat','завершаю бой':'end_combat','заканчиваю ход':'end_turn','рывок':'dash'}[words]
            if kind=='start_combat':
                encounters = context.get('encounters', [])
                return command(kind=kind, encounter_id=encounters[0]) if len(encounters)==1 else ask('Какое доступное столкновение начать?')
            return command(kind=kind)
        if words.startswith('длительный отдых'):return command(kind='long_rest', food_water='пищ' in words and 'вод' in words)
        rest = re.fullmatch(r'короткий отдых (?:с )?(\d+) кост(?:ью|ями)',words)
        if rest:return command(kind='short_rest',hit_dice=int(rest[1]))
        if 'отдых' in words:return ask('Короткий или длительный отдых? Для короткого укажите число костей хитов.')
        return ask('Offline mock не распознал намерение. Уточните одно действие и его цель из текущего листа.')

    async def stream(self, system, user, **options):
        try: packet = json.loads(user)
        except ValueError: packet = {}
        stream = super().stream if options.get('mode') == 'interpreter' or 'game_snapshot' in packet else MockLLM.stream.__get__(self)
        async for chunk in stream(system, user, **options):
            yield chunk
