"""r242: экран «Мои миры» подключён к странице, а не просто лежит файлом.

Живая проверка 06.11.2026 началась с того, что скрипт `web/worlds-panel.js`
существовал, а `web/index.html` его не грузил: у игрока экрана не было вовсе.
Такая поломка не видна ни одному тесту API, потому что сервер здоров. Здесь
проверяется именно стык: файл есть, страница его грузит, экран и кнопка меню
собираются в самом скрипте.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worlds_screen_is_loaded_by_the_page():
    page = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    assert page.count('/static/worlds-panel.js') == 1, 'скрипт экрана «Мои миры» не подключён ровно один раз'
    assert '/static/worlds.css' in page, 'стили экрана «Мои миры» не подключены'
    assert (ROOT / 'web/worlds-panel.js').is_file() and (ROOT / 'web/worlds.css').is_file()


def test_worlds_screen_mounts_itself_and_has_a_menu_button():
    script = (ROOT / 'web/worlds-panel.js').read_text(encoding='utf-8')
    # Экран ищется по id в переключении видов: view() включает .view с этим id.
    assert "screen.id='worlds'" in script
    assert "nav.dataset.view='worlds'" in script
    # Мир открытой кампании и библиотека — два разных блока, оба на этом экране.
    assert "id='world-save-file'" in script and "id='world-save-library'" in script
    assert "id='worlds-list'" in script and "id='worlds-upload'" in script
