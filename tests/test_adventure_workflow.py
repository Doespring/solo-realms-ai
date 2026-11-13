"""r210: два прежних сценария подготовки удалены вместе с legacy-путём.

Оба теста проверяли `POST /api/preparation/drafts/{id}/launch` и ходы
`POST /api/campaigns/{id}/play` на кампании без признака `nat20`. С r210 этот
маршрут отвечает 409 `nat20_scene_required`, а такая кампания не открывается
(`legacy_campaign_unsupported`), поэтому проверять в нём нечего:

* `test_selected_collection_module_survives_draft_resume_and_campaign_launch`
  — перенос выбранного модуля сборника в кампанию на запуске подготовки;
* `test_prepared_library_hero_text_clock_handout_undo_and_no_spoilers`
  — выдача раздатки по игровым часам, undo и отсутствие закрытого текста
  в публичном состоянии той же кампании.

Живой путь той же библиотеки — подключение проверенной книги к кампании
Nat20: `tests/test_attach_on_nat20_campaign.py` (attach, отложенный бросок,
журнал, материалы). Без покрытия после удаления остались выдача раздатки по
игровым часам и undo на подключённой книге — кандидат в карточку.

Что осталось проверяемым здесь — сам отказ: черновик подготовки больше не
становится кампанией ни при каком подтверждении.
"""
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
from solo.preparation import Draft


def test_preparation_launch_refuses_to_create_a_campaign_without_the_engine(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3')
    preparation = app.state.preparation
    draft = preparation.create()
    body = Draft.model_validate(draft['body'])
    body.name = 'Прежний мастер'
    body.source_kind = 'custom'
    body.prepared = True
    body.background_confirmed = True
    body.hero = Hero(name='Лира').model_dump()
    body.ruleset = 'dnd2024'
    body.opening = 'Перед героем лесная дорога.'
    draft = preparation.save(draft['id'], body, draft['revision'])
    with TestClient(app) as client:
        launched = client.post('/api/preparation/drafts/' + draft['id'] + '/launch',
                               json=dict(confirm=True))
        assert launched.status_code == 409, launched.text
        assert launched.json()['detail']['code'] == 'nat20_scene_required', launched.text
        # Ни одной кампании не родилось: отказ не оставляет сироту в базе.
        assert app.state.store.list_campaigns() == []
