"""r253: место, названное в прозе страницы, получает стартовую сцену.

В r250 проверенный абзац публиковался только тогда, когда место начинало его
заголовком отдельной строкой. Русские издания печатают такой заголовок лишь у
части мест (по книге r237 — у 86 из 127), поэтому мир «Восточная Гавань»
остался с шаблоном. Проверяется:

1. место названо только в прозе абзаца → стартовая сцена есть, шаблона нет;
2. публичная фраза после двоеточия мастера и в конце страницы тоже находится;
3. границы те же: пустая строка, чужое место (в т.ч. нумерованный слух о соседнем
   городе и номерная комната), колонтитул и половина мастера не попадают в сцену;
4. страница, которая место не называет, остаётся `unresolved` с причиной
   `no-heading` и шаблоном как последним исходом;
5. страница, которая называет место только мастеру, даёт `only-gm`, пустая
   страница — `page-empty`, неоткрытая — `page-unreadable`: причина видна без
   чтения книги.

Модель синтетическая: ни одного платного вызова.
"""
import hashlib

from solo.adventure_contracts import AdventureDefinition

TEMPLATE='Вы находитесь в месте «Восточная Гавань». Что делает ваш герой?'
SCENE='Над крышами Восточной Гавани висит морозная дымка, и снег скрипит под ногами зевак.'
PUBLIC='Перед ратушей собралась толпа, чтобы поглазеть на публичную казнь Дазона, человека волшебника.'
GM_NOTE='Капитан Арлагат подходит к персонажам и говорит, что привело их в Восточную Гавань.'


def prose_page():
    """Страница r237 (стр. 42) по вёрстке: колонтитул, речь мастера, проза."""
    return '\n'.join([
        'Глава 1 | Десять Городов 41',
        'Публичная казнь',
        'События начинают разворачиваться, когда персонажи оказываются поблизости от места казни Дазона:',
        '',
        PUBLIC,
        SCENE,
        '',
        GM_NOTE,
    ])+'\n'


def world(page=42,start='Восточная Гавань',others=('Ратуша',)):
    places=[dict(name=start,description=start+'.',facts=[],sealed_text='',objects=[],npc=[],
        interactions=[],known_to_hero=True)]
    places.extend(dict(name=name,description='',facts=[],sealed_text='',objects=[],npc=[],
        interactions=[],known_to_hero=False) for name in others)
    return dict(start=start,ready=True,title=start+' — начало',definition=dict(
        id='r253-prose',title=start+' — начало',ruleset='dnd2024',ruleset_version=1,reviewed=True,
        entry_stage=dict(policy='verified-entry-v1',document='c'*64,page=page,
            opening_sha256=hashlib.sha256((start+'.').encode()).hexdigest(),
            boundary='Переход дальше закрыт.'),
        world=dict(name=start,source='fixture-book',locations=places,nodes=[],routes=[],events=[])))


def start_place(result):
    start=(result.get('start') or '').strip()
    for place in result['definition']['world']['locations']:
        if place.get('name')==start:return place
    raise AssertionError('стартового места нет в мире: '+start)


def test_place_named_only_in_the_pages_prose_gets_a_scene():
    """r253: заголовка на странице нет — абзац всё равно находится по прозе."""
    from solo.first_playable import publish_entry_scene
    fixed=publish_entry_scene(world(),page_text=prose_page())
    place=start_place(fixed)
    source=fixed['opening_source']
    assert source['text']!='unresolved',source
    assert source['text']=='printed-observable',source
    assert 'reason' not in source,source
    assert source['sentences']>=1,source
    assert place['description']!=TEMPLATE,place['description']
    assert place['description'].rstrip('.')!='Восточная Гавань',place['description']
    # Сцена начинается с фразы, где место названо; до неё страница говорит о
    # другом, а после неё начинается половина мастера.
    assert place['description'].startswith('Восточная Гавань. '+SCENE),place['description']
    # Половина мастера со страницы не публикуется, даже когда место названо в ней.
    assert 'Арлагат' not in place['description'],place['description']
    assert 'персонаж' not in place['description'].casefold(),place['description']
    assert place['facts']==[SCENE],place['facts']
    # Опубликованный текст привязан к странице и её отпечатку.
    assert source['page_sha256']==hashlib.sha256(prose_page().encode()).hexdigest(),source
    assert fixed['definition']['entry_stage']['opening_sha256']==hashlib.sha256(
        place['description'].encode()).hexdigest()
    AdventureDefinition.model_validate(fixed['definition'])


def test_prose_search_stops_at_another_place_and_keeps_the_heading_path():
    """Границы те же: чужое место обрывает абзац, а заголовок по-прежнему первый."""
    from solo.prepared_world import page_paragraph
    prose=page_paragraph(prose_page(),'Восточная Гавань',['Ратуша'])
    assert prose and prose[0].startswith('Над крышами Восточной Гавани'),prose
    assert 'Арлагат' not in ' '.join(prose),prose
    # Заголовок отдельной строкой работает как раньше и важнее прозы.
    headed='\n'.join(['Восточная Гавань',PUBLIC,SCENE,'','Ратуша',PUBLIC])+'\n'
    assert page_paragraph(headed,'Восточная Гавань',['Ратуша'])==[PUBLIC,SCENE]


def test_place_named_at_the_end_of_a_page_with_master_text_before_it():
    """r237 стр.42: проза места стоит последней, выше — указания ведущему."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join([
        'Глава 1 | Десять Городов 41',
        'Публичная казнь',
        'События начинают разворачиваться, когда персонажи оказываются поблизости от места казни Дазона:',
        'Перед ратушей собралась толпа, чтобы поглазеть на публичную казнь Дазона, человека волшебника.',
        'Пока зеваки толпятся у горящего волшебника, к персонажам приближается женщина в меховой шапке и ладном плаще.',
        'Если персонажи спросят о Дазоне, она скажет им, что он хладнокровно убил нескольких путешественников.',
        'Жители Восточной Гавани греются у горящего на костре злого волшебника',
    ])+'\n'
    fixed=publish_entry_scene(world(),page_text=page)
    place=start_place(fixed)
    source=fixed['opening_source']
    assert source['text']=='printed-observable',source
    assert place['description']!=TEMPLATE,place['description']
    # Сцена — публичная фраза о месте, а не указание ведущему над ней.
    assert 'Жители Восточной Гавани греются' in place['description'],place['description']
    assert 'персонаж' not in place['description'].casefold(),place['description']
    assert 'Публичная казнь' not in place['description'],place['description']
    assert place['facts']==['Жители Восточной Гавани греются у горящего на костре злого волшебника.'],place['facts']


def test_a_page_that_does_not_name_the_place_reports_no_heading():
    """Страница без места: шаблон остаётся, причина названа."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['Колонтитул','В город ведёт дорога.','Ещё абзац.'])+'\n'
    legacy=world()
    fixed=publish_entry_scene(legacy,page_text=page)
    assert start_place(fixed)['description']=='Восточная Гавань.',start_place(fixed)
    source=fixed['opening_source']
    assert source['text']=='unresolved',source
    assert source['reason']=='no-heading',source
    assert source['sentences']==0,source
    assert 'opening_source' not in legacy


def test_a_page_that_names_the_place_only_to_the_master_reports_only_gm():
    """Место названо только в указании ведущему — сцены нет, причина видна."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['Глава 1 | Десять Городов 41','Получение задания',GM_NOTE])+'\n'
    fixed=publish_entry_scene(world(),page_text=page)
    assert start_place(fixed)['description']=='Восточная Гавань.',start_place(fixed)
    source=fixed['opening_source']
    assert source['text']=='unresolved',source
    assert source['reason']=='only-gm',source


def test_page_furniture_is_not_published_from_an_unbroken_page():
    """Страница без пустых строк: колонтитул не прилипает к сцене (вёрстка r237)."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['42 Глава 1 | Десять Городов','Начало поисков',
        'Большая часть озера покрыта льдом.','Ветер идёт с севера.',
        'Это и произошло с четырьмя рыбаками из Восточной Гавани, которые ушли на лёд.'])+'\n'
    fixed=publish_entry_scene(world(),page_text=page)
    place=start_place(fixed)
    assert fixed['opening_source']['text']=='printed-observable',fixed['opening_source']
    # Сцена начинается с фразы, где место названо, а не с вёрстки страницы.
    assert place['description'].startswith('Восточная Гавань. Это и произошло'),place['description']
    assert 'Глава' not in place['description'] and 'Начало поисков' not in place['description'],place['description']
    assert place['facts'] and 'Восточной Гавани' in place['facts'][0],place['facts']


def test_a_numbered_neighbour_room_is_not_published():
    """Номерная комната в начале страницы — чужая сцена, её текст не берём."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['Глава 1 | Десять Городов 63','C12. Спальня Кадрота',
        'Кадрот спит здесь, а заодно славой крепости пустует соседний зал.'])+'\n'
    fixed=publish_entry_scene(world(start='Крепость'),page_text=page)
    place=start_place(fixed)
    assert fixed['opening_source']['text']=='unresolved',fixed['opening_source']
    assert fixed['opening_source']['reason']=='only-gm',fixed['opening_source']
    assert place['description']=='Крепость.',place['description']


def test_a_numbered_item_about_another_place_ends_the_paragraph():
    """Слух о соседнем городе — не продолжение сцены этого места (r237, стр.19)."""
    from solo.prepared_world import page_paragraph
    page='\n'.join(['Глава 1 | Десять Городов 18','Слухи в Десяти Городах',
        '3 В Восточной Гавани был схвачен злой волшебник, убивший нескольких искателей приключений.',
        '4 Остерегайтесь страшного белого лося в Глухолесье!',
        '5 В Каэр-Диневале давненько никто не видел городского спикера.'])+'\n'
    paragraph=page_paragraph(page,'Восточная Гавань',['Глухолесье','Каэр-Диневал'])
    joined=' '.join(paragraph)
    assert 'Восточной Гавани' in joined,paragraph
    assert 'Глухолесье' not in joined and 'Каэр-Диневале' not in joined,paragraph
    assert 'В Восточной Гавани был схвачен' in paragraph[0],paragraph


def test_a_public_sentence_after_the_masters_colon_is_published():
    """Двоеточие мастера не склеивает публичную фразу с указанием ведущему."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['Глава 1 | Десять Городов 41',
        'События начинают разворачиваться, когда персонажи оказываются поблизости от места казни Дазона:',
        'Перед ратушей в Восточной Гавани собралась толпа.'])+'\n'
    place=start_place(publish_entry_scene(world(),page_text=page))
    assert 'собралась толпа' in place['description'],place['description']
    assert 'персонаж' not in place['description'].casefold(),place['description']


def test_a_neighbour_room_does_not_lend_its_text():
    """Текст соседней номерной комнаты не становится описанием места."""
    from solo.prepared_world import page_paragraph
    page='\n'.join(['Глава 1 | Десять Городов 63','C12. Спальня Кадрота',
        'Кадрот спит здесь, а кухня за стеной пустует.'])+'\n'
    assert page_paragraph(page,'Кухня')==[],page_paragraph(page,'Кухня')


def test_an_empty_page_reports_why_the_page_is_missing():
    """Страницы нет вовсе и страница не открылась — разные причины отказа."""
    from solo.first_playable import publish_entry_scene
    unreadable=publish_entry_scene(world(),page_text='')
    assert unreadable['opening_source']['text']=='unresolved',unreadable['opening_source']
    assert unreadable['opening_source']['reason']=='page-unreadable',unreadable['opening_source']
    # Мир без закреплённой страницы: читать нечего, а не «не открылось».
    bare=world()
    bare['definition']=dict(bare['definition'])
    del bare['definition']['entry_stage']
    empty=publish_entry_scene(bare,page_text='')
    assert empty['opening_source']['reason']=='page-empty',empty['opening_source']
