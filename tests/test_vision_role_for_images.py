"""The model that reads a picture is its own role, not the text analyst.

Switching book preparation to a cheap text model used to disable vision
entirely: the image gate demanded `mode=='pdf_analyst'` and an image tariff on
the very model that had just become DeepSeek. These tests pin the separation:
text goes to `pdf_analyst`, pictures go to `vision`, and a profile without a
configured vision role refuses in the same words instead of quietly sending an
image to a model that cannot see it.
"""
import asyncio, json
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from solo.ai_roles import DEEPSEEK, GEMINI, IMAGE_REQUIRED, routerai_preset, select_role, vision_config
from solo.analysis_images import image_count, png_part
from solo.cloud import CloudLLM
from solo.cloud_control import LABELS, ROLES, status
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudSettings
from test_cloud import TestVault, sse


def setup(tmp_path, respond, *, with_vision=True, with_image_tariff=True):
    settings = CloudSettings(tmp_path / 'cloud-profiles.json', TestVault())
    p = routerai_preset()
    for role in ('interpreter', 'narrator', 'pdf_analyst', 'vision'):
        cfg = getattr(p, role)
        if cfg is None:
            continue
        cfg.endpoint = 'explicit-route'
        cfg.input_per_million = cfg.output_per_million = Decimal('1')
        if role == 'vision' and with_image_tariff:
            cfg.image_per_million = Decimal('1')
    if not with_vision:
        p.vision = None
    p.enabled = True
    p.pdf_analysis_enabled = True
    for field in ('max_request_cost', 'session_budget', 'daily_budget', 'monthly_budget', 'preparation_budget'):
        setattr(p, field, Decimal(10))
    settings.save(type(p).model_validate(p.model_dump()))
    settings.vault.set(p.id, 'one-synthetic-key')
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return CloudLLM(settings, CostLedger(tmp_path / 'cloud-usage.sqlite3'), client=client), settings.profile()


def run_grammar(llm, grammar):
    async def go():
        async for _ in llm.stream('', [dict(type='text', text='страница'), picture()], mode='pdf_analyst', grammar=grammar):
            pass
    asyncio.run(go())


def image_only_source(tmp_path):
    """A one-page PNG bundle whose extracted text is empty."""
    from solo.adventure_library import AdventureLibrary
    from solo.adventure_worker import run as import_source

    library = AdventureLibrary(tmp_path / 'source-library')
    bundle = library.create('Synthetic image-only source')
    path = tmp_path / 'source.png'
    Image.new('RGB', (200, 160), 'white').save(path)
    job = library.enqueue(bundle['id'], path, 'source.png', 'maps')
    import_source(library.data, job['id'])
    document = library.review(bundle['id'])['documents'][0]['id']
    with library.db() as db:
        db.execute('UPDATE pages SET content=? WHERE document=?', ('', document))
    return bundle['id'], document


def picture():
    return png_part(Image.new('RGB', (320, 200), 'white'))


def run(llm, user, mode):
    # The mock transport lives on this client, and closing it makes the next
    # call build a real one — so a test closes the client once, at the end.
    async def go():
        return ''.join([c async for c in llm.stream('', user, mode=mode, max_tokens=64)])
    return asyncio.run(go())


def close(llm):
    asyncio.run(llm.close())


def test_an_image_goes_to_the_vision_role_and_text_to_the_analyst(tmp_path):
    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': '{}'}}]}]))
    run(llm, 'Проверка', 'pdf_analyst')
    assert sent[-1]['model'] == p.pdf_analyst.model
    assert not isinstance(sent[-1]['messages'][1]['content'], list)

    run(llm, [dict(type='text', text='страница'), picture()], 'pdf_analyst')
    assert sent[-1]['model'] == p.vision.model == GEMINI
    assert sent[-1]['messages'][1]['content'][1]['type'] == 'image_url'


@pytest.mark.parametrize('mode', ['pdf_analyst', 'preparation_world'])
def test_a_cheaper_text_analyst_does_not_take_vision_away(tmp_path, mode):
    # The exact regression: preparation was moved to DeepSeek, whose image
    # tariff is empty, and every picture stopped working with it.
    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': '{}'}}]}]))
    cheaper = p.model_copy(deep=True)
    cheaper.pdf_analyst.model = DEEPSEEK
    cheaper.pdf_analyst.image_per_million = None
    cheaper.pdf_analyst.parameters = ['max_tokens', 'temperature', 'response_format']
    cheaper.pdf_analyst.structured = 'json_object'
    llm.settings.save(type(cheaper).model_validate(cheaper.model_dump()))

    run(llm, [dict(type='text', text='карта'), picture()], mode)
    assert sent[-1]['model'] == GEMINI
    assert llm.metrics[-1]['model'] == GEMINI
    assert llm.metrics[-1]['status'] == 'ok'


def test_a_profile_without_vision_refuses_an_image_in_the_old_words(tmp_path):
    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': '{}'}}]}]), with_vision=False)
    assert p.vision is None
    with pytest.raises(ValueError, match='тарифом'):
        run(llm, [dict(type='text', text='страница'), picture()], 'pdf_analyst')
    assert not sent
    # A text-only request still works: only pictures need the separate role.
    run(llm, 'Проверка', 'pdf_analyst')
    assert len(sent) == 1 and sent[0]['model'] == p.pdf_analyst.model


def test_vision_refuses_an_image_without_its_own_image_tariff(tmp_path):
    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': '{}'}}]}]), with_image_tariff=False)
    assert p.vision.image_per_million is None
    with pytest.raises(ValueError, match='тарифом'):
        run(llm, [dict(type='text', text='страница'), picture()], 'pdf_analyst')
    assert not sent


def test_the_settings_screen_sees_vision_with_its_own_model_and_tariff(tmp_path):
    llm, p = setup(tmp_path, lambda r: sse([{'choices': [{'delta': {'content': '{}'}}]}]))
    assert 'vision' in ROLES and LABELS['vision'] == 'Зрение: карты'
    details = status(llm.settings, p.id)
    assert details['roles']['vision']['label'] == 'Зрение: карты'
    assert details['roles']['vision']['model'] == GEMINI
    assert details['roles']['vision']['problems'] == []
    assert details['roles']['pdf_analyst']['model'] == p.pdf_analyst.model
    public = llm.settings.public()['profiles'][0]
    assert public['vision']['model'] == GEMINI
    assert public['vision']['image_per_million'] is not None
    assert select_role(p, 'vision')[0].model == GEMINI
    assert vision_config(p).image_per_million is not None


def test_the_preset_keeps_vision_separate_and_unpriced_until_confirmed():
    p = routerai_preset()
    assert p.vision is not None and p.vision.model == GEMINI
    assert p.vision is not p.pdf_analyst
    assert p.vision.image_per_million is None
    with pytest.raises(ValueError, match='тарифом'):
        vision_config(p)
    with pytest.raises(ValueError) as caught: vision_config(p)
    assert IMAGE_REQUIRED in str(caught.value)


def test_vision_without_an_image_tariff_is_a_visible_settings_problem(tmp_path):
    llm, p = setup(tmp_path, lambda r: sse([{'choices': [{'delta': {'content': '{}'}}]}]), with_image_tariff=False)
    problems = status(llm.settings, p.id)['roles']['vision']['problems']
    assert any('тариф изображений' in text for text in problems), problems
    close(llm)


def test_vision_keeps_the_json_contract_of_the_book_analysis(tmp_path):
    # The live profile carries the analysis-grade vision config (json_object,
    # 2048 tokens). The vision role must not flatten that contract: a grammar
    # still goes out as response_format, otherwise long page lists truncate.
    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': '{}'}}]}]))
    live = p.model_copy(deep=True)
    live.vision = live.pdf_analyst.model_copy(deep=True)
    live.vision.max_tokens = 2048
    live.vision.structured = 'json_object'
    live.vision.image_per_million = Decimal('78')
    llm.settings.save(type(live).model_validate(live.model_dump()))
    grammar = dict(type='object', properties=dict(status=dict(type='string')))
    run_grammar(llm, grammar)
    assert sent[-1]['model'] == GEMINI
    assert sent[-1]['response_format'] == {'type': 'json_object'}
    assert sent[-1]['max_tokens'] == 2048
    close(llm)


def test_a_profile_whose_only_reader_is_vision_can_review_pages(tmp_path):
    from solo.adventure_analysis import AdventureAnalysis, AnalysisRequest
    from solo.adventure_library import AdventureLibrary
    from solo.adventure_worker import run as import_source

    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': json.dumps(ANSWER)}}]}]))
    # No separate analyst model: the picture is read by the vision role, and the
    # owner's consent toggle — not a second model — governs the separate review.
    only_vision = p.model_copy(deep=True)
    only_vision.pdf_analyst = None
    only_vision.pdf_analysis_enabled = True
    llm.settings.save(type(only_vision).model_validate(only_vision.model_dump()))
    bundle, document = image_only_source(tmp_path)
    body = AnalysisRequest(document=document, pages=[1], role='gemini', include_images=True,
                           problem='Локальное извлечение текста пусто', request_id='vision', consent_to_send=True)
    answer = asyncio.run(AdventureAnalysis(AdventureLibrary(tmp_path / 'source-library'), llm).analyze(bundle, body))
    assert answer['status'] == 'requires_review'
    assert sent and sent[0]['model'] == GEMINI
    assert isinstance(sent[0]['messages'][1]['content'], list)
    close(llm)


def test_the_separate_page_review_still_needs_the_owner_consent(tmp_path):
    from solo.adventure_analysis import AdventureAnalysis, AnalysisRequest
    from solo.adventure_library import AdventureLibrary

    sent = []
    llm, p = setup(tmp_path, lambda r: sent.append(json.loads(r.content)) or sse([{'choices': [{'delta': {'content': json.dumps(ANSWER)}}]}]))
    no_consent = p.model_copy(deep=True)
    no_consent.pdf_analysis_enabled = False
    llm.settings.save(type(no_consent).model_validate(no_consent.model_dump()))
    bundle, document = image_only_source(tmp_path)
    body = AnalysisRequest(document=document, pages=[1], role='gemini', include_images=True,
                           problem='Локальное извлечение текста пусто', request_id='no-consent', consent_to_send=True)
    with pytest.raises(ValueError, match='согласие'):
        asyncio.run(AdventureAnalysis(AdventureLibrary(tmp_path / 'source-library'), llm).analyze(bundle, body))
    assert not sent
    close(llm)


ANSWER = dict(suggestions=[], problems=[], classifications=[dict(page=1, kind='unknown', source='image', needs_visual_review=True)])
