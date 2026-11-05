"""Test-only d20 adapter to existing Game Engine, on an owned temporary DB.

No application route imports this module. It never dispatches PDF opcodes or AI
responses. Only an explicit trusted CheckCommand can cross the commit boundary.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import secrets
import tempfile

from solo.contracts import Hero, Step
from solo.rulesets import BUILTINS, RulesetEngine, active, digest
from solo.storage import Store


@dataclass(frozen=True)
class CheckCommand:
    request_id: str
    expected_version: int
    rules_family: str
    ruleset_digest: str
    advantage_sources: int = 0
    disadvantage_sources: int = 0
    ability: str = "strength"
    dc: int = 15
    proficient: bool = False
    operation: str = "ability_check"


class IsolatedCheckAdapter:
    def __init__(self, *, hero=None, ruleset_id="dnd2024", rng=None):
        self._directory = tempfile.TemporaryDirectory(prefix="solo-hybrid-check-")
        self._root = Path(self._directory.name).resolve()
        self._temp_parent = Path(tempfile.gettempdir()).resolve()
        try:
            self.store = Store(self._root / "engine.sqlite3")
            self.campaign_id = self.store.create(
                "Isolated hybrid d20 test", hero or Hero(name="Rules test"),
                BUILTINS[ruleset_id].model_copy(deep=True),
            )["id"]
            self.rng = rng if rng is not None else secrets.SystemRandom()
        except BaseException:
            self.close()
            raise

    @property
    def root(self):
        return self._root

    def _assert_owned_store(self):
        if self.store.path.resolve() != self._root / "engine.sqlite3":
            raise ValueError("test adapter may only use its own temporary database")

    def command(self, request_id, **fields):
        self._assert_owned_store()
        current = self.store.campaign(self.campaign_id)
        return CheckCommand(
            request_id=request_id, expected_version=current["version"],
            rules_family="dnd2024", ruleset_digest=digest(active(current["state"]).model_dump()),
            **fields,
        )

    def execute(self, command):
        self._assert_owned_store()
        if type(command) is not CheckCommand:
            raise ValueError("an AI response is a proposal, not a trusted CheckCommand")
        if command.operation != "ability_check":
            raise ValueError("unregistered test operation")
        if type(command.request_id) is not str or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", command.request_id):
            raise ValueError("invalid request identifier")
        if type(command.expected_version) is not int or command.expected_version < 0:
            raise ValueError("invalid expected version")
        for count in (command.advantage_sources, command.disadvantage_sources):
            if type(count) is not int or not 0 <= count <= 1000:
                raise ValueError("source counts must be bounded nonnegative integers")
        if type(command.proficient) is not bool or type(command.dc) is not int:
            raise ValueError("invalid trusted check parameters")
        step = Step(
            kind="check", text="Isolated verified ability check", ability=command.ability,
            dc=command.dc, proficient=command.proficient,
            advantage=command.advantage_sources > 0,
            disadvantage=command.disadvantage_sources > 0,
        )
        signature = digest(asdict(command))
        with self.store.connection(True) as connection:
            current = self.store.campaign(self.campaign_id, connection)

            def transition(state):
                # engine_event handles exact replays first; these checks apply to
                # a new command inside the existing BEGIN IMMEDIATE transaction.
                preset = active(state)
                if command.rules_family != "dnd2024" or preset.edition != "2024" or preset.id != "dnd2024":
                    raise ValueError("edition mismatch")
                if command.expected_version != current["version"]:
                    raise ValueError("stale state")
                if command.ruleset_digest != digest(preset.model_dump()):
                    raise ValueError("ruleset fingerprint mismatch")
                result = RulesetEngine(preset).check(Hero.model_validate(state["hero"]), step, self.rng)
                return [{"type": "scripted", "kind": "isolated_ability_check", "check": result,
                         "trust": "existing_engine_subset", "ai_proposal_applied": False}]

            return self.store.engine_event(
                connection, current, command.request_id, step.text, transition, signature,
            )

    def close(self):
        # Verify the resolved recursive-cleanup target is the directory allocated
        # by this object under the original system temp parent.
        target = Path(self._directory.name).resolve()
        if target != self._root or target.parent != self._temp_parent or not target.name.startswith("solo-hybrid-check-"):
            raise ValueError("refusing cleanup outside owned test directory")
        self._directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
