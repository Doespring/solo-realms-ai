"""A world with no verified place at all must never be published ready."""
import os
os.environ['SOLO_TEST']='1'

from solo.prepared_world import PreparedWorldValidator


def test_a_world_without_locations_is_quarantined(tmp_path):
    validator = PreparedWorldValidator(None, [])
    batch = dict(title="Пусто", start="Мост", ruleset="dnd2014",
                 minimum_level=1, maximum_level=5, party_size=4,
                 metadata_evidence=[],
                 entities=[dict(key="bridge", kind="location",
                                  # A place with no usable name cannot verify.
                                  data=dict(name="", description="Мост."))])
    result = validator.compile([batch], "dnd2014")
    reasons = [q.get("reason", "") for q in result["quarantine"]]
    assert any("локаци" in r for r in reasons), (
        "the empty world was published: %r" % result["quarantine"])