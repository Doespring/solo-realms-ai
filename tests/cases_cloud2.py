"""Additional independent intentions frozen before any paid cloud model benchmark."""
from cases_pass16 import g
CASES=[
 dict(id='cloud01',text='Подхожу к колонне, но пока не стреляю в лучника у ворот.',goals=[g('move','now','колонн'),g('attack','negated','лучник')],clarify=False,forbid=[]),
 dict(id='cloud02',text='Если торговец согласится обменять кольцо на письмо, предложу сделку. Пока ничего не отдаю.',goals=[g('speak','conditional','торгов','соглас'),g('use/other','negated','отда')],clarify=False,forbid=[g('use','now')]),
 dict(id='cloud03',text='Забудь моё прежнее намерение атаковать. Сейчас говорю со стражем.',goals=[g('attack/other','negated','атак'),g('speak','now','страж')],clarify=False,forbid=[g('attack','now')]),
 dict(id='cloud04',text='Пробую взломать замок. Это лишь попытка, я ещё не открыл дверь.',goals=[g('check/use/other','now','замок'),g('use/other','negated','двер')],clarify=False,forbid=[]),
 dict(id='cloud05',text='Иду к клетке 3, 4 на втором уровне карты, если туда ведёт лестница.',goals=[g('move','conditional','3|клетк','лестниц')],clarify=False,forbid=[g('move','now')]),
 dict(id='cloud06',text='Сначала применяю свою способность, затем выхожу из укрытия только если она сработает.',goals=[g('use/other','now','способ'),g('move','conditional','укрыт','сработ')],clarify=False,forbid=[]),
 dict(id='cloud07',text='Перехожу мост, но останавливаюсь перед закрытыми воротами.',goals=[g('move','now','мост'),g('wait/move','now','ворот')],clarify=False,forbid=[]),
 dict(id='cloud08',text='Он предложил продать её мне. Я спрашиваю о цене, ничего пока не покупая.',goals=[g('speak','now','цен'),g('use/other','negated','покуп')],clarify=True,forbid=[]),
 dict(id='cloud09',text='Лада, если я скажу «нападай», это будет цитата рассказа, а не приказ.',goals=[g('speak','now','Лад')],clarify=False,forbid=[g('attack','now')]),
 dict(id='cloud10',text='Планирую завтра предложить бартер капитану, а сегодня спрашиваю Ирину о письме.',goals=[g('speak/other','intended','капитан'),g('speak','now','Ирин')],clarify=False,forbid=[]),
 dict(id='cloud11',text='Показываю письмо Ирине и Ладе по очереди, не передавая его им.',goals=[g('use/other','now','Ирин'),g('use/other','now','Лад'),g('use/other','negated','перед')],clarify=False,forbid=[]),
 dict(id='cloud12',text='Если меня заметят, отступаю за колонну; иначе остаюсь у окна и слушаю.',goals=[g('move','conditional','колонн','замет'),g('wait/observe','conditional','окн|слуш','иначе|замет')],clarify=False,forbid=[]),
]
