"""Access control: who can see which metrics, breakdowns and rows.

authorize() is the only way to get a Grant, and compile_plan() needs a Grant,
so no SQL can be built without passing these checks. Deny by default.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analyst.plan import Filter
from analyst.semantic import CONFIG_DIR, SemanticError

POLICY_FILE = CONFIG_DIR / "access_policy.yaml"
OTHER = "Other (small groups)"


class PolicyError(SemanticError):
    """The request is not allowed for this role. The message is safe to show."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Role(Strict):
    description: str
    metrics: list[str] | Literal["*"]
    dimensions: list[str] | Literal["*"]
    row_filter: Filter | None = None


class Policy(Strict):
    min_group_size: int = Field(ge=2)
    hidden_columns: list[str]
    roles: dict[str, Role]


class Grant(Strict):
    """Proof that a plan passed authorize(). Carries the rules the compiler must apply."""
    role: str
    row_filters: list[Filter]
    min_group_size: int


def load(layer, path=POLICY_FILE):
    try:
        policy = Policy(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except (ValidationError, TypeError) as e:
        raise SemanticError(f"invalid access policy: {e}") from e
    check_against_layer(policy, layer)
    return policy


def check_against_layer(policy, layer):
    for name, role in policy.roles.items():
        for m in [] if role.metrics == "*" else role.metrics:
            if m not in layer.metrics:
                raise SemanticError(f"role {name}: unknown metric {m!r}")
        for d in [] if role.dimensions == "*" else role.dimensions:
            if d not in layer.dimensions:
                raise SemanticError(f"role {name}: unknown dimension {d!r}")
        if role.row_filter and role.row_filter.dimension not in layer.dimensions:
            raise SemanticError(f"role {name}: unknown row filter dimension")
    for d_name, d in layer.dimensions.items():
        if d.column in policy.hidden_columns:
            raise SemanticError(f"dimension {d_name} exposes hidden column {d.column}")
    # Row filters and group sizes are both on customers, so every source must reach it.
    for s_name, s in layer.sources.items():
        if "customers" not in s.tables:
            raise SemanticError(f"source {s_name} does not join customers")


def role_rules(policy, role):
    if role not in policy.roles:
        raise PolicyError(f"Unknown role {role!r}: no data is available.")
    return policy.roles[role]


def allowed_metrics(policy, layer, role):
    rules = role_rules(policy, role)
    return list(layer.metrics) if rules.metrics == "*" else list(rules.metrics)


def allowed_dimensions(policy, layer, role):
    rules = role_rules(policy, role)
    return list(layer.dimensions) if rules.dimensions == "*" else list(rules.dimensions)


def authorize(plan, role, policy, layer):
    rules = role_rules(policy, role)
    metrics = allowed_metrics(policy, layer, role)
    if plan.metric not in metrics:
        raise PolicyError(f"The metric {plan.metric!r} is not available for your role. "
                          f"You can ask about: {', '.join(metrics)}.")
    dims = allowed_dimensions(policy, layer, role)
    for d in plan.dimensions + [f.dimension for f in plan.filters]:
        if d not in dims:
            raise PolicyError(f"Breaking down or filtering by {d!r} is not available for "
                              f"your role. You can use: {', '.join(dims) or 'none'}.")
    row = rules.row_filter
    if row:
        for f in plan.filters:
            if f.dimension == row.dimension and not set(f.values) <= set(row.values):
                raise PolicyError(f"Your role can only see {row.dimension} "
                                  f"{', '.join(row.values)}.")
    return Grant(role=role, row_filters=[row] if row else [],
                 min_group_size=policy.min_group_size)


def protect_small_groups(rows, query, layer, k):
    """Merge or blank result rows built from fewer than k customers.

    rows: dicts with the query's columns, including _n_entities.
    Returns (rows without _n_entities, notes for the "how computed" section).
    """
    plan = query.plan
    metric = layer.metric(plan.metric)
    dims = list(plan.dimensions)
    mergeable = not set(dims) & set(metric.non_additive_dimensions)
    notes, out = [], []

    periods = {}
    for r in rows:
        periods.setdefault(r.get("period"), []).append(dict(r))

    for group in periods.values():
        if not dims:
            for r in group:
                if r["_n_entities"] < k:
                    r["value"] = None
                    notes.append(f"A result built from fewer than {k} customers was hidden.")
            out += group
            continue
        small = [r for r in group if r["_n_entities"] < k]
        if not small:
            out += group
            continue
        visible = sorted((r for r in group if r["_n_entities"] >= k),
                         key=lambda r: (r["_n_entities"], [str(r[d]) for d in dims]))
        if not mergeable:
            out += visible
            notes.append(f"{len(small)} group(s) with fewer than {k} customers were hidden.")
            continue
        other = small
        # Merge the smallest visible group too, until Other can't be traced to
        # fewer than k customers (and so can't be recovered by subtraction).
        while sum(r["_n_entities"] for r in other) < k and visible:
            other.append(visible.pop(0))
        merged = combine(other, dims, metric)
        if merged["_n_entities"] < k:
            merged["value"] = None
        out += visible + [merged]
        notes.append(f"{len(other)} group(s) were combined into {OTHER!r} "
                     f"so that no result shows fewer than {k} customers.")

    cleaned = [{c: r[c] for c in query.columns if c != "_n_entities"} for r in out]
    return cleaned, sorted(set(notes))


def combine(rows, dims, metric):
    merged = dict(rows[0])
    for d in dims:
        merged[d] = OTHER
    merged["_n_entities"] = sum(r["_n_entities"] for r in rows)
    if metric.aggregation == "ratio":
        merged["numerator"] = sum(r["numerator"] for r in rows)
        merged["denominator"] = sum(r["denominator"] for r in rows)
        merged["value"] = (merged["numerator"] / merged["denominator"]
                           if merged["denominator"] else None)
    else:
        merged["value"] = sum(r["value"] for r in rows)
    return merged
