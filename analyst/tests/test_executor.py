import time

import pytest

from analyst.executor import Executor, QueryTimeout, ResultStore, connect, execute
from analyst.plan import QueryPlan
from analyst.policy import OTHER, PolicyError
from conftest import DB, run

MARCH = dict(start="2026-03-01", end="2026-04-01")


@pytest.fixture(scope="module")
def executor(layer, policy):
    return Executor(layer, policy)


def test_result_matches_the_plain_query(executor, con, compile_as):
    plan = QueryPlan(metric="revenue", dimensions=["segment"], **MARCH)
    result = executor.run(plan, "ceo", "r1")
    assert {(r["segment"],): r["value"] for r in result.rows} == {
        k: float(v) for k, v in run(con, compile_as(plan)).items()}


def test_result_keeps_its_plan_sql_and_role(executor):
    plan = QueryPlan(metric="churn_rate", **MARCH)
    r = executor.run(plan, "eu_manager", "r7")
    assert (r.result_id, r.role, r.plan) == ("r7", "eu_manager", plan)
    assert "LIMIT" in r.sql and r.params["r0_0"] == "EU"
    assert r.columns == ["value", "numerator", "denominator"]
    assert str(r.data_as_of) == "2026-08-31"


def test_no_internal_count_column_reaches_the_result(executor):
    r = executor.run(QueryPlan(metric="revenue", dimensions=["region"], **MARCH), "ceo", "r1")
    assert all("_n_entities" not in row for row in r.rows) and "_n_entities" not in r.columns


def test_small_region_is_merged(executor):
    r = executor.run(QueryPlan(metric="revenue", dimensions=["region"], **MARCH), "ceo", "r1")
    regions = {row["region"] for row in r.rows}
    assert "LATAM" not in regions and OTHER in regions and r.notes


def test_policy_still_applies(executor):
    with pytest.raises(PolicyError):
        executor.run(QueryPlan(metric="active_users", **MARCH), "finance_analyst", "r1")


def test_too_many_rows_are_cut_and_flagged(layer, policy):
    small = Executor(layer, policy, max_rows=3)
    r = small.run(QueryPlan(metric="revenue", start="2025-01-01", end="2026-09-01",
                            grain="month"), "ceo", "r1")
    assert len(r.rows) == 3 and r.truncated and any("first 3 rows" in n for n in r.notes)


def test_slow_query_is_stopped():
    con = connect(DB)
    began = time.monotonic()
    with pytest.raises(QueryTimeout):
        execute(con, "SELECT COUNT(*) FROM range(100000) a, range(100000) b "
                     "WHERE a.range * b.range = 7", {}, timeout=0.5)
    assert time.monotonic() - began < 5
    con.close()


def test_store_numbers_results_per_request(executor):
    store = ResultStore(executor, "ceo")
    a = store.query(QueryPlan(metric="revenue", **MARCH))
    b = store.query(QueryPlan(metric="arpu", **MARCH))
    assert (a.result_id, b.result_id) == ("r1", "r2")
    assert store.get("r2") is b and store.get("r3") is None


def test_no_canary_value_in_any_result(executor, layer):
    # Every metric, broken down every allowed way: no email can come out.
    for metric in layer.metrics:
        r = executor.run(QueryPlan(metric=metric, start="2025-01-01", end="2026-09-01",
                                   grain="month", dimensions=["region", "segment", "plan"]),
                         "ceo", "r1")
        assert "canary-" not in str(r.rows) and "@example.test" not in str(r.rows)
