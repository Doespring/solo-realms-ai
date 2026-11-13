"""Select one command backend from canonical campaign state, without migration."""
from .command_engine import CommandEngine
from .game_commands import GameCommand

# r210: кампания без ``nat20`` — сохранение прежней версии. Владелец решил
# (03.11.2026) не тянуть старые хвосты: режима совместимости нет, ручной
# калькулятор вырезан из пользовательских путей. Эти две фразы — единственный
# текст отказа, его читает и сервер, и экран.
LEGACY_CODE = 'legacy_campaign_unsupported'
LEGACY_REFUSAL = ('Эта кампания создана в прежней версии и не открывается: '
                  'ручной калькулятор отключён, режима совместимости нет. '
                  'Начните новую кампанию.')
CREATION_CODE = 'nat20_scene_required'
CREATION_REFUSAL = ('Кампания создаётся только с движком Nat20: ручной лист героя '
                    'больше не принимается. Начните приключение на экране Nat20.')


def legacy_refusal():
    return dict(code=LEGACY_CODE, message=LEGACY_REFUSAL)


def creation_refusal():
    return dict(code=CREATION_CODE, message=CREATION_REFUSAL)


def is_native(current):
    """Presence of the marker, not truthiness: a corrupt Nat20 state fails closed."""
    return 'nat20' in current['state']


def native_backend(store, current):
    # Lazy import keeps the optional dependency out of ordinary Solo campaigns.
    if is_native(current):
        from .nat20_api import Nat20GameEngine
        return Nat20GameEngine(store)
    return None


def command_backend(store, current, *, rng=None):
    """The backend for reads that still have to work: one calculator, Nat20.

    No user route reaches the legacy calculator any more (r210). The fallback
    stays for reading a campaign's own history by direct call — it is not a
    second way to play: every HTTP entry checks ``is_native`` before resolving.
    """
    return native_backend(store, current) or CommandEngine(store, rng=rng)


def stored_command(state, raw):
    """Read older native envelopes through their existing strict contract."""
    if 'nat20' in state:
        from .nat20_contracts import GameCommand as NativeCommand
        raw = NativeCommand.model_validate(raw).model_dump(exclude={'contract_version', 'rules_version'})
    return GameCommand.model_validate(raw)
