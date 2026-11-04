"""The "why": which groups drove a month-over-month change. Pure code, no LLM.

For a metric and a month, compared with the month before:
- is the change notable? (bigger than NOTABLE_K x the median month-to-month
  change over the LOOKBACK months before it; the same rule as data/check_planted.py)
- for each breakdown the role may use: each group's share of the change
  (additive metrics), or a mix/rate split (ratio metrics: did the rate change
  inside groups, or did the weight of groups change?)
- company events (annotations.yaml) dated in the two months.

All data comes through the Executor, so access rules, row filters and
small-group merging apply to the "why" exactly as to any other answer.
The report says what the data shows and what happened at the same time. It
never says what caused what: data like this can't prove a cause.
"""

import statistics
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

from analyst import policy as access
from analyst.plan import Filter, QueryPlan
from analyst.semantic import CONFIG_DIR

NOTABLE_K = 2
LOOKBACK = 6
MOST = 0.5  # one group "accounts for most of the change" above this share
# Shares of a tiny change are noise (measured: a +0.05% change gave "shares" of
# 1400% and 2100%), so a driver is only named when the change is notable.
ANNOTATIONS_FILE = CONFIG_DIR / "annotations.yaml"


class Contribution(BaseModel):
    group: str
    value_a: float | None
    value_b: float | None
    delta: float | None
    share: float | None  # of the total change (additive metrics only)


class DimensionDrivers(BaseModel):
    dimension: str
    contributions: list[Contribution]    # biggest share of the change first
    top_group: str | None = None
    top_share: float | None = None
    concentrated: bool = False           # top group has more than half the change
    mix: float | None = None             # ratio metrics: change from group weights
    rate: float | None = None            # ratio metrics: change of rates inside groups


class DriverReport(BaseModel):
    metric: str
    month: date
    prev_month: date
    value_a: float | None
    value_b: float | None
    change: float | None
    change_pct: float | None
    threshold_pct: float | None          # None if there isn't enough history
    times_threshold: float | None
    notable: bool | None
    dimensions: list[DimensionDrivers]
    annotations: list[dict]
    result_ids: list[str]
    notes: list[str]


def add_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def load_annotations(path=ANNOTATIONS_FILE):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["annotations"]


def pct(a, b):
    return (b - a) / abs(a) if a else None


def notable_test(series, month):
    """(threshold, times) for the change into `month`, or (None, None) without history."""
    changes = []
    for i in range(1, LOOKBACK + 1):
        m = add_months(month, -i)
        c = pct(series.get(add_months(m, -1)), series.get(m)) \
            if series.get(add_months(m, -1)) is not None and series.get(m) is not None else None
        if c is None:
            return None, None
        changes.append(abs(c))
    threshold = NOTABLE_K * statistics.median(changes)
    change = pct(series.get(add_months(month, -1)), series.get(month))
    if change is None or threshold == 0:
        return threshold, None
    return threshold, abs(change) / threshold


def ratio_parts(rows, month):
    """{group: (numerator, denominator)} for one month of a ratio metric."""
    return {r["_group"]: (r["numerator"], r["denominator"]) for r in rows
            if r["period"] == month and r["denominator"]}


def mix_rate(a, b):
    """Split a ratio's change: mix = sum (wB - wA) * rA, rate = sum wB * (rB - rA).
    Same formula as data/check_planted.py; mix + rate is exactly the total change.
    A group missing in one month counts with weight 0 there."""
    na, nb = sum(d for _, d in a.values()), sum(d for _, d in b.values())
    mix = rate = 0.0
    for g in set(a) | set(b):
        ra = a[g][0] / a[g][1] if g in a else None
        rb = b[g][0] / b[g][1] if g in b else None
        wa, wb = (a[g][1] / na if g in a else 0.0), (b[g][1] / nb if g in b else 0.0)
        ra = rb if ra is None else ra   # a new group adds only through its weight
        rb = ra if rb is None else rb
        mix += (wb - wa) * ra
        rate += wb * (rb - ra)
    return mix, rate


def explain_change(store, metric, month, filters=(), annotations=None):
    """DriverReport for `metric` in `month` vs the month before, for store.role."""
    ex, role = store.executor, store.role
    layer, policy = ex.layer, ex.policy
    m = layer.metric(metric)
    month, prev = add_months(month, 0), add_months(month, -1)
    filters = [f if isinstance(f, Filter) else Filter(**f) for f in filters]
    notes, result_ids = [], []

    # Total series, with enough history for the notable test.
    total = store.query(QueryPlan(metric=metric, start=add_months(month, -(LOOKBACK + 1)),
                                  end=add_months(month, 1), grain="month", filters=filters))
    result_ids.append(total.result_id)
    series = {r["period"]: r["value"] for r in total.rows if r["value"] is not None}
    va, vb = series.get(prev), series.get(month)
    threshold, times = notable_test(series, month)
    if threshold is None:
        notes.append(f"Not enough history before {month} to judge normal variation.")

    filtered = {f.dimension for f in filters} | \
        {r.dimension for r in ([access.role_rules(policy, role).row_filter] if
                               access.role_rules(policy, role).row_filter else [])}
    dims = [d for d in m.allowed_dimensions
            if d in access.allowed_dimensions(policy, layer, role) and d not in filtered]

    out = []
    for d in dims:
        r = store.query(QueryPlan(metric=metric, start=prev, end=add_months(month, 1),
                                  grain="month", dimensions=[d], filters=filters))
        result_ids.append(r.result_id)
        for n in r.notes:
            if n not in notes:
                notes.append(n)
        rows = [dict(row, _group=str(row[d])) for row in r.rows]
        by = {}
        for row in rows:
            by.setdefault(row["_group"], {})[row["period"]] = row["value"]
        total_delta = (vb - va) if None not in (va, vb) else None
        contributions = []
        for g, vals in by.items():
            a, b = vals.get(prev), vals.get(month)
            if m.aggregation == "ratio":
                delta = (b - a) if None not in (a, b) else None
                share = None
            else:  # additive and distinct counts: groups are disjoint customers, so deltas add up
                a, b = a or 0, b or 0
                delta = b - a
                share = delta / total_delta if total_delta else None
            contributions.append(Contribution(group=g, value_a=a, value_b=b, delta=delta,
                                              share=share))
        dd = DimensionDrivers(dimension=d, contributions=contributions)
        if m.aggregation == "ratio":
            dd.mix, dd.rate = mix_rate(ratio_parts(rows, prev), ratio_parts(rows, month))
            dd.contributions.sort(key=lambda c: -abs(c.delta or 0))
        elif total_delta:
            dd.contributions.sort(key=lambda c: -(c.share or 0))
            top = dd.contributions[0]
            dd.top_group, dd.top_share = top.group, top.share
            dd.concentrated = top.share > MOST
        out.append(dd)

    notable = None if times is None else times >= 1
    if not notable:
        for dd in out:
            dd.concentrated = False
        notes.append("The change is not notable, so no group is named as a driver."
                     if notable is False else "Without history, no group is named as a driver.")
    events = [a for a in (annotations if annotations is not None else load_annotations())
              if prev <= a["date"] < add_months(month, 1)]
    return DriverReport(
        metric=metric, month=month, prev_month=prev, value_a=va, value_b=vb,
        change=(vb - va) if None not in (va, vb) else None,
        change_pct=pct(va, vb) if None not in (va, vb) else None,
        threshold_pct=threshold, times_threshold=times,
        notable=notable,
        dimensions=out, annotations=events, result_ids=result_ids, notes=notes)
