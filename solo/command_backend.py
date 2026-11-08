"""Select one command backend from canonical campaign state, without migration."""
from .command_engine import CommandEngine
from .game_commands import GameCommand


def native_backend(store, current):
    # Presence, not truthiness: corrupt/unsupported Nat20 state must fail closed.
    # Lazy import keeps the optional dependency out of ordinary Solo campaigns.
    if 'nat20' in current['state']:
        from .nat20_api import Nat20GameEngine
        return Nat20GameEngine(store)
    return None


def command_backend(store, current, *, rng=None):
    return native_backend(store, current) or CommandEngine(store, rng=rng)


def stored_command(state, raw):
    """Read older native envelopes through their existing strict contract."""
    if 'nat20' in state:
        from .nat20_contracts import GameCommand as NativeCommand
        raw = NativeCommand.model_validate(raw).model_dump(exclude={'contract_version', 'rules_version'})
    return GameCommand.model_validate(raw)
