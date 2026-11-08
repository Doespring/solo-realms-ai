"""Closed production bridge: pinned PHB definitions -> existing local d20 tests.

Definition text/effects are never executable. New campaigns are not activated;
an existing Advanced session must explicitly obtain a campaign-scoped grant.
"""
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .contracts import Ability, Hero, Step, Strict
from .first_playable import require_full_scene
from .rule_registry import status
from .rulesets import SKILLS, RulesetEngine, active, digest

ROOT = Path(__file__).resolve().parents[1]
DEFINITION_ROOT = ROOT / "rules_data/v1/chapters/phb-01"
ENGINE_SHA256 = "a91628e7fd3ca0acd3729a5f2260de1bab5457c21827f3a9d046ff3a1eaa0f64"
PINS = (
    ("advantage", "13c0d2ac568357c5715f882fcd6a9dce96a1219b213cf3f4cd65ac35c0da0b59"),
    ("disadvantage", "8a9b89df3adb1efbf9898ec3a01947bd3fdf3b9af9fa12a506670a48e1685924"),
    ("cancellation", "7fc3cabb68fc6f34dcc3bfb55e615ceae693c8896bd619e37aa98759ca616a83"),
)
CHAPTER_PATH = ROOT / "rules_data/v2/chapters/phb-01/01-core.rules.json"
CHAPTER_SHA256 = "1c656711d63cad3eea0fd1f7c800d1fdee2b19a3f27b6443fb669c8bac071396"
# This is a release allowlist, never a dispatcher supplied by PDF or AI data.
CHAPTER_BINDINGS = {
    "ability_modifier": "RulesetEngine.d20", "ability_check": "RulesetEngine.check",
    "saving_throw": "RulesetEngine.saving_throw", "proficiency": "RulesetEngine.proficiency",
    "skill_check": "RulesetEngine.skill", "save_proficiency": "RulesetEngine.saving_throw",
}
OPERATIONS = ("ability_check", "skill_check", "saving_throw")


class BridgeInput(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Activation(BridgeInput):
    confirm: bool
    expected_version: int = Field(ge=0)
    rules_family: Literal["dnd2024"]
    rule_pack_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CheckCommand(BridgeInput):
    confirm: bool
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    expected_version: int = Field(ge=0)
    rules_family: Literal["dnd2024"]
    rule_pack_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ruleset_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["ability_check", "skill_check", "saving_throw"]
    ability: Ability
    skill: str = Field(default="", max_length=40)
    dc: int = Field(ge=1, le=40)
    proficient: bool = False
    advantage_sources: int = Field(default=0, ge=0, le=1000)
    disadvantage_sources: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def operation_inputs(self):
        if self.operation == "skill_check":
            if self.skill not in SKILLS or SKILLS[self.skill] != self.ability or self.proficient:
                raise ValueError("Registered skill/ability pair and sheet proficiency required")
        elif self.skill or (self.operation == "saving_throw" and self.proficient):
            raise ValueError("Saving throw proficiency comes only from the confirmed sheet")
        return self


class CheckProposal(BridgeInput):
    expected_version: int = Field(ge=0)
    rules_family: Literal["dnd2024"]
    rule_pack_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ruleset_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["ability_check", "skill_check", "saving_throw"]
    ability: Ability
    skill: str = Field(default="", max_length=40)
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def operation_inputs(self):
        if (self.operation == "skill_check" and (self.skill not in SKILLS or SKILLS[self.skill] != self.ability)
                or self.operation != "skill_check" and self.skill):
            raise ValueError("Unregistered proposal skill/ability pair")
        return self


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    revision: int
    edition: str
    definition_sha256: str
    source_sha256: str
    dependencies: tuple[str, ...] = ("checks.ability",)


@lru_cache(maxsize=3)
def _parse_definition(raw, name, expected_sha):
    # The pins were taken from LF copies; a checkout on Windows can bring the
    # same file back with CRLF, which changes the bytes and not the rules.
    # Normalising before hashing keeps the pin meaningful across checkouts
    # instead of failing 118 tests over a line-ending conversion.
    if hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() != expected_sha:
        raise ValueError("RuleDefinition fingerprint mismatch")
    document = json.loads(raw)
    if (document["schema_version"] != "1.0.0" or document["id"] != "dnd2024.d20." + name
            or document["edition"] != "dnd2024" or document["revision"] != 1
            or document["verification"]["status"] != "source_checked"
            or document["engine"]["handler"] != "RulesetEngine.d20"
            or document["engine"]["compatibility"] != "implemented_subset"
            or document["resources"]):
        raise ValueError("RuleDefinition not in the verified release subset")
    return RuleDefinition(document["id"], document["revision"], document["edition"],
                          expected_sha, document["sources"][0]["sha256"])


def verified_pack():
    try:
        engine_sha = hashlib.sha256((ROOT / "solo/rulesets.py").read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("Engine dependency is unavailable") from error
    if engine_sha != ENGINE_SHA256:
        raise ValueError("Engine dependency changed; revalidation is required")
    definitions = []
    for name, expected_sha in PINS:
        path = (DEFINITION_ROOT / (name + ".json")).resolve()
        if not path.is_relative_to(DEFINITION_ROOT.resolve()):
            raise ValueError("RuleDefinition outside the pinned directory")
        try:
            with path.open("rb") as stream:
                raw = stream.read(65537)
        except OSError as error:
            raise ValueError("Pinned RuleDefinition is unavailable") from error
        if len(raw) > 65536:
            raise ValueError("RuleDefinition exceeds release bounds")
        definition = _parse_definition(raw, name, expected_sha)
        if any(status(dependency, definition.edition) != "implemented" for dependency in definition.dependencies):
            raise ValueError("RuleDefinition dependency is unavailable")
        definitions.append(definition)
    try:
        with CHAPTER_PATH.open("rb") as stream:
            raw = stream.read(262145)
    except OSError as error:
        raise ValueError("PHB release definition is unavailable") from error
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != CHAPTER_SHA256:
        raise ValueError("PHB release definition changed; revalidation is required")
    chapter = json.loads(raw)
    candidates = {row["id"]: row for row in chapter["rules"]}
    for name, handler in CHAPTER_BINDINGS.items():
        row = candidates["dnd2024.phb01." + name]
        if (chapter["schema_version"] != "2.1.0" or row["revision"] != 1
                or row["rules_family"] != "dnd2024" or row["classification"]["mode"] != "deterministic"
                or row["verification"] != "source_checked_human_pending" or row["auto_activate"]
                or row["readiness"]["status"] != "adapter_verified"
                or row["readiness"]["handler"] != handler or row["readiness"]["blockers"]
                or row["source"]["sha256"] != definitions[0].source_sha256):
            raise ValueError("PHB definition is not in the verified release subset")
        definitions.append(RuleDefinition(row["id"], row["revision"], row["rules_family"],
                                          CHAPTER_SHA256, row["source"]["sha256"], tuple(row["dependencies"])))
    release_ids = {row.id for row in definitions}
    if any(dep not in release_ids and status(dep, "dnd2024") != "implemented"
           for row in definitions for dep in row.dependencies):
        raise ValueError("PHB release dependency is unavailable")
    fingerprint = digest({"bridge_revision": 2, "engine_sha256": ENGINE_SHA256,
                          "definitions": [asdict(row) for row in definitions], "operations": OPERATIONS})
    return tuple(definitions), fingerprint


def _eligible(current):
    state = current["state"]
    require_full_scene(state)
    preset = active(state)
    if preset.id != "dnd2024" or preset.edition != "2024":
        raise ValueError("Hybrid bridge edition mismatch")
    if state.get("combat", {}).get("active") or state.get("hero_death"):
        raise ValueError("Hybrid ability checks do not support this scene context")
    hero = Hero.model_validate(state["hero"])
    if hero.hp == 0 or set(hero.conditions) - {"poisoned"}:
        raise ValueError("Unregistered condition-dependent test; a dedicated handler is required")
    return preset


class HybridRulesAdapter:
    def __init__(self, store, session_valid):
        self.store = store
        self._session_valid = session_valid
        self._grants = {}

    def _access(self, token, campaign=None, pack_digest=None):
        if not token or not self._session_valid(token):
            raise PermissionError("Advanced session required")
        if campaign is not None:
            grant = self._grants.get((token, campaign))
            if grant is None or (pack_digest is not None and grant[0] != pack_digest):
                raise PermissionError("Hybrid rules are not authorized for this session and campaign")
            return grant

    def catalog(self, token):
        self._access(token)
        definitions, fingerprint = verified_pack()
        return {"rule_pack_digest": fingerprint, "scope": "ability_check", "operations": list(OPERATIONS), "default_active": False,
                "rules": [{"id": row.id, "revision": row.revision, "edition": row.edition,
                           "source_sha256": row.source_sha256} for row in definitions]}

    def activate(self, token, campaign, command):
        self._access(token)
        if type(command) is not Activation or command.confirm is not True:
            raise ValueError("Explicit activation confirmation required")
        command = Activation.model_validate(command.model_dump(), strict=True)
        _, fingerprint = verified_pack()
        if command.rule_pack_digest != fingerprint:
            raise ValueError("Rule pack changed")
        with self.store.connection() as connection:
            current = self.store.campaign(campaign, connection)
            preset = _eligible(current)
            if current["version"] != command.expected_version:
                raise ValueError("Stale activation state")
            self._access(token)
            self._grants = {key: value for key, value in self._grants.items() if self._session_valid(key[0])}
            if len(self._grants) >= 128 and (token, campaign) not in self._grants:
                raise ValueError("Too many active campaign grants")
            self._grants[(token, campaign)] = (fingerprint, digest(preset.model_dump()))
        return {"enabled": True, "campaign": campaign, "rule_pack_digest": fingerprint}

    def deactivate(self, token, campaign):
        self._access(token)
        self._grants.pop((token, campaign), None)
        return {"enabled": False}

    def _bound(self, token, campaign, current, fingerprint):
        grant = self._access(token, campaign, fingerprint)
        preset = _eligible(current)
        if grant[1] != digest(preset.model_dump()):
            raise ValueError("Campaign ruleset changed; reactivate the verified subset")
        return preset

    def context(self, token, campaign):
        self._access(token, campaign)
        _, fingerprint = verified_pack()
        with self.store.connection() as connection:
            current = self.store.campaign(campaign, connection)
            preset = self._bound(token, campaign, current, fingerprint)
            hero = Hero.model_validate(current["state"]["hero"])
            return {"state_version": current["version"], "rules_family": "dnd2024",
                    "rule_pack_digest": fingerprint, "ruleset_digest": digest(preset.model_dump()),
                    "actor": {"name": hero.name, "level": hero.level, "abilities": dict(hero.abilities)}}

    def propose(self, token, campaign, proposal):
        if type(proposal) is not CheckProposal:
            raise ValueError("Structured proposal required")
        proposal = CheckProposal.model_validate(proposal.model_dump(), strict=True)
        context = self.context(token, campaign)
        if (proposal.expected_version != context["state_version"]
                or proposal.rule_pack_digest != context["rule_pack_digest"]
                or proposal.ruleset_digest != context["ruleset_digest"]):
            raise ValueError("Stale proposal context")
        return {"status": "proposal", "commit_allowed": False,
                "proposal_digest": digest(proposal.model_dump()), "state_version": context["state_version"]}

    def execute(self, token, campaign, command):
        self._access(token, campaign)
        if type(command) is not CheckCommand or command.confirm is not True:
            raise ValueError("A proposal cannot execute; a confirmed Engine command is required")
        command = CheckCommand.model_validate(command.model_dump(), strict=True)
        definitions, fingerprint = verified_pack()
        if command.rule_pack_digest != fingerprint:
            raise ValueError("Rule pack changed")
        step = Step(kind="check", text="Verified hybrid " + command.operation, ability=command.ability,
                    skill=command.skill,
                    dc=command.dc, proficient=command.proficient,
                    advantage=command.advantage_sources > 0, disadvantage=command.disadvantage_sources > 0)
        signature = digest({"campaign": campaign, "principal": digest(token), "command": command.model_dump()})
        with self.store.connection(True) as connection:
            current = self.store.campaign(campaign, connection)
            preset = self._bound(token, campaign, current, fingerprint)
            if digest(preset.model_dump()) != command.ruleset_digest:
                raise ValueError("Ruleset fingerprint mismatch")

            def transition(state):
                self._bound(token, campaign, current, fingerprint)
                if current["version"] != command.expected_version:
                    raise ValueError("Stale command state")
                engine = RulesetEngine(preset)
                hero = Hero.model_validate(state["hero"])
                if command.operation == "saving_throw":
                    result = engine.saving_throw(hero, command.ability, command.dc,
                                                 advantage=step.advantage, disadvantage=step.disadvantage)
                else:
                    result = engine.check(hero, step)
                used = {"ability_modifier", "proficiency", command.operation}
                if command.operation == "saving_throw":
                    used.add("save_proficiency")
                rule_ids = [row.id for row in definitions if row.id.startswith("dnd2024.d20.")
                            or row.id.removeprefix("dnd2024.phb01.") in used]
                return [{"type": "scripted", "kind": "hybrid_ability_check", "check": result,
                         "operation": command.operation,
                         "rule_pack_digest": fingerprint, "rule_ids": rule_ids,
                         "ai_proposal_applied": False}]

            event = self.store.engine_event(connection, current, command.request_id, step.text, transition, signature)
            self._access(token, campaign, fingerprint)
            return event


def public_check_event(public_event):
    """Further narrow the existing Game.public_event projection for this API."""
    row = next(row for row in public_event["results"] if row.get("kind") == "hybrid_ability_check")
    fields = ("ability", "dice", "selected", "bonus", "total", "dc", "success", "mode", "ruleset")
    return {"id": public_event["id"], "action": public_event["action"], "undone": public_event["undone"],
            "operation": row.get("operation", "ability_check"),
            "result": {key: row["check"][key] for key in fields},
            **({"skill": row["check"]["skill"]} if "skill" in row["check"] else {}),
            "rule_ids": row["rule_ids"], "rule_pack_digest": row["rule_pack_digest"]}
