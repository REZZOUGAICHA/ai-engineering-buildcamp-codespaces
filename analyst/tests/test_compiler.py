import pytest
from pydantic import ValidationError

from analyst.compiler import CompileError, QueryPlan, compile_plan
from analyst.semantic import SemanticError
from conftest import run

METRICS = ["revenue", "paying_customers", "arpu", "new_customers",
           "churned_customers", "churn_rate", "active_users", "reports_created"]

# Each shape is checked for every metric: a total, a monthly series with a
# breakdown, a breakdown with a filter, and the whole data range.
SHAPES = {
    "march_2026_total": dict(start="2026-03-01", end="2026-04-01"),
    "2026_monthly_by_region": dict(start="2026-01-01", end="2026-09-01", grain="month",
                                   dimensions=["region"]),
    "q3_2025_eu_by_segment_plan": dict(start="2025-07-01", end="2025-10-01",
                                       dimensions=["segment", "plan"],
                                       filters=[{"dimension": "region", "values": ["EU"]}]),
    "all_time_total": dict(start="2025-01-01", end="2026-09-01"),
}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("metric", METRICS)
def test_compiled_sql_matches_pandas(con, compile_as, truth, metric, shape):
    plan = QueryPlan(metric=metric, **SHAPES[shape])
    got = run(con, compile_as(plan))
    want = truth.expected(plan)
    assert want, "the ground truth is empty, so this test would prove nothing"
    assert got.keys() == want.keys()
    for k in want:
        assert float(got[k]) == pytest.approx(want[k], rel=1e-9), k


def test_planted_story_1_value(con, compile_as):
    # Same number check_planted.py measured: revenue Feb -> Mar 2026 is -2.96%.
    feb, mar = (run(con, compile_as(QueryPlan(metric="revenue", start=s, end=e)))[()]
                for s, e in (("2026-02-01", "2026-03-01"), ("2026-03-01", "2026-04-01")))
    assert float((mar - feb) / feb) == pytest.approx(-0.0296, abs=5e-5)


def test_every_query_excludes_internal_accounts(compile_as):
    for metric in METRICS:
        q = compile_as(QueryPlan(metric=metric, start="2026-01-01", end="2026-02-01"))
        assert "NOT customers.is_internal" in q.sql, metric


def test_filter_values_are_parameters_not_sql(con, compile_as):
    evil = "EU') OR TRUE --"
    q = compile_as(QueryPlan(metric="revenue", start="2026-01-01", end="2026-02-01",
                             filters=[{"dimension": "region", "values": [evil]}]))
    assert evil not in q.sql
    assert evil in q.params.values()
    assert run(con, q) == {}  # no region has that name, so nothing matches


def test_cannot_compile_without_a_grant(layer):
    plan = QueryPlan(metric="revenue", start="2026-01-01", end="2026-02-01")
    with pytest.raises(CompileError, match="Grant"):
        compile_plan(plan, layer, {"role": "ceo", "row_filters": []})


def test_unknown_metric(compile_as):
    with pytest.raises(SemanticError):
        compile_as(QueryPlan(metric="engagement", start="2026-01-01", end="2026-02-01"))


@pytest.mark.parametrize("field", ["dimensions", "filters"])
def test_dimension_not_in_semantic_layer(compile_as, field):
    value = ["email"] if field == "dimensions" else [{"dimension": "email", "values": ["x"]}]
    with pytest.raises(SemanticError, match="email"):
        compile_as(QueryPlan(metric="revenue", start="2026-01-01", end="2026-02-01",
                             **{field: value}))


@pytest.mark.parametrize("bad", [
    dict(start="2026-02-01", end="2026-01-01"),                # backwards period
    dict(start="2026-01-01", end="2026-01-01"),                # empty period
    dict(dimensions=["region", "region"]),                     # duplicate dimension
    dict(grain="week"),                                        # unknown grain
    dict(sql="SELECT * FROM users"),                           # a plan can't carry SQL
    dict(filters=[{"dimension": "region", "values": []}]),     # empty filter
])
def test_bad_plans_are_rejected(bad):
    with pytest.raises(ValidationError):
        QueryPlan(**{"metric": "revenue", "start": "2026-01-01", "end": "2026-02-01", **bad})


def test_empty_period_returns_no_rows(con, compile_as):
    q = compile_as(QueryPlan(metric="revenue", start="2030-01-01", end="2030-02-01"))
    assert run(con, q) == {}  # SUM over no rows is NULL: "no data", not 0
