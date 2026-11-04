import copy

import pytest
import yaml

from analyst import policy as access
from analyst.compiler import QueryPlan
from analyst.policy import OTHER, PolicyError
from analyst.semantic import SemanticError
from conftest import run
from test_compiler import METRICS, SHAPES

RAW = yaml.safe_load(access.POLICY_FILE.read_text(encoding="utf-8"))
MARCH = dict(start="2026-03-01", end="2026-04-01")


def plan(metric="revenue", **kw):
    return QueryPlan(metric=metric, **{**MARCH, **kw})


# --- Loading the policy -------------------------------------------------------

@pytest.mark.parametrize("change", [
    lambda r: r["roles"]["finance_analyst"]["metrics"].append("profit"),       # unknown metric
    lambda r: r["roles"]["product_manager"]["dimensions"].append("country"),   # unknown dimension
    lambda r: r["roles"]["ceo"].update(can_see_everything=True),               # unknown key
    lambda r: r.update(min_group_size=1),                                       # rule switched off
    lambda r: r["hidden_columns"].append("customers.region"),                  # dimension is hidden
])
def test_broken_policy_fails_loudly(layer, tmp_path, change):
    raw = copy.deepcopy(RAW)
    change(raw)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(SemanticError):
        access.load(layer, path)


# --- Deny by default and out-of-scope requests ---------------------------------

@pytest.mark.parametrize("role", ["intern", "", "CEO", "ceo "])
def test_unknown_role_sees_nothing(policy, layer, role):
    with pytest.raises(PolicyError, match="Unknown role"):
        access.authorize(plan(), role, policy, layer)
    with pytest.raises(PolicyError):
        access.allowed_metrics(policy, layer, role)


@pytest.mark.parametrize("role, request_", [
    ("finance_analyst", dict(metric="active_users")),
    ("finance_analyst", dict(metric="reports_created")),
    ("product_manager", dict(metric="revenue")),
    ("product_manager", dict(metric="churn_rate")),
    ("product_manager", dict(metric="active_users", dimensions=["region"])),
    ("product_manager", dict(metric="active_users",
                             filters=[{"dimension": "region", "values": ["EU"]}])),
    ("eu_manager", dict(filters=[{"dimension": "region", "values": ["NA"]}])),
    ("eu_manager", dict(filters=[{"dimension": "region", "values": ["EU", "NA"]}])),
])
def test_out_of_scope_requests_are_refused(policy, layer, role, request_):
    with pytest.raises(PolicyError) as e:
        access.authorize(plan(**request_), role, policy, layer)
    # A refusal says what is allowed; it never contains data.
    assert not any(ch.isdigit() for ch in str(e.value)), str(e.value)


@pytest.mark.parametrize("role, request_", [
    ("ceo", dict(metric="active_users", dimensions=["region"])),
    ("finance_analyst", dict(metric="churn_rate", dimensions=["region"])),
    ("product_manager", dict(metric="active_users", dimensions=["plan", "segment"])),
    ("eu_manager", dict(filters=[{"dimension": "region", "values": ["EU"]}])),
])
def test_in_scope_requests_are_allowed(policy, layer, role, request_):
    access.authorize(plan(**request_), role, policy, layer)


def test_each_role_sees_only_its_metrics(policy, layer):
    assert access.allowed_metrics(policy, layer, "ceo") == METRICS
    assert access.allowed_metrics(policy, layer, "product_manager") == [
        "active_users", "reports_created"]


# --- Row filter -----------------------------------------------------------------

@pytest.mark.parametrize("metric", METRICS)
def test_row_filter_is_in_every_eu_query(compile_as, metric):
    q = compile_as(plan(metric, dimensions=["segment"]), "eu_manager")
    assert "customers.region IN ($r0_0)" in q.sql
    assert q.params["r0_0"] == "EU"


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("metric", METRICS)
def test_eu_manager_gets_eu_only_numbers(con, compile_as, truth, metric, shape):
    p = QueryPlan(metric=metric, **SHAPES[shape])
    got = run(con, compile_as(p, "eu_manager"))
    # Hand computation: the same plan with an explicit region = EU filter, in pandas.
    eu_only = p.model_copy(update={"filters": [*p.filters, access.Filter(
        dimension="region", values=["EU"])]})
    want = truth.expected(eu_only)
    assert want
    assert got.keys() == want.keys()
    for k in want:
        assert float(got[k]) == pytest.approx(want[k], rel=1e-9), k


def test_eu_manager_region_breakdown_shows_only_eu(con, compile_as):
    got = run(con, compile_as(plan(dimensions=["region"]), "eu_manager"))
    assert list(got) == [("EU",)]


# --- Minimum group size --------------------------------------------------------

def protected(con, layer, query, k=5):
    rows = con.execute(query.sql, query.params).fetchall()
    return access.protect_small_groups([dict(zip(query.columns, r)) for r in rows],
                                       query, layer, k)


def test_latam_is_merged_into_other(con, layer, compile_as):
    q = compile_as(plan(dimensions=["region"]))
    raw = run(con, q)
    assert ("LATAM",) in raw  # the planted small region really is in the raw result
    rows, notes = protected(con, layer, q)
    regions = [r["region"] for r in rows]
    assert "LATAM" not in regions and OTHER in regions
    assert all("_n_entities" not in r for r in rows)
    assert sum(r["value"] for r in rows) == sum(raw.values())  # nothing lost, only merged
    assert notes


def test_a_filter_narrowing_to_few_customers_is_hidden(con, layer, compile_as):
    q = compile_as(plan(filters=[{"dimension": "region", "values": ["LATAM"]}]))
    assert run(con, q)  # the raw query has a value...
    rows, notes = protected(con, layer, q)
    assert rows[0]["value"] is None and notes  # ...but it's hidden from the user


@pytest.mark.parametrize("metric", METRICS)
def test_eu_breakdowns_never_show_small_groups(con, layer, compile_as, metric):
    q = compile_as(plan(metric, start="2025-01-01", end="2026-09-01", grain="month",
                        dimensions=["segment", "plan"]), "eu_manager")
    keys = q.columns[:q.columns.index("value")]
    n_of = {tuple(r[:len(keys)]): r[-1] for r in con.execute(q.sql, q.params).fetchall()}
    rows, _ = protected(con, layer, q)
    for r in rows:
        key = tuple(r[k] for k in keys)
        if OTHER not in key and r["value"] is not None:
            assert n_of[key] >= 5, key  # every group shown on its own has >= 5 customers


@pytest.mark.parametrize("metric", ["new_customers", "churned_customers"])
def test_eu_fine_breakdown_really_has_small_groups(con, compile_as, metric):
    # Guard for the test above: if no group were small, it would pass trivially.
    # Measured on 2026-11-04: only these two metrics have small groups here
    # (70 and 48 rows); for the other six the protection never triggers.
    q = compile_as(plan(metric, start="2025-01-01", end="2026-09-01", grain="month",
                        dimensions=["segment", "plan"]), "eu_manager")
    assert any(r[-1] < 5 for r in con.execute(q.sql, q.params).fetchall())


# --- protect_small_groups on hand-made rows -----------------------------------

class FakeQuery:
    def __init__(self, metric, dims, ratio=False):
        self.plan = QueryPlan(metric=metric, dimensions=dims, **MARCH)
        self.columns = dims + (["value", "numerator", "denominator"] if ratio else ["value"]) + [
            "_n_entities"]


def rows_(*spec):
    return [{"region": r, "value": v, "_n_entities": n} for r, v, n in spec]


def test_no_small_groups_is_unchanged(layer):
    rows, notes = access.protect_small_groups(
        rows_(("EU", 10, 9), ("NA", 20, 8)), FakeQuery("revenue", ["region"]), layer, 5)
    assert rows == [{"region": "EU", "value": 10}, {"region": "NA", "value": 20}] and not notes


def test_small_groups_big_enough_together_are_merged_alone(layer):
    rows, _ = access.protect_small_groups(
        rows_(("EU", 10, 9), ("A", 1, 3), ("B", 2, 3)), FakeQuery("revenue", ["region"]), layer, 5)
    assert {r["region"]: r["value"] for r in rows} == {"EU": 10, OTHER: 3}


def test_one_small_group_also_takes_the_smallest_visible_group(layer):
    rows, _ = access.protect_small_groups(
        rows_(("EU", 10, 9), ("NA", 20, 6), ("LATAM", 1, 3)), FakeQuery("revenue", ["region"]),
        layer, 5)
    # LATAM alone (3) is too small, so NA (6, the smallest visible) joins it.
    assert {r["region"]: r["value"] for r in rows} == {"EU": 10, OTHER: 21}


def test_everything_small_is_blanked(layer):
    rows, _ = access.protect_small_groups(
        rows_(("A", 1, 1), ("B", 2, 2)), FakeQuery("revenue", ["region"]), layer, 5)
    assert rows == [{"region": OTHER, "value": None}]


def test_ratio_groups_merge_by_numerator_and_denominator(layer):
    q = FakeQuery("churn_rate", ["region"], ratio=True)
    rows = [{"region": "EU", "value": 0.1, "numerator": 10, "denominator": 100, "_n_entities": 100},
            {"region": "A", "value": 0.5, "numerator": 1, "denominator": 2, "_n_entities": 2},
            {"region": "B", "value": 0.0, "numerator": 0, "denominator": 4, "_n_entities": 4}]
    out, _ = access.protect_small_groups(rows, q, layer, 5)
    other = [r for r in out if r["region"] == OTHER][0]
    # 1/6, not the average of 0.5 and 0.0.
    assert (other["numerator"], other["denominator"]) == (1, 6)
    assert other["value"] == pytest.approx(1 / 6)


def test_small_total_is_blanked(layer):
    q = FakeQuery("revenue", [])
    rows, notes = access.protect_small_groups([{"value": 99, "_n_entities": 2}], q, layer, 5)
    assert rows == [{"value": None}] and notes
