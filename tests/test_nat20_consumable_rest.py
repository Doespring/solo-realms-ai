"""Consumables must not come back on a rest.

`recover_item_and_activity_pools` is the safe entry point: it reads each item
`uses.recovery` rules through the loader, and a consumable (`auto_destroy` with
`recovery: []`) matches no rule for any period, so its spent count is preserved.

`recover_item_uses(..., recovery=None)` is the legacy no-data path and DOES refill
everything. That path must never be reached from the host, and this file pins both
halves: the safe call preserves, and the dangerous one is named so a regression
that routes the host through it is caught.
"""
import pytest

from dnd5e_engine.rest import recover_item_and_activity_pools, recover_item_uses
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def test_comet_is_a_consumable(loader):
    uses = loader.get_item("comet").uses
    assert uses.recovery == []
    assert uses.max == "1"


@pytest.mark.parametrize("period", ["sr", "lr", "dawn", "day"])
def test_a_spent_consumable_is_not_restored_on_any_event(loader, period):
    out = recover_item_and_activity_pools(
        {"item_use:comet": {"spent": 1}}, period, ["comet"], loader
    )
    assert out == {"item_use:comet": 1}


def test_an_unspent_consumable_stays_unspent(loader):
    out = recover_item_and_activity_pools(
        {"item_use:comet": {"spent": 0}}, "lr", ["comet"], loader
    )
    assert out == {"item_use:comet": 0}


def test_equipment_not_carried_is_left_alone(loader):
    out = recover_item_and_activity_pools(
        {"item_use:comet": {"spent": 1}}, "lr", [], loader
    )
    assert out == {"item_use:comet": 1}


def test_the_legacy_no_data_path_refills_which_is_why_the_loader_is_required():
    out = recover_item_uses({"item_use:comet": {"spent": 1}}, "lr", None)
    assert out == {"comet": 0}


def test_charges_recharge_when_a_rule_matches(loader):
    recharging = []
    for slug in loader.list_slugs("items"):
        item = loader.get_item(slug)
        uses = getattr(item, "uses", None)
        if uses is not None and uses.recovery and not uses.auto_destroy:
            recharging.append(slug)
    assert recharging, "корпус должен содержать перезаряжаемые предметы"
    slug = recharging[0]
    period = loader.get_item(slug).uses.recovery[0].period
    out = recover_item_and_activity_pools(
        {"item_use:" + slug: {"spent": 1}}, period, [slug], loader
    )
    assert out["item_use:" + slug] == 0
