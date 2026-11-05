"""Versioned mechanical extension of Solo's existing command/event contracts."""
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .game_commands import GameCommand as SoloGameCommand
from .engine_memory_contracts import GameEvent, GameSnapshot
from .contracts import Strict
from .nat20_campaign import CampaignDefinition, RewardReceipt
from .nat20_prototype import PrototypeDefinition


class GameCommand(SoloGameCommand):
    contract_version: Literal["nat20-command-v1"] = "nat20-command-v1"
    rules_version: Literal["dnd2024"] = "dnd2024"


class DurableState(Strict):
    """JSON-only replay recipe plus full checked read-model; no pickle/bytecode."""
    contract_version: Literal["nat20-state-v1"] = "nat20-state-v1"
    rules_version: Literal["dnd2024"] = "dnd2024"
    engine_version: Literal["0.7.0"] = "0.7.0"
    release_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed: int = Field(ge=0, le=2**63-1)
    hero: dict
    sheet: dict
    hit_dice: dict
    exhaustion: int = Field(ge=0, le=6)
    active_effects: list[dict]
    combat: dict | None = None
    definition: CampaignDefinition | PrototypeDefinition | None = None
    experience: int = Field(default=0, ge=0, le=10000000)
    rewards: list[RewardReceipt] = Field(default_factory=list, max_length=64)
    # How far the game calendar has been scored for charge recovery. Kept with
    # the durable state so a campaign that gains minutes through a path that
    # never consults the recovery module still catches up on the next command.
    recovery_minute: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def reward_ledger(self):
        if self.experience != sum(r.experience for r in self.rewards):
            raise ValueError('Experience differs from the confirmed reward ledger')
        if len({r.id for r in self.rewards}) != len(self.rewards):
            raise ValueError('Duplicate reward receipt')
        for receipt in self.rewards:
            encounter = self.definition.encounters.get(receipt.encounter_id) if self.definition else None
            if encounter is None or receipt.id != encounter.reward.id or receipt.source != encounter.reward.source:
                raise ValueError('Reward is not registered in this campaign')
        return self


__all__ = ["GameCommand", "GameEvent", "GameSnapshot", "DurableState"]
