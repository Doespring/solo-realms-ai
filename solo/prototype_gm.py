"""Presentation and preparation adapters for the existing GM; no mechanical rules."""
import json
import hashlib
import re

from . import public_scene, scene_intents
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
    # A04/F07: в сцену игрока идут только открытые факты места. Страница книги
    # (source_text) и половина Мастера (private_facts) остаются в контракте, но в
    # пакет рассказчика не попадают: иначе «Осматриваюсь» пересказывает книгу —
    # заголовок эпизода, обзор приключения и планы ведущего.
    contract = place if place.get('public_scene') is not None else public_scene.scene_contract(place)
    scene_text = str(contract.get('public_scene') or '')
    confirmed = [fact for fact in place.get('facts', []) if not public_scene.is_service(fact)]
    target = intent.target.casefold().strip()
    selected = [n for n in people if target in (str(n.get('id', '')).casefold(), n['name'].casefold())] if target else people
    # A04/F08: реплика разбирается на части. Осмотр и вопрос о самой сцене — не
    # разговор, собеседник им не нужен; разговор — обращение к названному
    # человеку; вопрос Мастеру — ни то, ни другое. Одна часть не отменяет другие.
    plan = scene_intents.plan(utterance, people)
    # Названный собеседник — это разговор, даже если в реплике есть «осматриваюсь».
    # Осмотр перевешивает только тогда, когда человек не назван и не выбран: ровно
    # случай F08, где один стоящий рядом NPC делал из вопроса о сцене разговор.
    named = bool(target) and len(selected) == 1
    observing = intent.status == 'observe' or (bool(scene_intents.kinds(plan, 'observe')) and not named)
    conversation = intent.status == 'conversation' and not observing
    if conversation and len(selected) != 1:
        if not people:
            # Собеседника в сцене нет. Это не отказ хода: герой всё равно видит
            # место, и ответ на осмотр не отменяется пустым разговором.
            conversation, observing = False, True
        else:
            message = 'С кем вы хотите поговорить? ' + ', '.join(n['name'] for n in people)
            game.store.converse(current['id'], request_id, text, message, 'clarification',
                                dict(command_cycle=True, command_status='clarification', intent_text=text, expected_version=current['version']))
            return dict(status='clarification', command=None, events=[], state_version=current['version'],
                        snapshot=context['game_snapshot'], narrative=message)
    # The interpreter says 'observe'; the reply contract says 'observation'.
    # Passing the raw value through made every look-around answer 500.
    status = 'conversation' if conversation else 'observation'
    packet = dict(packet_version=2, place=place['name'], atmosphere=scene_text or str(place.get('description') or ''),
                  confirmed=confirmed, npc=[], memory=context['memory_context']['memories'],
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
    # Осмотр и разговор — тоже пересказ, а не сочинение: verify принимает только
    # дословно повторённое подтверждённое предложение. Прежняя общая роль просила
    # «художественное описание» (r250, живой прогон: «Дис поднимается из мглы, как
    # оттиск чужой воли…»), модель писала своё, валидатор справедливо это
    # отбрасывал, и игрок получал шаблон. Инструкция по статусу называет, ЧТО
    # повторять; сам verify не ослабляется, новых слов о мире в ответе быть не
    # может.
    if conversation:
        system += ('\nСейчас герой говорит; собеседник — ' + selected[0]['name'] + '. description — одно предложение из atmosphere или confirmed слово в слово. '
                   'Реплика — строка из confirmed_knowledge (или memory) этого NPC слово в слово; ответа, которого нет в пакете, не существует.')
    else:
        system += '\nСейчас герой осматривается. Ответ — пересказ предложений из atmosphere и confirmed: одно-три предложения, теми же словами, без единого нового утверждения о мире, погоде, звуках или людях.'
    # One catch-all used to report a provider outage, a malformed reply and a
    # narrator inventing a fact as the same 'engine_fallback'. Those need
    # different answers: an outage is worth retrying, a rejected claim is not,
    # and a broken format is a bug in the transport. The reason is recorded
    # beside the policy so the diagnostics can tell them apart.
    reason = ''
    try:
        with game.model_scope(current['id'], request_id):
            async for _ in game.llm.narrate_scene(system, json.dumps(packet, ensure_ascii=False), narrative_format='structured', grounded=True):
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
    if not conversation:
        # A04/F07: служебные фразы книги не проходят в сцену даже тогда, когда
        # рассказчик повторил их из воспоминаний кампании, а не из пакета.
        narrative = public_scene.for_player(narrative, fallback=scene_text)
    unmet = scene_intents.unmet(plan, people, conversation=conversation)
    if unmet:
        # A04/F08: составная реплика. Выполненная часть остаётся, невыполненная
        # называется по-русски — вместо отказа всему ходу.
        narrative = narrative.rstrip() + '\n\n' + '\n'.join(unmet)
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
