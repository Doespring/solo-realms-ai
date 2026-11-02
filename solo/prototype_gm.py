"""Presentation and preparation adapters for the existing GM; no mechanical rules."""
import json
import hashlib
import re

from .narrative_grounding import CanonViolation, fallback, render, verify, sentences
from .prompts import NARRATOR_ROLE


def dialogue_memories(records, query=''):
    """Typed knowledge from an already ACL-scoped builder, never state audit rows.

    npc_state (goals, beliefs, etc.) describes storage transitions, not something
    an NPC said or knows about the player's topic. Do not make it quotable merely
    because its truth is fact. Keep original evidence metadata for verification.
    """
    terms = {w for w in re.findall(r'[^\W\d_]+', query.casefold()) if len(w) >= 3}
    def relevance(text):
        words = re.findall(r'[^\W\d_]+', text.casefold())
        return sum(any(word.startswith(term) or term.startswith(word) for word in words) for term in terms)
    selected = []
    for row in records:
        if row.get('truth') != 'fact' or row.get('kind') not in ('npc_knowledge', 'knowledge', 'fact'):
            continue
        # Only remove explicit authoring labels; all remaining claims are literal
        # source sentences. Never summarize JSON/state transitions into knowledge.
        content = re.sub(r'(?m)^\s*(?:Описание|Знание)\s*:\s*', '', row.get('content', ''))
        parts = sorted(sentences(content), key=relevance, reverse=True)
        if parts:
            selected.append(dict(row, content=' '.join(parts)))
    return sorted(selected, key=lambda row: relevance(sentences(row['content'])[0]), reverse=True)


async def present_intent(game, current, request_id, text, intent, context, *, utterance=None):
    """Narrate a public scene or one NPC's evidence. No state/HP/action mutation."""
    from .command_backend import command_backend
    utterance = utterance or text
    place = context['scene']
    people = place.get('npc', [])
    target = intent.target.casefold().strip()
    selected = [n for n in people if target in (str(n.get('id', '')).casefold(), n['name'].casefold())] if target else people
    # The interpreter says 'observe'; the reply contract says 'observation'.
    # Passing the raw value through made every look-around answer 500.
    status = 'observation' if intent.status == 'observe' else intent.status
    conversation = intent.status == 'conversation'
    if conversation and len(selected) != 1:
        message = 'С кем вы хотите поговорить? ' + ', '.join(n['name'] for n in people) if people else 'Здесь нет доступного собеседника. Выберите другое действие.'
        game.store.converse(current['id'], request_id, text, message, 'clarification',
                            dict(command_cycle=True, command_status='clarification', intent_text=text, expected_version=current['version']))
        return dict(status='clarification', command=None, events=[], state_version=current['version'],
                    snapshot=context['game_snapshot'], narrative=message)
    packet = dict(packet_version=2, place=place['name'], atmosphere=place['description'],
                  confirmed=place.get('facts', []), npc=[], memory=context['memory_context']['memories'],
                  # A look-around that changes nothing must not replay the scene.
                  observation=not conversation)
    if conversation:
        person = selected[0]
        packet['npc'] = [dict(name=person['name'], confirmed_knowledge=person.get('known', ''), id=person.get('id'))]
        packet['player_words'] = [dict(to=person['name'], words=utterance)]
        if person.get('id'):
            snapshot = command_backend(game.store, current).snapshot(current)
            own = game.compiler.engine_context(current['id'], npc_id=person['id'], query=utterance,
                                               current_snapshot=snapshot.model_dump(mode='json'))
            # NPC knowledge is scoped by the existing builder; other NPCs' memories never merge.
            packet['npc'][0]['memory'] = dialogue_memories(own['memories'], utterance)
            packet['memory'] = []
    system = NARRATOR_ROLE + '\nТолько подтверждённая видимая сцена и знания выбранного NPC. Не меняй ресурсы, мир или действия героя. Не добавляй новые факты. proposals=[].'
    # One catch-all used to report a provider outage, a malformed reply and a
    # narrator inventing a fact as the same 'engine_fallback'. Those need
    # different answers: an outage is worth retrying, a rejected claim is not,
    # and a broken format is a bug in the transport. The reason is recorded
    # beside the policy so the diagnostics can tell them apart.
    reason = ''
    try:
        with game.model_scope(current['id'], request_id):
            async for _ in game.llm.narrate_scene(system, json.dumps(packet, ensure_ascii=False), narrative_format='structured'):
                pass
        structure = verify(game.llm.last_narrative, packet)
        policy = 'engine_fallback' if getattr(game.llm,'last_narrative_policy','').startswith('engine_') else 'engine_verified'
        if policy == 'engine_fallback':reason = 'provider'
    except CanonViolation:
        # The narrator said something the confirmed scene does not support.
        # Retrying the same request would only ask it again to invent.
        structure, policy, reason = fallback(packet), 'engine_rejected', 'rejected_claim'
    except (ConnectionError, TimeoutError, OSError) as error:
        structure, policy, reason = fallback(packet), 'engine_fallback', 'provider:' + type(error).__name__
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # A malformed structured reply: a transport/format fault, not a claim.
        structure, policy, reason = fallback(packet), 'engine_fallback', 'format:' + type(error).__name__
    narrative = render(structure)
    # Sanitise where the text is born, before it is stored: this value becomes
    # record_scenario(), store.narrative() and the HTTP reply, and the renderer
    # below is not the only reader. The rules layer names counters such as
    # 'attacks_remaining: 0' that must never reach the table or the log.
    if not narrative.strip():
        # An empty narration would show the player a silent GM bubble while the
        # event still claims 'complete'. Say that nothing was produced instead.
        narrative = 'Мастер не смог описать сцену. Попробуйте иначе.'
    # player_text is a MODULE function (solo.game.player_text), not a method of
    # Game. An earlier getattr(game, ...) guard silently evaluated to False and
    # made this line a no-op, so narrator text kept leaking engine counters.
    from .game import player_text
    narrative = player_text(narrative)
    latest = game.store.campaign(current['id'])
    if latest['version'] != current['version']:
        return dict(status='state_version_error', command=None, events=[], state_version=latest['version'],
                    snapshot=command_backend(game.store, latest).visible(command_backend(game.store, latest).snapshot(latest)),
                    narrative='Сцена изменилась. Повторите намерение в обновлённой сцене.')
    def record(state):
        if game.store.campaign(current['id'])['version'] != current['version']:
            raise ValueError('Сцена изменилась. Повторите намерение.')
        evidence = [dict(text=row['text'], known_by=['hero', selected[0]['name']],
                         source_ref='conversation:' + request_id, provenance='npc_statement', truth='statement',
                         scope='private', kind='conversation') for row in structure['dialogue']] if conversation else []
        # 'status' was never a local name here: the value lives on the intent.
        # The line had never run, so every observation of a scene answered 500.
        return [dict(type='scripted', kind=status, text=narrative, memory_evidence=evidence)]
    journal = game.store.record_scenario(current['id'], 'social-' + hashlib.sha256(request_id.encode()).hexdigest()[:32], text, record)
    game.store.narrative(journal['id'], narrative, 'complete', structure=structure)
    after = game.store.campaign(current['id'])
    # Already sanitised at birth (see 'narrative = player_text' above), so every
    # store on this path receives the same clean text.
    # No db here: the memory step below opens the next write transaction, and
    # passing a connection that closed with its block would be worse than the
    # duplicate transaction this call used to open.
    message = game.store.converse(current['id'], request_id, text, narrative, 'notice',
                                 dict(command_cycle=True, command_status=status, expected_version=after['version'], journal_id=journal['id']))
    # Use existing Memory.put/ACL and statement provenance, never world facts from prose.
    with game.store.connection(True) as db:
        latest = game.store.campaign(current['id'], db)
        if latest['version'] != after['version']:
            return dict(status='state_version_error', command=None, events=[], state_version=latest['version'],
                        snapshot=command_backend(game.store, latest).visible(command_backend(game.store, latest).snapshot(latest)),
                        narrative='Сцена изменилась. Повторите намерение в обновлённой сцене.')
        ids = game.store.memory.identities(current['id'], current['state'])
        recipients = [ids['hero']]
        if conversation and ids['npc'].get(selected[0]['name']):
            recipients.append(ids['npc'][selected[0]['name']])
        if conversation:
            game.store.memory.put(db, key='conversation:' + message['id'], campaign=current['id'], event=journal['id'],
                                  kind='conversation', provenance='player_statement', truth='statement', scope='private',
                                  location=ids['location'], text=utterance, importance=4, minutes=int(current['state'].get('elapsed_minutes', 0)),
                                  recipients=recipients, subjects=recipients, source_ref='conversations:' + message['id'])
    return dict(status=status, command=None, events=[], state_version=after['version'],
                snapshot=command_backend(game.store, after).visible(command_backend(game.store, after).snapshot(after)),
                narrative=narrative, narrative_policy=policy, narrative_reason=reason, journal_id=journal['id'])
