"""The pasted-sheet route a player actually reaches.

The route lives behind the experimental flag that the real launch turns on
(scripts/start_cloud.py:23), and nothing tested it before: an import that
answers 200 with an empty draft looks healthy from outside.
"""
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM

RUSSIAN_SHEET = (
    "Имя: Лира\n"
    "Класс: волшебник\n"
    "Уровень: 3\n"
    "Вид: высокий эльф\n"
    "Предыстория: мудрец\n"
    "Сила 8\nЛовкость 14\nТелосложение 13\n"
    "Интеллект 16\nМудрость 12\nХаризма 10\n"
)


def client(tmp_path):
    return TestClient(create_app(tmp_path / "solo.sqlite3", MockLLM(), experimental=True))


def test_the_route_a_player_reaches_reads_a_russian_sheet(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/prototype/wizard/import", json={"text": RUSSIAN_SHEET})
        assert response.status_code == 200, response.text
        read = response.json()
        assert read["ready"] is True, read
        assert read["missing"] == [] and read["ambiguous"] == []
        assert read["draft"]["class_slug"] == "wizard"
        assert read["draft"]["species_slug"] == "elf-high"
        assert read["draft"]["background_slug"] == "sage"
        assert read["draft"]["level"] == 3


def test_the_route_answers_404_when_the_launch_does_not_open_it(tmp_path):
    with TestClient(create_app(tmp_path / "solo.sqlite3", MockLLM())) as api:
        assert api.post("/api/prototype/wizard/import",
                        json={"text": RUSSIAN_SHEET}).status_code == 404
