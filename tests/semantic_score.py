"""Reference matcher for current interpreter regression cases."""
def normalize(value):return ' '.join(str(value).lower().replace('ё','е').split())
def matches(step,goal):
    text=normalize(step.get('target','')+' '+step.get('text',''))
    return step['kind'] in goal['kinds'] and (not goal['target_any'] or any(normalize(t) in text for t in goal['target_any']))
def score(case,plan):
    errors=[];critical=[];used=set();order=[];steps=plan['steps']
    for i,g in enumerate(case['goals']):
        choices=[(j,s) for j,s in enumerate(steps) if j not in used and matches(s,g)]
        correct=[(j,s) for j,s in choices if s['disposition']==g['when']]
        if not correct:
            errors.append({'category':'missing_action' if not choices else {'negated':'negation','conditional':'condition','intended':'deferred'}.get(g['when'],'intent'),'goal':i})
            if g['when']!='now' and any(s['disposition']=='now' for j,s in choices):critical.append(i)
            continue
        j,s=correct[0];used.add(j);order.append(j)
        if g['condition_any'] and not any(normalize(t) in normalize(s['condition']) for t in g['condition_any']):errors.append({'category':'missing_condition','goal':i})
    if order!=sorted(order):errors.append({'category':'order'})
    if case['clarify'] and not plan['unclear']:errors.append({'category':'ambiguity'})
    for forbidden in case['forbid']:
        if any(s['disposition']=='now' and matches(s,forbidden) for s in steps):critical.append('undeclared_action')
    return {'passed':not errors and not critical,'errors':errors,'critical':critical}
