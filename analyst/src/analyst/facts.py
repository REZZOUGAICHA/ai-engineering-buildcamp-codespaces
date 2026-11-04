"""Facts: every number the writer may cite, computed and formatted by code.

A ResultSet becomes named fields, e.g. r1.value, r1.change_pct, r1.value[EU].
The writer only places {{r1.change_pct}}-style placeholders; render() fills them.
So the model never types, rounds or computes a number.
"""

import re
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\{\{\s*(r\d+)\.([a-z_]+)(?:\[([^\]]+)\])?\s*\}\}")


@dataclass(frozen=True)
class Fact:
    value: object          # number, text, or None (no data)
    kind: str              # amount | change | change_pct | label | month | count
    unit: str | None = None

    @property
    def sign(self):
        if self.kind in ("change", "change_pct") and self.value is not None:
            return (self.value > 0) - (self.value < 0)
        return None


def fmt_amount(v, unit):
    if v is None:
        return "no data"
    if unit == "percent":
        return f"{v * 100:.2f}%"
    if unit == "USD":
        return f"${v:,.2f}" if abs(v) < 1000 else f"${v:,.0f}"
    return f"{v:,.0f}"


def fmt(f, absolute=False):
    """Format a fact for the reader. absolute=True drops the sign (the words carry it)."""
    v = f.value
    if v is None:
        return "no data"
    if f.kind in ("label", "month"):
        return str(v)
    if f.kind == "count":
        return f"{v:,}"
    if absolute:
        v = abs(v)
    sign = "+" if v > 0 and not absolute else ""
    if f.kind == "change_pct":
        return f"{sign}{v * 100:.2f}%"
    if f.kind == "change":
        if f.unit == "percent":  # a change of a rate is in percentage points
            return f"{sign}{v * 100:.2f} percentage points"
        return sign + fmt_amount(v, f.unit) if v >= 0 else "-" + fmt_amount(-v, f.unit)
    return fmt_amount(v, f.unit)


def month_label(d):
    return d.strftime("%B %Y")


def series_facts(points, unit, suffix=""):
    """Facts for one series of (period, value); period is None for grain total."""
    out = {}
    points = [(p, v) for p, v in points if v is not None]
    if not points:
        return {f"value{suffix}": Fact(None, "amount", unit)}
    if points[0][0] is None:  # grain total: one value
        return {f"value{suffix}": Fact(points[0][1], "amount", unit)}
    points.sort(key=lambda pv: pv[0])
    (p0, v0), (p1, v1) = points[0], points[-1]
    out[f"first_month{suffix}"] = Fact(month_label(p0), "month")
    out[f"last_month{suffix}"] = Fact(month_label(p1), "month")
    out[f"first{suffix}"] = Fact(v0, "amount", unit)
    out[f"last{suffix}"] = Fact(v1, "amount", unit)
    out[f"months{suffix}"] = Fact(len(points), "count")
    if len(points) > 1:
        out[f"change{suffix}"] = Fact(v1 - v0, "change", unit)
        if v0:
            out[f"change_pct{suffix}"] = Fact((v1 - v0) / abs(v0), "change_pct", unit)
        lo = min(points, key=lambda pv: pv[1])
        hi = max(points, key=lambda pv: pv[1])
        out[f"min{suffix}"] = Fact(lo[1], "amount", unit)
        out[f"min_month{suffix}"] = Fact(month_label(lo[0]), "month")
        out[f"max{suffix}"] = Fact(hi[1], "amount", unit)
        out[f"max_month{suffix}"] = Fact(month_label(hi[0]), "month")
    return out


def facts(result, layer):
    """{field: Fact} for one ResultSet. Group fields look like value[EU] or value[EU / SMB]."""
    unit = layer.metrics[result.plan.metric].unit
    dims = result.plan.dimensions
    groups = {}
    for row in result.rows:
        key = " / ".join(str(row[d]) for d in dims)
        groups.setdefault(key, []).append((row.get("period"), row["value"]))
    if not dims:
        return series_facts(groups.get("", []), unit)
    out = {}
    for key, points in groups.items():
        out.update(series_facts(points, unit, f"[{key}]"))
    # Rank groups by their latest value, so the writer can name the biggest/smallest.
    latest = {k: max((pv for pv in pts if pv[1] is not None), key=lambda pv: pv[0] or 0,
                     default=(None, None))[1] for k, pts in groups.items()}
    latest = {k: v for k, v in latest.items() if v is not None}
    out["groups"] = Fact(len(groups), "count")
    if latest:
        big, small = max(latest, key=latest.get), min(latest, key=latest.get)
        out["largest_group"] = Fact(big, "label")
        out["largest_value"] = Fact(latest[big], "amount", unit)
        out["smallest_group"] = Fact(small, "label")
        out["smallest_value"] = Fact(latest[small], "amount", unit)
    return out


def all_facts(results, layer):
    """{(result_id, field): Fact} for a request's results."""
    return {(rid, field): f for rid, r in results.items() for field, f in facts(r, layer).items()}


def key_of(match):
    rid, name, group = match.group(1), match.group(2), match.group(3)
    return rid, f"{name}[{group}]" if group is not None else name


def render(text, table):
    """Fill every placeholder. A *_abs field prints without its sign."""
    def fill(m):
        rid, field = key_of(m)
        absolute = field.split("[")[0].endswith("_abs")
        base = re.sub(r"_abs(?=\[|$)", "", field)
        return fmt(table[(rid, base)], absolute=absolute)
    return PLACEHOLDER.sub(fill, text)
