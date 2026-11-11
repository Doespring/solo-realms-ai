"""Today's closed GM tool contract. Mechanical parameters are engine-owned."""
from typing import Annotated, Literal
import os
import sys

from pydantic import ConfigDict, Field, model_serializer, model_validator
from .contracts import Strict


class ToolInput(Strict):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Check(ToolInput):
    # An unset field is absent, not null: a printed check must serialise exactly
    # as it did before the master's ruling had anywhere to travel.
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    kind: Literal['check']
    check_id: str = Field(min_length=1, max_length=120)
    # The master's ruling travels WITH the command, because the engine has no
    # other way to hear it. When the material prints a check these stay None and
    # the definition's own difficulty is used. When it prints none, the master
    # names the difficulty here and the journal records it as the master's, not
    # as a fact of the book. Never accepted from the model's own answer.
    dc: int | None = Field(default=None, ge=1, le=40)
    source: Literal['printed', 'master'] | None = None

    @model_validator(mode='after')
    def no_self_ruled_difficulty(self):
        # A difficulty may only enter through a master's ruling made inside the
        # game. A command that arrives already carrying one, without that mark,
        # is the model writing mechanics -- exactly what this contract forbids.
        if self.dc is not None and self.source != 'master':
            raise ValueError('Difficulty may only be set by the master ruling, not passed with the command')
        return self

    @model_serializer(mode='wrap')
    def omit_unset_ruling(self, handler):
        # A check the material prints carries no difficulty field at all, so its
        # shape is exactly what every reader of this contract already expects.
        data = handler(self)
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if not (k in ('dc', 'source') and v is None)}
        return data


class StartCombat(ToolInput):
    kind: Literal['start_combat']
    encounter_id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,79}$')


class GridPosition(ToolInput):
    x: int = Field(ge=0, le=79)
    y: int = Field(ge=0, le=79)
    z: int = Field(ge=-6, le=20)


class Move(ToolInput):
    kind: Literal['move']
    position: GridPosition


class Attack(ToolInput):
    kind: Literal['attack']
    target: str = Field(min_length=1, max_length=80)


class Dash(ToolInput):
    kind: Literal['dash']


class EndTurn(ToolInput):
    kind: Literal['end_turn']


class ShortRest(ToolInput):
    kind: Literal['short_rest']
    hit_dice: int = Field(ge=0, le=20)


class LongRest(ToolInput):
    kind: Literal['long_rest']
    food_water: bool


class EndCombat(ToolInput):
    kind: Literal['end_combat']


class UseFeature(ToolInput):
    kind: Literal['use_feature']
    feature_id: Literal['second-wind']


class CastSpell(ToolInput):
    kind: Literal['cast_spell']
    spell_id: Literal['bless']
    target: Literal['hero']


class DropConcentration(ToolInput):
    kind: Literal['drop_concentration']


class LevelUp(ToolInput):
    """Advance one class level; the engine decides whether one is owed.

    The player names nothing mechanical here: class, subclass and choices stay
    with the advancement rules the scenario fixed, so a chat phrase cannot
    invent a level the book does not grant.
    """
    kind: Literal['level_up']


class NativeAction(ToolInput):
    """Public native choices only; never accepts roll/stat/formula overrides."""
    kind: Literal['native_action']
    operation: Literal['attack', 'cast_spell', 'use_item', 'move', 'dash', 'dodge',
        'disengage', 'hide', 'help', 'ready', 'reaction', 'move_mark', 'use_feature',
        'pass', 'drop_concentration', 'grapple', 'shove', 'stand_up', 'escape_grapple']
    target_id: str | None = Field(default=None, min_length=1, max_length=120)
    target_ids: list[str] | None = Field(default=None, max_length=100)
    weapon_id: str | None = Field(default=None, min_length=1, max_length=120)
    spell_id: str | None = Field(default=None, min_length=1, max_length=120)
    item_id: str | None = Field(default=None, min_length=1, max_length=120)
    feature_id: str | None = Field(default=None, min_length=1, max_length=120)
    activity_id: str | None = Field(default=None, min_length=1, max_length=120)
    slot_level: int | None = Field(default=None, ge=0, le=9)
    charges_to_spend: int | None = Field(default=None, ge=1, le=1000)
    pool_points: int | None = Field(default=None, ge=1, le=1000)
    position: GridPosition | None = None
    direction: list[int] | None = Field(default=None, min_length=2, max_length=2)
    excluded_target_ids: list[str] | None = Field(default=None, max_length=100)
    use_bonus_action: bool | None = None
    shove_push: bool | None = None
    two_handed: bool | None = None
    as_ritual: bool | None = None
    form_id: str | None = Field(default=None, min_length=1, max_length=120)
    stat_block_action_id: str | None = Field(default=None, min_length=1, max_length=120)
    reaction_trigger: Literal['cast_spell', 'hit_by_attack', 'targeted_by_magic_missile'] | None = None
    redeem_granted_die: Literal['feature_grant:bardic-inspiration'] | None = None


TOOLS = (Check, StartCombat, Move, Attack, Dash, EndTurn, ShortRest, LongRest, EndCombat, UseFeature, CastSpell, DropConcentration, LevelUp, NativeAction)
Action = Annotated[Check | StartCombat | Move | Attack | Dash | EndTurn | ShortRest | LongRest | EndCombat | UseFeature | CastSpell | DropConcentration | LevelUp | NativeAction,
                   Field(discriminator='kind')]


class GameCommand(ToolInput):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    expected_version: int = Field(ge=0)
    action: Action


class CommandIntent(ToolInput):
    status: Literal['command', 'clarification', 'unsupported']
    action: Action | None
    message: str = Field(max_length=1000)

    @model_validator(mode='after')
    def complete(self):
        if self.status == 'command':
            if self.action is None:
                raise ValueError('Command requires one action and no message')
        elif self.action is not None or not self.message.strip():
            raise ValueError('A non-command requires a message and no action')
        return self

    @model_validator(mode='before')
    @classmethod
    def envelope(cls, value):
        # Одна команда и сопроводительный текст не противоречат друг другу:
        # действие известно, а пояснение лишь описывает его. Молча отбрасываем
        # пояснение только у настоящей команды с действием, чтобы модель не
        # роняла ход в 'clarification' из-за собственной словоохотливости.
        # Противоречивый конверт (команда без действия, не-команда с действием)
        # по-прежнему отвергается в complete().
        if isinstance(value, dict) and value.get('status') == 'command' and value.get('action') is not None and value.get('message'):
            value = {**value, 'message': ''}
        return value


def bless_stand_enabled():
    """Pending mechanics are source-test-only, including with inherited flags."""
    return not getattr(sys, 'frozen', False) and os.environ.get('SOLO_TEST') == '1' and os.environ.get('SOLO_NAT20_BLESS_STAND') == '1'


def tool_schemas():
    """Strict function schemas; also advertised to the existing Interpreter."""
    tools = []
    experimental = bless_stand_enabled()
    for model in TOOLS:
        if not experimental and model in (CastSpell, DropConcentration):
            continue
        schema = model.model_json_schema()
        kind = schema['properties'].pop('kind')['const']
        schema['required'].remove('kind')
        # Strict function calling requires nullable choices to be present too.
        schema['required'] = list(schema['properties'])
        tools.append(dict(type='function', function=dict(name=kind, strict=True, parameters=schema)))
    return tools
