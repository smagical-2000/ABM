"""Pure engagement scorer (Milestone E) — points map + heat tiers."""

import pytest

from auto_search.engagement import scoring


def test_points_for_canonical_kinds():
    assert scoring.points_for("click") == 1
    assert scoring.points_for("reply") == 6
    assert scoring.points_for("meeting_booked") == 10
    assert scoring.points_for("sales_accepted_opportunity") == 10   # SFDC SAO ≈ BOFU
    # zero-point + unknown kinds score 0
    assert scoring.points_for("open") == 0
    assert scoring.points_for("delivered") == 0
    assert scoring.points_for("bounce") == 0
    assert scoring.points_for("nonsense") == 0
    assert scoring.points_for("CLICK") == 1          # case-insensitive


@pytest.mark.parametrize("score,tier", [
    (0, "Lower"), (5, "Lower"),
    (6, "Some"), (11, "Some"),
    (12, "Warm"), (20, "Warm"),
    (21, "Hot"), (100, "Hot"),
])
def test_tier_boundaries(score, tier):
    assert scoring.tier_for(score) == tier


def test_heat_clamps_and_tiers():
    assert scoring.heat(17) == scoring.Heat(17, "Warm")
    assert scoring.heat(-5).tier == "Lower"          # never negative
