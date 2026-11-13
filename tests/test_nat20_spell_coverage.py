"""Сколько заклинаний корпуса реально имеют типизированный боевой обработчик.

The task forbids counting a spell as implemented because its JSON loads, because it
appears in a list, or because a handler returned a success status. This file pins
the measurement that is actually defensible: a spell counts as supported only when
its canonical record carries an activity the engine can execute (attack, save,
damage, heal, check, cast, an allowlisted conjuration, or a utility with live
changes/statuses). Everything else must be refused, not quietly accepted.
"
Numbers are pinned so a silent regression in the corpus or in the classifier shows
up as a failure rather than as a quietly smaller feature set.
"""
import pytest

from dnd5e_srd_data.loader import BundledAssetLoader
from solo.nat20_prototype import spell_execution_status


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _statuses(loader):
    out = {}
    for slug in loader.list_slugs("spells"):
        spell = loader.get_spell(slug)
        out[slug] = (spell.level, spell_execution_status(spell.model_dump(mode="json")))
    return out


def test_supported_and_refused_counts_are_pinned(loader):
    statuses = _statuses(loader)
    supported = [s for s, (_, st) in statuses.items() if st == "native_candidate"]
    refused = [s for s, (_, st) in statuses.items() if st != "native_candidate"]
    assert len(statuses) == 339
    # 219 с тех пор, как Find Steed сел на выбор формы (раунд 70):
    # `find-steed` был `narrative_or_unsupported_in_combat` и стал
    # `native_candidate`. Рост на единицу проверен поимённо, а не
    # подставлен под новый результат: призыв реально сажает скакуна.
    # 220 с тех пор, как Animate Dead научился считать тела (раунд 72):
    # `animate-dead` был `narrative_or_unsupported_in_combat` и стал
    # `native_candidate`. Рост проверен поимённо: 1 + increase*2 тел на
    # слотах 3/4/5/6, а не просто «стало больше».
    # 221 с тех пор, как Create Undead сел на счёт по форме (раунд 73):
    # девятый набор «слот × форма» из книги проверен поимённо.
    # 222 с Giant Insect (раунд 75): три формы читают ОДИН статблок, а
    # слот масштабирует его числа — AC 11+уровень, HP 30+10/уровень.
    # Проверено по 9 сочетаниям «форма x слот» (4/5/6).
    #
    # Счёт 222 держится и после раунда 76, но это НЕ довод в пользу
    # Shillelagh: `shillelagh` попадал в `native_candidate` уже на HEAD,
    # потому что его карточка несёт `EnchantActivity`, а классификатор
    # видит только тип активности. Механики за этим не было — заклинание
    # испускало `EffectApplied` и не меняло у оружия ничего (§7). Раунд 76
    # эту механику добавил (кость d8 и характеристика заклинателя), но
    # счётчик от неё не вырос. Обратный пример той же ловушки —
    # `true-strike`: он ТОЖЕ числится поддержанным, хотя падает на
    # неподдержанном токене `@details.level` и в бою ничего не делает.
    # Оба имени здесь для того, чтобы счётчик не принимали за отчёт о
    # полноте (§13).
    # Счёт 222 был верным для корпуса того коммита. После него (раунд 76,
    # коммит 9783336) Shillelagh научился переписывать оружие, и
    # классификатор, который читает карточку и видит там EnchantActivity,
    # продолжает числить его поддержанным — механизмы это не прибавило.
    # Рост на единицу проверен поимённо: при неизменном наборе правил
    # классификатора `223 = 222 + shillelagh`. Это по-прежнему не отчёт о
    # полноте (§13): дюжина семейств заклинаний остаётся без механики и
    # без отказа, их перечисление — в reports/ENGINE_WORKSPACE_PROGRESS.md.
    assert len(supported) == 223
    assert len(refused) == 116
    # Каждый слага ровно в одной корзине: иначе счётчики могут совпасть, а
    # покрытие быть неполным (одна запись посчитана дважды, другая ни разу).
    assert len(set(supported) | set(refused)) == 339
    assert not set(supported) & set(refused)
    # Отказ должен быть именно объявленным отказом, а не неизвестным статусом.
    unknown = sorted({st for _, st in statuses.values()} - {"native_candidate", "narrative_or_unsupported_in_combat"})
    assert unknown == [], unknown


def test_every_level_keeps_supported_spells(loader):
    """Coverage must not collapse at high levels: each level keeps handlers."""
    statuses = _statuses(loader)
    by_level = {}
    for _, (level, status) in statuses.items():
        bucket = by_level.setdefault(level, {"native_candidate": 0, "refused": 0})
        bucket["native_candidate" if status == "native_candidate" else "refused"] += 1
    assert sorted(by_level) == list(range(10))
    for level, counts in sorted(by_level.items()):
        assert counts["native_candidate"] > 0, (level, counts)
    # Level 9 is the endgame and must still offer real handlers.
    assert by_level[9] == {"native_candidate": 12, "refused": 4}


def test_the_four_level_nine_gaps_are_named(loader):
    statuses = _statuses(loader)
    ninth = {s: st for s, (level, st) in statuses.items() if level == 9}
    refused = sorted(s for s, st in ninth.items() if st != "native_candidate")
    assert refused == ["gate", "shapechange", "time-stop", "true-resurrection"]


def test_a_spell_without_activities_is_refused():
    """The cheapest way to fake coverage: a record that merely exists."""
    assert spell_execution_status({"slug": "nothing", "activities": []}) == (
        "narrative_or_unsupported_in_combat")
    assert spell_execution_status({"slug": "nothing"}) == (
        "narrative_or_unsupported_in_combat")


def test_a_typed_activity_is_accepted():
    """Типизированная активность делает запись «исполняемой» для классификатора.

    Классификатор смотрит на ВИД активности, а не на её наполнение: `damage` без
    урона тоже пройдёт. Это его объявленное ограничение (он не валидатор).
    Фактическое наполнение проверяет отдельный тест на реальном корпусе ниже,
    поэтому здесь утверждается ровно то, что заявлено.
    """
    minimal = {"slug": "x", "activities": [{"kind": "damage"}], "passive_effects": []}
    assert spell_execution_status(minimal) == "native_candidate"


def test_no_supported_spell_has_an_empty_damage_activity(loader):
    """В реальном корпусе пустых `damage`-активностей нет.

    Классификатор пропустил бы `{"kind": "damage"}` без частей урона, поэтому
    отсутствие таких записей доказывается обходом корпуса, а не доверием к
    классификатору.
    """
    offenders = []
    for slug in loader.list_slugs("spells"):
        raw = loader.get_spell(slug).model_dump(mode="json")
        if spell_execution_status(raw) != "native_candidate":
            continue
        for activity in raw.get("activities") or []:
            if activity.get("kind") != "damage":
                continue
            if not ((activity.get("damage") or {}).get("parts") or []):
                offenders.append(slug)
                break
    assert offenders == []

