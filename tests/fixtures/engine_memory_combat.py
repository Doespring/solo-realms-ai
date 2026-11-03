"""Hand-authored resolved mechanics. This fixture does not execute D&D rules.

The complete battle uses the provisional memory-v1 contract, not a Nat20 DTO.
"""
from copy import deepcopy


def combat(campaign):
    witnesses = ['hero', 'irina', 'guard']
    public = dict(scope='public', known_by=witnesses)
    private = lambda who: dict(scope='private', known_by=[who])
    actors = []
    for actor_id, kind, name, hp, position in [
        ('hero', 'hero', 'Мирон', 20, [0, 0, 0]),
        ('irina', 'npc', 'Ирина', 12, [1, 0, 0]),
        ('guard', 'npc', 'Страж', 12, [2, 0, 0]),
    ]:
        actors.append(dict(id=actor_id, kind=kind, name=name, **public,
                           hp=dict(current=hp, maximum=hp, **public),
                           resources={'slot': dict(current=1, **private(actor_id))},
                           conditions=[], position=dict(coordinates=position, **public)))
    actors[2]['resources']['secret_reserve'] = dict(current=7, **private('guard'))
    actors[2]['conditions'].append(dict(id='secret_curse', scope='secret', known_by=['guard']))
    snapshot = dict(contract_version='memory-v1', campaign_id=campaign, event_id='',
                    branch_id='main', sequence=0, location='Таверна «Последний огонь»',
                    actors=actors, combat=dict(id='fight-1', active=True, round=1, turn='hero', **public))
    result = []

    def emit(kind, payload, subjects, access=public):
        seq = len(result) + 1
        event = dict(contract_version='memory-v1', id=f'fight-{seq}', campaign_id=campaign,
                     branch_id='main', sequence=seq, parent_event_id=f'fight-{seq-1}' if seq > 1 else None,
                     type=kind, payload=payload, subjects=subjects, source_ref=f'fixture:fight-1/{seq}',
                     game_minutes=seq, **access)
        snapshot.update(event_id=event['id'], sequence=seq)
        result.append((deepcopy(event), deepcopy(snapshot)))

    emit('combat_started', dict(combat_id='fight-1', participants=witnesses), witnesses)
    emit('roll', dict(kind='attack', dice=[15], total=19, success=True), ['hero', 'guard'])
    actors[0]['resources']['slot']['current'] = 0
    emit('resource', dict(target='hero', resource='slot', before=1, after=0), ['hero'], private('hero'))
    actors[2]['hp']['current'] = 5
    emit('damage', dict(target='guard', amount=7, before=12, after=5), ['hero', 'guard'])
    actors[2]['conditions'].append(dict(id='prone', **public))
    emit('condition', dict(target='guard', condition='prone', active=True), ['guard'])
    actors[0]['position']['coordinates'] = [1, 1, 0]
    emit('movement', dict(target='hero', before=[0, 0, 0], after=[1, 1, 0]), ['hero'])
    snapshot['combat'].update(turn='guard', round=2)
    emit('roll', dict(kind='attack', dice=[12], total=16, success=True), ['guard', 'hero'])
    actors[0]['hp']['current'] = 16
    emit('damage', dict(target='hero', amount=4, before=20, after=16), ['guard', 'hero'])
    actors[0]['hp']['current'] = 19
    emit('healing', dict(target='hero', amount=3, before=16, after=19), ['hero', 'irina'])
    actors[2]['conditions'] = [c for c in actors[2]['conditions'] if c['id'] != 'prone']
    emit('condition', dict(target='guard', condition='prone', active=False), ['guard'])
    snapshot['combat'].update(turn='hero')
    emit('roll', dict(kind='attack', dice=[18], total=22, success=True), ['hero', 'guard'])
    actors[2]['hp']['current'] = 0
    emit('damage', dict(target='guard', amount=5, before=5, after=0), ['hero', 'guard'])
    snapshot['combat'].update(active=False, turn=None)
    emit('combat_ended', dict(combat_id='fight-1', participants=witnesses), witnesses)
    return result
