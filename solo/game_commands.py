"""Today's closed GM tool contract. Mechanical parameters are engine-owned."""
from typing import Annotated, Literal
import os
import sys

from pydantic import ConfigDict, Field, model_validator
from .contracts import Strict


class ToolInput(Strict):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Check(ToolInput):
    kind: Literal['check']
    check_id: str = Field(min_length=1, max_length=120)


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


class Order(ToolInput):
    """A request or an order to one companion (r216) — the host answers it.

    The interpreter only *reads* the player's sentence: who it addresses, whether
    it is a request or an order, and which of the four categories it falls into.
    Whether the companion agrees is decided by the host from his own traits and
    relation (``solo/nat20_orders.py``), so a model can neither talk him into
    obedience nor hide the reason from the journal. ``text`` is the player's own
    wording, kept for the journal line.
    """
    kind: Literal['order']
    target_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=500)
    form: Literal['order', 'request']
    category: Literal['risk', 'profit', 'against_character', 'normal']


class NativeAction(ToolInput):
    """Public native choices only; never accepts roll/stat/formula overrides."""
    kind: Literal['native_action']
    operation: Literal['attack', 'cast_spell', 'use_item', 'move', 'dash', 'dodge',
        'disengage', 'hide', 'help', 'ready', 'reaction', 'move_mark', 'use_feature',
        'pass', 'drop_concentration', 'grapple', 'shove', 'stand_up', 'escape_grapple',
        'drop_prone']
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


class SceneAction(ToolInput):
    """PHB 2024 scene action outside combat: one ability check, engine-rolled.

    The host names the action and the skill; the DC, the die and the outcome stay
    with the engine. No roll, DC or stat override is accepted here, and this is
    deliberately not a Native operation: the pinned engine has no such
    ``intent_type``, so an operation named after the action would fail at
    execution instead of rolling a check.
    """
    kind: Literal['scene_action']
    action_id: Literal['influence', 'study', 'search']
    skill: Literal['persuasion', 'deception', 'intimidation', 'performance',
                   'animal_handling', 'insight', 'arcana', 'history', 'religion',
                   'nature', 'investigation', 'perception']
    # Влияние адресуется существу (книга, стр. 15: «изменить отношение
    # существа»), а Изучение и Поиск — месту. Пока цели не было, при двух
    # НИПах в сцене хост не мог сказать, на кого смотрит проверка, и
    # влияние на любого из них было бы догадкой. Пусто — единственный НИП
    # сцены; при нескольких хост называет имена вместо броска.
    npc_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode='after')
    def belongs_to_action(self):
        from .nat20_content import SCENE_ACTIONS
        if self.skill not in SCENE_ACTIONS[self.action_id]['skills']:
            raise ValueError('Skill is not part of this scene action')
        return self


TOOLS = (Check, StartCombat, Move, Attack, Dash, EndTurn, ShortRest, LongRest, EndCombat, UseFeature, CastSpell, DropConcentration, LevelUp, Order, NativeAction, SceneAction)
Action = Annotated[Check | StartCombat | Move | Attack | Dash | EndTurn | ShortRest | LongRest | EndCombat | UseFeature | CastSpell | DropConcentration | LevelUp | Order | NativeAction | SceneAction,
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
