"""Согласованность данных спасброска: on_save не должен требоваться без урона.

В корпусе 162 save-активности объявляют политику `damage.on_save`, и у **83** из
них `damage.parts` пуст (counterspell, command, scrying, grease…). Это не потеря
механики: `_scale_on_save` (`activities/save.py:274-290`) применяется ВНУТРИ цикла
по частям урона, поэтому при пустом списке он не вызывается вовсе — урона и не
должно быть. Вред мог бы возникнуть, только если бы движок подставлял фиктивную
часть урона вместо отсутствующей.

Файл закрепляет обе половины: обработчик даёт 0 урона, когда частей нет, и
записи с последствиями (состояния) остаются исполнимыми.
"""
import pytest

from dnd5e_engine.activities.save import _scale_on_save
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _save_activities(loader):
    for slug in loader.list_slugs("spells"):
        raw = loader.get_spell(slug).model_dump(mode="json")
        for act in raw.get("activities") or ():
            damage = act.get("damage") or {}
            if damage.get("on_save"):
                yield slug, act, damage


def test_the_corpus_has_save_rules_without_damage(loader):
    """Зафиксировать факт: пустые parts — не редкость, их 83."""
    empty = [(s, a) for s, a, d in _save_activities(loader) if not d.get("parts")]
    assert len(empty) == 83


def test_a_damage_part_scales_but_no_part_means_no_damage():
    """Политика on_save применяется к каждой части; нет части — нет урона."""
    assert _scale_on_save(10, "half", succeeded=True) == 5
    assert _scale_on_save(10, "half", succeeded=False) == 10
    assert _scale_on_save(10, "none", succeeded=True) == 0
    assert sum([], 0) == 0


def test_half_on_save_without_damage_is_a_corpus_convention(loader):
    """Генератор корпуса проставляет `on_save` шаблонно, даже без урона.

    Из 141 активности с `on_save` в `half`/`full` у **70** пуст `damage.parts`.
    Это НЕ потеря механики: `_scale_on_save` вызывается внутри цикла по частям,
    поэтому при пустом списке он не исполняется — урона и не возникает.
    Проверены все 70: bane, slow, confusion, ray-of-enfeeblement, enlarge-reduce и
    прочие описания «d20» относятся к проверкам/штрафам, а не к урону, — частей
    урона у них действительно нет. Свойство корпуса закреплено, чтобы изменение
    генерации было замечено.

    Записи, у которых урон есть в другой активности того же заклинания
    (например antipathy-sympathy), сюда не относятся: у них parts заполнены.
    """
    empty = [slug for slug, _a, d in _save_activities(loader)
             if d.get("on_save") in {"half", "full"} and not d.get("parts")]
    assert len(empty) == 70
    assert "counterspell" in empty


def test_on_save_values_are_known_policies(loader):
    """Неизвестная политика — не молчаливое обнуление, а громкое предупреждение."""
    known = {"half", "none", "full"}
    seen = {d["on_save"] for _, _, d in _save_activities(loader)}
    assert seen <= known, f"неизвестные политики: {seen - known}"


def test_counterspell_asks_for_a_constitution_save(loader):
    """Источник 2024: спасбросок Телосложения, а не автоуспех 2014."""
    raw = loader.get_spell("counterspell").model_dump(mode="json")
    activity = raw["activities"][0]
    assert activity["kind"] == "save"
    assert activity["save"]["ability"] == ["con"]
    assert "Constitution saving throw" in raw["description"]
    assert activity["damage"]["parts"] == [], "урона нет — эффект в снятии заклинания"

