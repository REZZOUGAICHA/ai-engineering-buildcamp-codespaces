"""Run a QueryPlan for a role: authorize, compile, guard, execute, protect.

This is the only function the agent will call to get data. The database is
opened read-only, with file and network access switched off and the settings
locked, so even SQL that slipped past the guard couldn't read files.
"""

import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
from pydantic import BaseModel, ConfigDict

from analyst import policy as access
from analyst import semantic, sql_guard
from analyst.compiler import compile_plan
from analyst.plan import QueryPlan

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "company.duckdb"
MAX_ROWS = 500
TIMEOUT_SECONDS = 10


class QueryTimeout(Exception):
    pass


class ResultSet(BaseModel):
    """One query result. Keeps its plan and SQL so every number can be traced back."""
    model_config = ConfigDict(frozen=True)
    result_id: str
    role: str
    plan: QueryPlan
    sql: str
    params: dict
    columns: list[str]
    rows: list[dict]
    notes: list[str]
    truncated: bool
    data_as_of: date


def connect(path=DEFAULT_DB):
    return duckdb.connect(str(path), read_only=True, config={
        "enable_external_access": False,  # no files, no network, no extensions
        "lock_configuration": True,       # and a query can't switch that back on
    })


def execute(con, sql, params, timeout=TIMEOUT_SECONDS):
    """Run SQL; interrupt it if it takes longer than timeout seconds."""
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql, params).fetchall()
    except duckdb.InterruptException as e:
        raise QueryTimeout(f"query took longer than {timeout} s and was stopped") from e
    finally:
        timer.cancel()


def plain(v):
    # Money comes back as Decimal; the rest of the app works with floats.
    return float(v) if isinstance(v, Decimal) else v


class Executor:
    def __init__(self, layer=None, policy=None, db_path=DEFAULT_DB, max_rows=MAX_ROWS,
                 timeout=TIMEOUT_SECONDS):
        self.layer = layer or semantic.load()
        self.policy = policy or access.load(self.layer)
        self.con = connect(db_path)
        semantic.check_against_db(self.layer, self.con)
        self.max_rows, self.timeout = max_rows, timeout
        meta = dict(self.con.execute("SELECT key, value FROM _meta").fetchall())
        self.data_from = date.fromisoformat(meta["data_from"])
        self.data_as_of = date.fromisoformat(meta["data_as_of"])

    def hidden_terms(self):
        """Hidden column names and every hidden value, for the verifier's leak check.
        Read directly (not through the guard): this is trusted code, not a user query."""
        if not hasattr(self, "_hidden"):
            terms = set(self.policy.hidden_columns)
            for col in self.policy.hidden_columns:
                table, column = col.split(".")
                terms |= {v for (v,) in self.con.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall() if v}
            self._hidden = terms
        return self._hidden

    def run(self, plan, role, result_id):
        grant = access.authorize(plan, role, self.policy, self.layer)
        query = compile_plan(plan, self.layer, grant)
        sql = sql_guard.check(query.sql, self.layer, self.policy.hidden_columns, self.max_rows)
        raw = execute(self.con, sql, query.params, self.timeout)
        truncated = len(raw) > self.max_rows
        rows = [dict(zip(query.columns, map(plain, r))) for r in raw[:self.max_rows]]
        rows, notes = access.protect_small_groups(rows, query, self.layer, grant.min_group_size)
        if truncated:
            notes.append(f"Only the first {self.max_rows} rows are shown.")
        return ResultSet(result_id=result_id, role=role, plan=plan, sql=sql,
                         params={k: str(v) for k, v in query.params.items()},
                         columns=[c for c in query.columns if c != "_n_entities"],
                         rows=rows, notes=notes, truncated=truncated, data_as_of=self.data_as_of)


class ResultStore:
    """The results of one request, by id (r1, r2, ...). The writer may only cite these."""

    def __init__(self, executor, role):
        self.executor, self.role, self.results = executor, role, {}

    def query(self, plan):
        result = self.executor.run(plan, self.role, f"r{len(self.results) + 1}")
        self.results[result.result_id] = result
        return result

    def get(self, result_id):
        return self.results.get(result_id)
