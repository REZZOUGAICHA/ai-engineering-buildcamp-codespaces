"""Phase 6: driver analysis finds every planted story (numbers from data/PLANTED.md).

PLANTED.md was measured by data/check_planted.py, which reads the database
directly. Here the same numbers must come out through the full safe path
(policy -> compiler -> guard -> executor), so two independent routes agree.
"""

from datetime import date

import pytest

from analyst.drivers import explain_change
from analyst.executor import Executor, ResultStore
from analyst.policy import OTHER

METRICS = ["revenue", "paying_customers", "arpu", "new_customers", "churned_customers",
           "churn_rate", "active_users", "reports_created"]


@pytest.fixture(scope="module")
def ex(layer, policy, con):
    return Executor(layer, policy)


def report(ex, metric, month, role="ceo", **kw):
    return explain_change(ResultStore(ex, role), metric, month, **kw)


def dim(rep, name):
    return next(d for d in rep.dimensions if d.dimension == name)


def test_story1_revenue_drop_march_2026(ex):
    r = report(ex, "revenue", date(2026, 3, 1))
    assert r.change_pct == pytest.approx(-0.0296, abs=5e-5)
    assert r.threshold_pct == pytest.approx(0.0146, abs=5e-5)
    assert r.times_threshold == pytest.approx(2.03, abs=0.005) and r.notable
    assert (dim(r, "region").top_group, round(dim(r, "region").top_share, 2)) == ("EU", 1.35)
    assert (dim(r, "segment").top_group, round(dim(r, "segment").top_share, 2)) == ("SMB", 1.29)
    assert dim(r, "region").concentrated
    assert [str(a["date"]) for a in r.annotations] == ["2026-03-03"]


def test_story2_active_users_rise_september_2025(ex):
    r = report(ex, "active_users", date(2025, 9, 1))
    assert r.change_pct == pytest.approx(0.2052, abs=5e-5)
    assert r.times_threshold == pytest.approx(3.86, abs=0.005) and r.notable
    assert (dim(r, "plan").top_group, round(dim(r, "plan").top_share, 2)) == ("pro", 1.01)
    assert [a["type"] for a in r.annotations] == ["product"]


def test_story3_flat_month_is_not_notable(ex):
    r = report(ex, "revenue", date(2025, 11, 1))
    assert r.change_pct == pytest.approx(0.0073, abs=5e-5)
    assert r.times_threshold == pytest.approx(0.41, abs=0.005) and r.notable is False
    # A company event happened that month, but the change isn't notable:
    # the writer must not tie the two together.
    assert [a["type"] for a in r.annotations] == ["operations"]


def test_story4_churn_rate_rise_is_mostly_mix(ex):
    r = report(ex, "churn_rate", date(2026, 7, 1))
    assert r.change_pct == pytest.approx(0.1960, abs=5e-5)
    assert r.times_threshold == pytest.approx(2.86, abs=0.005) and r.notable
    seg = dim(r, "segment")
    assert seg.mix + seg.rate == pytest.approx(r.change, abs=1e-12)  # exact split
    assert (round(seg.mix * 100, 3), round(seg.rate * 100, 3)) == (0.699, -0.114)
    assert [a["type"] for a in r.annotations] == ["marketing"]


@pytest.mark.parametrize("metric", [m for m in METRICS if m not in ("arpu", "churn_rate")])
def test_group_changes_add_up_to_the_total_change(ex, metric):
    r = report(ex, metric, date(2026, 3, 1))
    for d in r.dimensions:
        assert sum(c.delta for c in d.contributions) == pytest.approx(r.change, abs=1e-6)


@pytest.mark.parametrize("metric", ["arpu", "churn_rate"])
def test_mix_plus_rate_is_the_total_change(ex, metric):
    r = report(ex, metric, date(2026, 3, 1))
    for d in r.dimensions:
        assert d.mix + d.rate == pytest.approx(r.change, abs=1e-9)


def test_eu_manager_drivers_are_eu_only(ex, truth):
    r = report(ex, "revenue", date(2026, 3, 1), role="eu_manager")
    assert "region" not in [d.dimension for d in r.dimensions]  # fixed by the row filter
    from analyst.plan import QueryPlan
    eu = truth.expected(QueryPlan(metric="revenue", start=date(2026, 3, 1), end=date(2026, 4, 1),
                                  filters=[{"dimension": "region", "values": ["EU"]}]))
    assert r.value_b == pytest.approx(eu[()])


def test_product_manager_never_gets_region_drivers(ex):
    r = report(ex, "active_users", date(2025, 9, 1), role="product_manager")
    assert [d.dimension for d in r.dimensions] == ["segment", "plan"]


def test_small_groups_stay_merged_in_drivers(ex):
    # New customers by region are small each month (see test_policy), so some
    # regions must arrive merged, and LATAM (3 customers in total) never alone.
    r = report(ex, "new_customers", date(2026, 3, 1))
    groups = [c.group for c in dim(r, "region").contributions]
    assert OTHER in groups and "LATAM" not in groups
    assert any("combined" in n for n in r.notes)


def test_not_enough_history_is_said_not_guessed(ex):
    r = report(ex, "revenue", date(2025, 3, 1))
    assert r.notable is None and any("Not enough history" in n for n in r.notes)


def test_no_driver_is_named_for_a_flat_month(ex):
    # Measured: EU has 71% of the +0.73% November change, but that's noise.
    r = report(ex, "revenue", date(2025, 11, 1))
    assert not any(d.concentrated for d in r.dimensions)
    assert any("not notable" in n for n in r.notes)
