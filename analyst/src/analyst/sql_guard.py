"""Check SQL before it runs (defense in depth: the compiler writes it, this re-checks it).

Rules: exactly one SELECT; only tables from the semantic layer; every column
named with its table; no *, no hidden columns; only allowlisted functions;
a LIMIT is always added. The SQL that runs is rebuilt from the checked tree,
so what was checked is exactly what runs.
"""

import sqlglot
from sqlglot import exp

from analyst.semantic import SemanticError

# Every function type the compiler produces (measured on all 8 metrics with
# sqlglot 30.29.0), plus the other plain aggregates. Anything else, including
# read_csv, getenv or table functions, is rejected. An allowlist can't miss a
# new dangerous function the way a blocklist can.
ALLOWED_FUNCTIONS = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Cast, exp.Nullif,
                     exp.TimestampTrunc, exp.DateTrunc, exp.And, exp.Or)


class SqlGuardError(SemanticError):
    pass


def allowed_tables(layer):
    return set().union(*(s.tables for s in layer.sources.values()))


def check(sql, layer, hidden_columns, max_rows):
    """Return safe SQL (rebuilt, with LIMIT max_rows + 1) or raise SqlGuardError."""
    try:
        statements = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise SqlGuardError(f"SQL could not be parsed: {e}") from e
    if len(statements) != 1 or statements[0] is None:
        raise SqlGuardError("exactly one SQL statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SqlGuardError(f"only SELECT is allowed, got {type(tree).__name__}")

    tables = allowed_tables(layer)
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier) or t.db or t.catalog or t.name not in tables:
            raise SqlGuardError(f"table not allowed: {t.sql(dialect='duckdb')}")
    for star in tree.find_all(exp.Star):
        # COUNT(*) reads no column, so it can't expose one. Any other * can.
        if not isinstance(star.parent, exp.Count):
            raise SqlGuardError("* is not allowed (except in COUNT(*))")
    hidden = {c.lower() for c in hidden_columns}
    for c in tree.find_all(exp.Column):
        if not c.table:
            raise SqlGuardError(f"column {c.name} must be named with its table")
        if f"{c.table}.{c.name}".lower() in hidden:
            raise SqlGuardError(f"hidden column not allowed: {c.table}.{c.name}")
        if c.table not in tables:
            raise SqlGuardError(f"unknown table in column {c.sql(dialect='duckdb')}")
    for f in tree.find_all(exp.Func):
        if not isinstance(f, ALLOWED_FUNCTIONS):
            raise SqlGuardError(f"function not allowed: {f.sql(dialect='duckdb')[:40]}")

    # One extra row tells the executor the result was cut off.
    return tree.limit(max_rows + 1).sql(dialect="duckdb")
