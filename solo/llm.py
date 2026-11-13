"""Active cloud provider exports. Historical local adapter exists only in research reports."""
from .cloud import CloudLLM,CloudUnavailable
from .cloud_cost import BudgetExceeded

class MockLLM:
    """Explicit test dependency only; never a production AI fallback."""
    def __init__(self,plan=None,failure=False):
        self.plan=plan;self.failure=failure;self.metrics=[]
    async def available(self):return {'result':'MockLLM (тест)','mock':True}
    async def interpret(self,text,scene):
        if self.plan is None:raise ValueError('В тесте нужен явный эталонный план')
        self.metrics.append({'mode':'interpreter'})
        return self.plan.model_copy(deep=True)
    async def stream(self,system,user,**kwargs):
        self.metrics.append({'mode':'narrator'})
        yield 'Тестовый '
        if self.failure:raise RuntimeError('Сбой тестовой модели')
        yield 'ответ.'


from .model_protocol import ModelBehaviour


class CommandMockLLM(MockLLM, ModelBehaviour):
    """Scripted responses through the real Interpreter and Narrator protocols.

    Explicit offline dependency, keyed by player utterance. Unknown input asks for
    clarification. No network, model profile, credentials, or paid fallback.
    """
    def __init__(self, intents, *, narrative=None, failure=False):
        super().__init__(failure=failure)
        self.intents, self.narrative_override, self.packets = intents, narrative, []

    def scripted_intent(self, text):
        return self.intents.get(text, dict(
            status='clarification', action=None, message='Уточните одно действие и его цель.'))

    async def stream(self, system, user, **kwargs):
        import json
        from .narrative_grounding import fallback
        mode = kwargs.get('mode', 'narrator')
        self.metrics.append(dict(mode=mode))
        packet = json.loads(user)
        self.packets.append(dict(mode=mode, packet=packet))
        if mode == 'interpreter':
            value = self.scripted_intent(packet['player_intent'])
        else:
            if self.failure:
                raise RuntimeError('Сбой тестового рассказчика')
            value = self.narrative_override if self.narrative_override is not None else fallback(packet)
        yield json.dumps(value, ensure_ascii=False)
