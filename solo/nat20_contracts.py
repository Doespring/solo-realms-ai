"""Versioned mechanical extension of Solo's existing command/event contracts."""
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .game_commands import GameCommand as SoloGameCommand
from .engine_memory_contracts import GameEvent, GameSnapshot
from .contracts import Strict
from .nat20_campaign import CampaignDefinition, RewardReceipt
from .nat20_settings import CampaignRules
from .nat20_prototype import PrototypeDefinition


class GameCommand(SoloGameCommand):
    contract_version: Literal["nat20-command-v1"] = "nat20-command-v1"
    rules_version: Literal["dnd2024"] = "dnd2024"


class DurableState(Strict):
    """JSON-only replay recipe plus full checked read-model; no pickle/bytecode.

    ``party`` (r214) is the campaign's membership: the main hero first, then
    every companion, each with its own build, derived sheet, hit die pool and
    current resources. The hero entry is *not* a second source of truth: it is
    rebuilt from ``hero``/``sheet``/``hit_dice``/``exhaustion`` before every
    validation, so the many existing write sites that move the hero keep
    working and the party can never disagree with them. Companions are their
    own records; nothing else in the state answers for them.
    """
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
    party: list[dict] = Field(default_factory=list, max_length=8)
    #: r217: the three rules the player owns (death, difficulty, rewards).
    #: A campaign saved before r217 has no such key and opens on the printed
    #: defaults instead of being refused.
    settings: CampaignRules = Field(default_factory=CampaignRules)
    #: r217: the hero is out of a fight the rules would not let kill him
    #: (death disabled, or an irreversible outcome that was stopped). The
    #: campaign continues; a long rest brings him back, never an implicit heal.
    out: dict | None = None

    @model_validator(mode='before')
    @classmethod
    def hero_leads_the_party(cls, data):
        """Rebuild the hero entry of ``party`` from the fields that own it.

        Called before every validation, so the entry cannot drift while the
        hero is levelled, healed, rested or spent: whatever ``hero``,
        ``sheet``, ``hit_dice`` and ``exhaustion`` say at that moment is what
        the party reports. Companions are copied as they stand.
        """
        if not isinstance(data, dict) or not isinstance(data.get('hero'), dict):
            return data
        definition = data.get('definition')
        build = definition.get('build') if isinstance(definition, dict) else getattr(definition, 'build', None)
        if build is not None and hasattr(build, 'model_dump'):
            build = build.model_dump(mode='json')
        hero = dict(data['hero'])
        record = dict(id='hero', role='hero', name=hero.get('name') or 'Герой', build=build,
                      sheet=data.get('sheet') or {}, member=hero,
                      hit_dice=data.get('hit_dice') or {}, exhaustion=int(data.get('exhaustion') or 0))
        companions = [dict(item) for item in (data.get('party') or [])
                      if isinstance(item, dict) and item.get('id') not in (None, '', 'hero')]
        result = dict(data)
        result['party'] = [record, *companions]
        return result

    @model_validator(mode='after')
    def party_is_a_membership(self):
        ids = [item.get('id') for item in self.party]
        if any(not isinstance(identifier, str) or not identifier for identifier in ids):
            raise ValueError('Party member without an id')
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate party member')
        if self.party[0].get('role') != 'hero':
            raise ValueError('The main hero leads the party')
        for item in self.party[1:]:
            if item.get('role') != 'companion' or not isinstance(item.get('build'), dict):
                raise ValueError('A companion carries its own build')
        return self

    @model_validator(mode='after')
    def reward_ledger(self):
        if self.experience != sum(r.experience for r in self.rewards):
            raise ValueError('Experience differs from the confirmed reward ledger')
        if len({r.id for r in self.rewards}) != len(self.rewards):
            raise ValueError('Duplicate reward receipt')
        for receipt in self.rewards:
            if receipt.kind == 'printed':
                # A printed scenario reward is priced by the book, not by an
                # encounter, so there is no registered encounter to compare it
                # with. Its page, its positive amount and its unique id are the
                # proof it carries; the sum check above still binds it to the
                # one experience counter.
                if receipt.encounter_id:
                    raise ValueError('A printed reward belongs to no encounter')
                continue
            encounter = self.definition.encounters.get(receipt.encounter_id) if self.definition else None
            if encounter is None or receipt.id != encounter.reward.id or receipt.source != encounter.reward.source:
                raise ValueError('Reward is not registered in this campaign')
        return self


__all__ = ["GameCommand", "GameEvent", "GameSnapshot", "DurableState", "CampaignRules"]
