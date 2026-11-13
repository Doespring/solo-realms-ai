"""Offline wire measurements + read-only aggregate usage; never exports prompts/keys.

Run from the project root: python -m scripts.playable_ai_readiness
All generation uses httpx.MockTransport and a temporary settings/vault/ledger.
The CLI never opens personal ledgers. --saved-usage-from reuses numeric aggregates.
The separately callable audit helper uses SQLite mode=ro, never CostLedger.
"""
import argparse
import asyncio
import json
import sqlite3
import statistics
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from solo.ai_roles import routerai_preset
from solo.cloud import CloudLLM
from solo.cloud_context import estimate_context
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudSettings
from scripts.command_smoke import SCENARIO, FixedDice, setup
from solo.contracts import Hero, Master, Plan
from solo.game import Game
from solo.narrative_grounding import fallback
from solo.storage import Store


class MockVault:
    def get(self, profile):
        return 'synthetic-readiness-key'

    def exists(self, profile):
        return True


def mock_cloud(directory, respond):
    profile = routerai_preset()
    profile.enabled = True
    for key in ('max_request_cost', 'session_budget', 'daily_budget', 'monthly_budget', 'preparation_budget'):
        setattr(profile, key, Decimal('100'))
    profile.total_budget = Decimal('100')
    # Подготовка мира идёт через preparation_role (analyst), а не через interpreter/
    # narrator: без тарифа у этой роли ledger отказывает «Неизвестна стоимость
    # модели», и любой прогон подготовки падает с failed ещё до первого вызова.
    for role in ('interpreter', 'narrator', 'pdf_analyst', 'secondary_narrator'):
        cfg = getattr(profile, role)
        cfg.input_per_million, cfg.output_per_million = Decimal('1'), Decimal('2')
        cfg.reasoning_control = 'routerai_enabled'
        cfg.parameters.append('reasoning')
    settings = CloudSettings(Path(directory) / 'profiles.json', MockVault())
    settings.save(profile)
    ledger = CostLedger(Path(directory) / 'usage.sqlite3')
    return CloudLLM(settings, ledger, client=httpx.AsyncClient(transport=httpx.MockTransport(respond))), profile


def summarize_wire(body, metric, label):
    """Only field names, counts and planning numbers, even for synthetic requests."""
    packet = json.loads(body['messages'][-1]['content'])
    system = body['messages'][0]['content']
    user = body['messages'][-1]['content']
    context = packet.get('context', {})
    tools = packet.get('tools', [])
    return dict(label=label, role=metric['mode'], prompt_version=metric['prompt_version'],
                message_roles=[m['role'] for m in body['messages']], user_fields=sorted(packet),
                context_fields=sorted(context), tool_names=[t['function']['name'] for t in tools],
                context_component_utf8_bytes={k: len(json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode('utf8')) for k, v in context.items()},
                system_utf8_bytes=len(system.encode('utf8')), user_utf8_bytes=len(user.encode('utf8')),
                tool_schema_utf8_bytes=len(json.dumps(tools, ensure_ascii=False, separators=(',', ':')).encode('utf8')) if tools else 0,
                schema_in_system='JSON schema (' in system,
                native_response_format=body.get('response_format', {}).get('type'),
                planning=metric['context_estimate'], output_limit=body.get('max_tokens', body.get('max_completion_tokens')),
                context_limit=metric['context_limit_tokens'],
                reserve=metric['reserved_cost'], cost=metric['cost'], cost_source=metric['cost_source'],
                actual_input_tokens=metric.get('input_tokens'), actual_output_tokens=metric.get('output_tokens'),
                usage_kind='synthetic; not calibration evidence')


async def measure_packets(directory):
    pending, measurements = [], []
    actions = {text: action for text, action in SCENARIO}
    def respond(request):
        body = json.loads(request.content)
        packet = json.loads(body['messages'][-1]['content'])
        pending.append(body)
        if 'player_intent' in packet:
            value = dict(status='command', action=actions[packet['player_intent']], message='')
        elif 'реплика' in packet:
            value = dict(summary='Осмотр', actions=[dict(id='a', category='ACTION_ATTEMPT', kind='check',
                         text=packet['реплика'], target='следы', item='', disposition='now', condition='',
                         condition_state='not_applicable', after=[])], unclear=[], branches=[])
        else:
            value = fallback(packet)
        usage = dict(prompt_tokens=1000 if 'player_intent' in packet or 'реплика' in packet else 500,
                     completion_tokens=80, cost='0.001', currency='RUB')
        frames = [dict(choices=[dict(delta=dict(content=json.dumps(value, ensure_ascii=False)))]), dict(choices=[], usage=usage)]
        content = ''.join('data: ' + json.dumps(f, ensure_ascii=False) + '\n\n' for f in frames) + 'data: [DONE]\n\n'
        return httpx.Response(200, content=content.encode('utf8'), headers={'content-type': 'text/event-stream'})
    llm, _ = mock_cloud(directory, respond)
    store, cid, game = setup(Path(directory) / 'game', llm=llm)
    try:
        dice = FixedDice()
        for index, (text, action) in enumerate(SCENARIO):
            start = len(llm.metrics)
            result = await game.play_command(cid, f'readiness-{index}', text, store.campaign(cid)['version'], rng=dice)
            if result['status'] != 'resolved':
                raise AssertionError(result['status'])
            for body, metric in zip(pending, llm.metrics[start:]):
                measurements.append(summarize_wire(body, metric, action['kind']))
            pending.clear()
        # Also inspect the existing legacy /play Interpreter and compiler/Narrator.
        await llm.interpret('Осматриваю следы', game.compiler.interpreter(store.campaign(cid)))
        measurements.append(summarize_wire(pending.pop(), llm.metrics[-1], 'legacy_interpreter'))
        legacy = Store(Path(directory) / 'legacy.sqlite3')
        legacy_id = legacy.create('Synthetic', Hero(name='Мирон'), master=Master(narrator_format='structured'))['id']
        plan = Plan(summary='Осмотр', steps=[dict(kind='observe', text='Смотрю на окно')])
        staged = legacy.stage(legacy_id, 'look', 'Смотрю на окно', plan, 0, dict(natural=True))
        event = legacy.apply(staged['id'])
        # Use the orchestrator too: Master.max_tokens can be lower than the role cap.
        async for _ in Game(legacy, llm).narrate(event['id']):
            pass
        measurements.append(summarize_wire(pending.pop(), llm.metrics[-1], 'legacy_narrator'))
    finally:
        await llm.close()
    examples = []
    for kind, text in [('ru', 'Привет, Мирон!'), ('json', '{"герой":"Мирон","hp":12}'), ('mixed', 'D&D: бросок d20 → 17; HP=12 😀')]:
        examples.append(dict(kind=kind, **estimate_context([dict(role='system', content='Роль'), dict(role='user', content=text)])))
    return dict(reference_examples=examples, wire_measurements=measurements,
                live_calls=0, prices='synthetic 1 input / 2 output RUB per million; not current provider prices')


async def measure_native_packets(directory):
    """Current experimental wizard capabilities, through the same cloud seam."""
    from solo.nat20_api import Nat20GameEngine
    pending, measurements = [], []
    actions = {
        'Проверяю ловкость': dict(kind='check', check_id='save-dex'),
        'Начинаю бой': dict(kind='start_combat', encounter_id='rats'),
                # Волшебные снаряды: заклинание с числом целей адресуется target_ids
        # (2 + @item.level = 3 на слоте 1). Сейчас движок отклоняет ЛЮБОЙ
        # такой каст: _reject_untargeted_cast требует target_id, а
        # заклинание с числом целей его по определению не имеет. Дефект и
        # готовый патч описаны в reports/ENGINE_WORKSPACE_PROGRESS.md
        # («НАЙДЕНО r119b»). Набор целей не подгоняем — это замаскировало
        # бы дефект движка.
        'Применяю волшебные снаряды': dict(kind='native_action', operation='cast_spell',
            spell_id='magic-missile', target_ids=['rat-a', 'rat-b', 'rat-a'], slot_level=1),  # Слот 1 = 2 + уровень = 3 снаряда: движок требует ровно count целей  # Два попадания по двум целям: три цели нарушают выбрасывание по существам
    }
    def respond(request):
        body = json.loads(request.content)
        pending.append(body)
        packet = json.loads(body['messages'][-1]['content'])
        value = dict(status='command', action=actions[packet['player_intent']], message='') if 'player_intent' in packet else fallback(packet)
        frames = [dict(choices=[dict(delta=dict(content=json.dumps(value, ensure_ascii=False)))]),
                  dict(choices=[], usage=dict(prompt_tokens=1000, completion_tokens=80))]
        content = ''.join('data: ' + json.dumps(f, ensure_ascii=False) + '\n\n' for f in frames) + 'data: [DONE]\n\n'
        return httpx.Response(200, content=content.encode('utf8'), headers={'content-type': 'text/event-stream'})
    llm, _ = mock_cloud(directory, respond)
    store = Store(Path(directory) / 'native.sqlite3')
    build = dict(species_slug='human', class_slug='wizard', level=1, background_slug='soldier',
        ability_scores=dict(strength=15, dexterity=13, constitution=14, intelligence=12, wisdom=10, charisma=8),
        # Набор характеристик обязан назвать способ генерации: иначе Nat20 не
        # может отличить законный набор от шести свободных чисел.
        ability_score_method='standard_array',
        equipment=['dagger', 'potion-of-healing'], selected_choices=[])
    # Положенные выборы (навык, черта вида, предыстория) берём у Nat20, а не
    # перечисляем руками: иначе сборка недобирает и умирает на валидации.
    _legal = Path(__file__).resolve().parent.parent / 'tests'
    import sys as _sys
    if str(_legal) not in _sys.path:
        _sys.path.insert(0, str(_legal))
    from nat20_builds import legal_picks
    from nat20_builds import loader as _loader
    from dnd5e_engine import CharacterBuildSpec as _Spec
    build['selected_choices'] = tuple(legal_picks(_Spec(**build), corpus=_loader()))
    source = dict(kind='authored_scenario', id='readiness', version='1', note='Synthetic authored encounter; no AI mechanics')
    encounter = dict(grid=dict(width=9, height=6), hero_position=dict(x=1, y=1, z=0), actors=[
        dict(id='rat-a', monster='rat', position=dict(x=2, y=1, z=0), attack_bonus=2, attack_bonus_source=source),
        dict(id='rat-b', monster='rat', position=dict(x=5, y=1, z=0), attack_bonus=2, attack_bonus_source=source)])
    try:
        campaign = Nat20GameEngine(store).create_experimental_campaign('Искатель', build=build,
            encounters={'rats': encounter}, prepared_spells=['magic-missile'], seed=5)
        cid = campaign['id']
        game = Game(store, llm)
        for index, (text, action) in enumerate(actions.items()):
            start = len(llm.metrics)
            result = await game.play_command(cid, f'native-readiness-{index}', text, store.campaign(cid)['version'])
            assert result['status'] == 'resolved', result['status']
            for body, metric in zip(pending, llm.metrics[start:]):
                measurements.append(summarize_wire(body, metric, 'native_' + action['kind']))
            pending.clear()
    finally:
        await llm.close()
    return measurements


def saved_usage_summary(path):
    """Aggregate numeric columns only; never query requests, responses, keys or IDs."""
    path = Path(path)
    if not path.exists():
        return dict(available=False)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute('PRAGMA table_info(calls)')}
        estimate = 'input_estimate_details' if 'input_estimate_details' in columns else "'{}' AS input_estimate_details"
        rows = list(db.execute('SELECT purpose,status,input_tokens,output_tokens,usage_source,' + estimate + ' FROM calls'))
    actual = [r for r in rows if r['usage_source'] == 'provider' and isinstance(r['input_tokens'], int)]
    pairs = []
    for row in actual:
        try:
            value = json.loads(row['input_estimate_details']).get('input_tokens_estimate')
        except (ValueError, TypeError, AttributeError):
            value = None
        if isinstance(value, int) and value > 0 and row['input_tokens'] > 0:
            pairs.append(value / row['input_tokens'])
    roles = {}
    for role in ('interpreter', 'narrator', 'secondary_narrator'):
        relevant = [r for r in actual if r['purpose'] == role]
        inputs = [r['input_tokens'] for r in relevant]
        outputs = [r['output_tokens'] for r in relevant if isinstance(r['output_tokens'], int)]
        roles[role] = dict(calls=len(relevant), actual_input_min=min(inputs, default=None), actual_input_max=max(inputs, default=None),
                           actual_output_min=min(outputs, default=None), actual_output_max=max(outputs, default=None))
    return dict(available=True, calls=len(rows), provider_usage=len(actual), paired_planning_and_actual=len(pairs),
                median_estimate_to_actual_ratio=statistics.median(pairs) if pairs else None, roles=roles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/playable-ai-measurements.json'))
    parser.add_argument('--saved-usage-from', type=Path, help='Reuse an existing aggregate JSON; does not open personal ledgers')
    args = parser.parse_args()
    saved = json.loads(args.saved_usage_from.read_text(encoding='utf8'))['saved_usage'] if args.saved_usage_from else None
    with TemporaryDirectory(prefix='solo-readiness-') as directory:
        result = asyncio.run(measure_packets(directory))
        native = Path(directory) / 'native'
        native.mkdir()
        result['native_wire_measurements'] = asyncio.run(measure_native_packets(native))
    result['saved_usage'] = saved
    result['saved_usage_source'] = 'Historical aggregate reused; not a fresh personal-ledger audit' if saved else 'Not collected; no personal ledger opened'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(output=str(args.output), measured_requests=len(result['wire_measurements']) + len(result['native_wire_measurements']), live_calls=0), ensure_ascii=True))


if __name__ == '__main__':
    main()
