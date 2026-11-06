"""Player projection and cached, disposable legality probes; no rule arithmetic."""
from collections import OrderedDict
from copy import deepcopy

from .game_commands import GameCommand
from .nat20_api import Nat20GameEngine
from .nat20_content import digest


class Nat20Screen:
    def __init__(self):
        self.cache = OrderedDict()

    def project(self, current):
        # All committed state/guards participate: undo/restore and rule changes
        # cannot reuse availability from another version or campaign.
        key = digest([current['id'], current['version'], current['state']])
        if key in self.cache:
            self.cache.move_to_end(key)
            return deepcopy(self.cache[key])
        backend = Nat20GameEngine(None)
        runtime = backend._bound(current)
        snapshot = backend.visible(backend.snapshot(current))
        hero = next(a for a in snapshot['actors'] if a['id'] == 'hero')
        labels = {'feature_use:second-wind': 'Второе дыхание · потрачено',
                  'movement_remaining': 'Осталось движения, футы', 'attacks_remaining': 'Осталось атак',
                  'action_available': 'Действие', 'bonus_action_available': 'Бонусное действие', 'reaction_available': 'Реакция',
                  'death_save:successes':'Спасброски от смерти · успехи','death_save:failures':'Спасброски от смерти · провалы',
                  'death_save:is_stable':'Стабилизирован','dead':'Смерть подтверждена Nat20','exhaustion':'Истощение'}
        hero_view = dict(name=hero['name'], hp=hero['hp']['current'], max_hp=hero['hp']['maximum'],
                         conditions=hero['conditions'], hit_dice=hero['resources']['hit_dice'],
                         resources=[dict(id=k, label=labels.get(k, k), current=v, maximum=None)
                                    for k, v in hero['resources'].items() if k != 'hit_dice'])
        battle = None
        candidates = []
        if runtime['combat']:
            native = runtime['combat']['view']
            initiatives = {a['entity_id']: a['initiative'] for a in native['initiative']}
            battle = dict(active=True, round=snapshot['combat']['round'], current_actor=snapshot['combat']['turn'],
                          actors=[dict(id=a['id'], name=a['name'], team='hero' if a['id'] == 'hero' else 'enemy',
                                       hp=a['hp']['current'], max_hp=a['hp']['maximum'], conditions=a['conditions'],
                                       initiative=initiatives[a['id']]) for a in snapshot['actors']])
            for actor in snapshot['actors']:
                if actor['id'] == 'hero':
                    continue
                candidates.append(('Атаковать: ' + actor['name'], dict(kind='attack', target=actor['id'])))
                # Coordinates are candidate destinations, not a path/range solver.
                # Native mechanics alone decides which of them are legal.
                x, y, z = actor['position']
                for px, py in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                    if (px, py, z) == tuple(hero['position']) or px < 0 or py < 0:
                        continue
                    candidates.append((f'Перейти: {px+1}, {py+1}', dict(kind='move', position=dict(x=px, y=py, z=z))))
            candidates += [('Рывок', dict(kind='dash')),
                           ('Закончить ход', dict(kind='end_turn')), ('Завершить бой', dict(kind='end_combat'))]
            from .nat20_prototype import experimental
            if experimental(runtime.get('definition')):
                candidates += [(name,dict(kind='native_action',operation=op)) for name,op in (
                    ('Уклонение','dodge'),('Отход','disengage'),('Встать','stand_up'))]
                if 'second-wind' in runtime['definition']['features']:
                    candidates.append(('Второе дыхание',dict(kind='native_action',operation='use_feature',feature_id='second-wind',target_id='hero')))
            else:
                candidates.append(('Второе дыхание',dict(kind='use_feature',feature_id='second-wind')))
        else:
            candidates = [('Проверить: ' + check, dict(kind='check', check_id=check)) for check in snapshot['available_checks']]
            # Offering a made-up 'basic' encounter advertised a fight the
            # engine had never registered; it was then rejected at execute time.
            encounters=runtime['definition']['encounters'] if runtime.get('definition') else {}
            candidates += [('Начать столкновение: '+key,dict(kind='start_combat',encounter_id=key)) for key in encounters]
            candidates += [('Короткий отдых', dict(kind='short_rest', hit_dice=0)),
                           ('Короткий отдых · потратить кость хитов', dict(kind='short_rest', hit_dice=1)),
                           ('Длительный отдых с пищей и водой', dict(kind='long_rest', food_water=True))]

        commands = []
        valid_candidates = []
        for label, action in candidates:
            command = GameCommand(request_id='screen-availability', expected_version=current['version'], action=action)
            try:
                backend._preflight(current, command)
            except ValueError:
                continue
            commands.append(command.model_dump(mode='json'))
            valid_candidates.append((label, action))
        # One disposable process, with independent replays for each candidate.
        # Same native validation as execution; no speculative state is committed.
        response = backend._worker(dict(mode='availability', state=runtime, commands=commands, session_id=current['id']))
        if len(response['available']) != len(commands):
            raise ValueError('Invalid native availability response')
        available = [dict(label=label, kind='engine', parameters=action, expected_version=current['version'])
                     for (label, action), allowed in zip(valid_candidates, response['available']) if allowed]
        if current['state'].get('lantern_scene'):
            for command in available:
                if command['parameters']['kind'] == 'start_combat':command['label'] = 'Войти во двор · начать бой'
        for index, command in enumerate(available):
            command['id'] = 'engine:' + str(index)
        terminal = None
        if hero['hp']['current'] <= 0 and not runtime['combat']:
            terminal = dict(code='native_zero_hp_outside_combat',
                message='0 HP вне активного боя: восстановление/воскрешение требует отдельной штатной процедуры.')
        result = dict(hero=hero_view, combat=battle, commands=available, terminal=terminal)
        self.cache[key] = result
        while len(self.cache) > 16:
            self.cache.popitem(last=False)
        return deepcopy(result)
