"""Turn a QueryPlan into SQL, using only definitions from the semantic layer.

Table names, columns and formulas come from metrics.yaml (trusted config).
Values from the plan (dates, filter values) are never pasted into the SQL
text: they are passed as query parameters, so they can't change the query.
"""

from pydantic import BaseModel, ConfigDict

from analyst.plan import Filter, QueryPlan  # noqa: F401  (QueryPlan re-exported)
from analyst.policy import Grant
from analyst.semantic import SemanticError


class CompileError(SemanticError):
    pass


class CompiledQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    sql: str
    params: dict
    columns: list[str]  # output columns, in order
    plan: QueryPlan


def compile_plan(plan, layer, grant):
    """grant comes from policy.authorize(): there is no way to compile without one."""
    if not isinstance(grant, Grant):
        raise CompileError("compile_plan needs a Grant from policy.authorize()")
    metric = layer.metric(plan.metric)
    source = layer.sources[metric.source]

    for d in plan.dimensions + [f.dimension for f in plan.filters]:
        if d not in metric.allowed_dimensions:
            raise CompileError(f"metric {metric.name} can't be broken down or filtered "
                               f"by {d!r}; allowed: {metric.allowed_dimensions}")

    select, group, columns = [], [], []
    if plan.grain == "month":
        select.append(f"date_trunc('month', {metric.time_column})::DATE AS period")
        columns.append("period")
    for d in plan.dimensions:
        select.append(f"{layer.dimensions[d].column} AS {d}")
        columns.append(d)
    group = list(range(1, len(select) + 1))

    if metric.aggregation == "ratio":
        select += [f"CAST({metric.numerator} AS DOUBLE) / NULLIF({metric.denominator}, 0) AS value",
                   f"{metric.numerator} AS numerator",
                   f"{metric.denominator} AS denominator"]
        columns += ["value", "numerator", "denominator"]
    else:
        select.append(f"{metric.expression} AS value")
        columns.append("value")
    # How many customers each row is built from, for the minimum group size rule.
    # Removed by policy.protect_small_groups() before anything is shown.
    select.append("COUNT(DISTINCT customers.customer_id) AS _n_entities")
    columns.append("_n_entities")

    params = {"start": plan.start, "end": plan.end}
    where = [f"{metric.time_column} >= $start", f"{metric.time_column} < $end"]
    where += [f"({f})" for f in (source.filters, metric.filters) if f]
    # The role's row filters (prefix r) are added here, inside the compiler,
    # the same way as the plan's own filters (prefix f).
    for prefix, filters in (("r", grant.row_filters), ("f", plan.filters)):
        for i, f in enumerate(filters):
            names = []
            for j, v in enumerate(f.values):
                params[f"{prefix}{i}_{j}"] = v
                names.append(f"${prefix}{i}_{j}")
            where.append(f"{layer.dimensions[f.dimension].column} IN ({', '.join(names)})")

    lines = [f"SELECT {', '.join(select)}", f"FROM {source.table}"]
    lines += [f"JOIN {j.table} ON {j.on}" for j in source.joins]
    lines.append("WHERE " + "\n  AND ".join(where))
    if group:
        positions = ", ".join(map(str, group))
        lines += [f"GROUP BY {positions}", f"ORDER BY {positions}"]
    return CompiledQuery(sql="\n".join(lines), params=params, columns=columns, plan=plan)
