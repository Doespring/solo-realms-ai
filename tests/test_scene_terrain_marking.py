"""r206 — разметка местности штатной сцены доходит до игрока, а не только до тестов.

До правки в `encounters.basic.grid` не было ни `cover_cells`, ни `blocked_cells`:
правила укрытия считались движком (r187), но авторская сцена их не задавала, и
игрок не мог ни встать за укрытие, ни упереться в стену.

Проверяется тремя существующими читателями разметки и проекцией экрана:
* `solo/nat20_screen.py::hide_rule_met` — гейт Скрытности читает `cover_cells`
  собственной клетки героя;
* `solo/nat20_content.py::_total_cover` — полное укрытие отбивает атаку;
* `solo/nat20_prototype.py::PrototypeEncounter.positions` — стартовая клетка в
  `blocked_cells` отвергается;
* `Nat20Screen.project` — строка местности и подпись клетки в предложении шага.
"""
import pytest

from solo.nat20_campaign import load_definition

SCENE = 'fighter-standard-array-2024-v1'
DEGREES = {'half', 'three_quarters', 'total'}


@pytest.fixture(scope='module')
def encounter():
    return load_definition(SCENE).encounters['basic']


def cells(width, height):
    return {f'{x},{y}' for x in range(width) for y in range(height)}


def test_shipped_scene_marks_cover_and_obstacles(encounter):
    """Данные: 3–6 клеток укрытия обоих степеней и 2–4 непроходимые клетки."""
    grid = encounter.grid
    cover, blocked = dict(grid.cover_cells), list(grid.blocked_cells)
    assert 3 <= len(cover) <= 6, cover
    assert 2 <= len(blocked) <= 4, blocked
    assert set(cover.values()) <= DEGREES, cover
    assert {'half', 'three_quarters'} <= set(cover.values()), cover
    inside = cells(grid.width, grid.height)
    assert set(cover) <= inside and set(blocked) <= inside
    assert not set(cover) & set(blocked), 'клетка не может быть и укрытием, и стеной'
    starts = {f'{encounter.hero_position.x},{encounter.hero_position.y}'}
    starts |= {f'{actor.position.x},{actor.position.y}' for actor in encounter.actors}
    assert not starts & set(blocked), 'стартовая клетка не может быть непроходимой'
    # Укрытие ставится там, где герой может встать сам: не на стартовой клетке
    # врага и не в стене.
    assert not starts & set(cover), 'укрытие не ставится на стартовую клетку'


def test_hide_gate_reads_cover_on_the_heroes_cell(encounter):
    """Скрытность: `hide_rule_met` читает `cover_cells` там же, где движок."""
    from solo.nat20_screen import hide_rule_met
    grid = encounter.grid.model_dump(mode='json')
    covered = next(cell for cell, degree in grid['cover_cells'].items()
                   if degree == 'three_quarters')

    def runtime(cell):
        return {'combat': {'initial': {'grid': grid},
                           'view': {'actor_zone': {'hero': cell}, 'initiative': []}}}

    assert hide_rule_met(runtime(covered)) is True
    assert hide_rule_met(runtime(next(iter(cells(encounter.grid.width,
                                                  encounter.grid.height)
                                         - set(grid['cover_cells'])
                                         - set(grid['blocked_cells']))))) is False


def test_total_cover_reader_sees_a_total_tag(encounter):
    """Полное укрытие отбивает атаку — читатель тот же, что и в бою."""
    from solo.nat20_content import _total_cover
    grid = encounter.grid.model_dump(mode='json')
    assert _total_cover(grid, next(iter(grid['cover_cells']))) is False
    grid['cover_cells']['0,0'] = 'total'
    assert _total_cover(grid, '0,0') is True


def test_starting_cell_in_a_wall_is_refused(encounter):
    """Непроходимая клетка читается моделью сцены: старт в стене отвергается."""
    from solo.nat20_prototype import PrototypeEncounter
    raw = encounter.model_dump(mode='json')
    PrototypeEncounter.model_validate(raw)  # сама сцена валидна
    wall = raw['grid']['blocked_cells'][0]
    x, y = (int(part) for part in wall.split(','))
    raw['hero_position'] = dict(x=x, y=y, z=0)
    with pytest.raises(ValueError, match='blocked'):
        PrototypeEncounter.model_validate(raw)


def test_screen_names_the_terrain_and_the_cell(tmp_path):
    """Экран: строка местности и подпись клетки, куда можно шагнуть."""
    from solo.game_commands import GameCommand
    from solo.nat20_api import Nat20GameEngine
    from solo.nat20_screen import Nat20Screen
    from solo.storage import Store

    store = Store(tmp_path / 'r206-screen.sqlite3')
    engine = Nat20GameEngine(store)
    cid = engine.create_campaign('r206', definition_id=SCENE, seed=1)['id']

    def send(rid, action):
        return engine.execute(cid, GameCommand(request_id=rid,
                                              expected_version=store.campaign(cid)['version'],
                                              action=action))

    send('start', dict(kind='start_combat', encounter_id='basic'))
    for attempt in range(8):
        view = store.campaign(cid)['state']['nat20']['combat']['view']
        if view['initiative'][view.get('current_turn_index') or 0]['entity_id'] == 'hero':
            break
        send('turn-%d' % attempt, dict(kind='end_turn'))
    packet = Nat20Screen().project(store.campaign(cid))
    terrain = packet['combat']['terrain']
    assert terrain and 'укрытие' in terrain and 'непроходимо' in terrain, terrain
    moves = [command['label'] for command in packet['commands']
             if command['label'].startswith('Перейти')]
    assert any('укрытие:' in label for label in moves), moves
    # Клиент рисует `snapshot.combat.terrain` (web/game-screen.js), поэтому тот же
    # факт обязан прийти через настоящую проекцию экрана, а не только из класса.
    from solo.play_screen import screen
    served = screen(store, cid)['snapshot']['combat']
    assert served['terrain'] == terrain, served.get('terrain')
