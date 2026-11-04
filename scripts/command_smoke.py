"""Offline reproducible vertical slice: python -m scripts.command_smoke --output ..."""
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from solo.contracts import Hero, Resource
from solo.game import Game
from solo.llm import CommandMockLLM
from solo.storage import Store


SCENARIO = [
    ('Исследую следы', dict(kind='check', check_id='следы')),
    ('Добавляю базовый бой', dict(kind='start_combat', encounter_id='basic')),
    ('Иду на клетку 7, 4, 0', dict(kind='move', position=dict(x=7, y=4, z=0))),
    ('Заканчиваю ход', dict(kind='end_turn')),
    ('Атакую foe-1', dict(kind='attack', target='foe-1')),
    ('Завершаю бой', dict(kind='end_combat')),
    ('Короткий отдых с одной костью', dict(kind='short_rest', hit_dice=1)),
    ('Длительный отдых с пищей и водой', dict(kind='long_rest', food_water=True)),
]


class FixedDice:
    def __init__(self, values=(13, 18, 4, 15, 3, 15, 4)):
        self.values, self.used = iter(values), []

    def randint(self, low, high):
        value = next(self.values)
        if not low <= value <= high:
            raise AssertionError(f'Die {value} outside {low}..{high}')
        self.used.append(value)
        return value


def setup(directory, *, llm=None):
    store = Store(Path(directory) / 'commands.sqlite3')
    hero = Hero(name='Мирон', hp=14, max_hp=20, level=2, ac=12,
                abilities=dict(strength=30, dexterity=14, constitution=14, intelligence=10, wisdom=10, charisma=10),
                resources=dict(resolve=Resource(current=0, maximum=1, recovery='short'),
                               focus=Resource(current=0, maximum=2, recovery='long')))
    state = dict(location='Каменный двор', turn=0, facts=[], deferred=[],
                 locations=[dict(name='Каменный двор', description='Каменный двор.', npc=[], objects=['следы'], facts=[],
                                 checks={'следы': dict(ability='wisdom', skill='survival', dc=12)})])
    campaign = store.create('Вертикальный сценарий', hero, initial_state=state)['id']
    mock = llm or CommandMockLLM({text: dict(status='command', action=action, message='') for text, action in SCENARIO})
    return store, campaign, Game(store, mock)


import re

_UUID = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _scrub(value):
    """Generated identifiers replaced, so two replays compare equal."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _UUID.sub('<id>', value)
    return value


async def run_scenario(directory):
    store, campaign, game = setup(directory)
    dice, trace = FixedDice(), []
    for index, (text, _) in enumerate(SCENARIO):
        version = store.campaign(campaign)['version']
        result = await game.play_command(campaign, f'demo-{index + 1}', text, version, rng=dice)
        if result['status'] != 'resolved':
            raise AssertionError(result)
        # Normalize generated IDs only; commands, dice, effects and narrative stay intact.
        result = _scrub(result)
        result.pop('journal_id', None)
        for event in result['events']:
            event['campaign_id'] = 'demo'
        trace.append(dict(player_intent=text, version_before=version, version_after=store.campaign(campaign)['version'], **result))
    return dict(scenario='exploration-combat-rest-v1', paid_api_calls=0, dice=dice.used, turns=trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/command-cycle.json'))
    args = parser.parse_args()
    with TemporaryDirectory(prefix='solo-command-') as directory:
        trace = asyncio.run(run_scenario(directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(f"OK: {len(trace['turns'])} commands; dice={trace['dice']}; paid API calls=0; {args.output.resolve()}")


if __name__ == '__main__':
    main()
