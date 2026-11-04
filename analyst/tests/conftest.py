from pathlib import Path

import duckdb
import pytest

from analyst import policy as access
from analyst import semantic
from analyst.compiler import compile_plan
from analyst.executor import connect

DB = Path(__file__).resolve().parents[1] / "data" / "company.duckdb"


@pytest.fixture(scope="session")
def layer():
    return semantic.load()


@pytest.fixture(scope="session")
def policy(layer):
    return access.load(layer)


@pytest.fixture(scope="session")
def compile_as(layer, policy):
    """compile_as(plan, role): authorize, then compile, the only allowed path."""
    def go(plan, role="ceo"):
        return compile_plan(plan, layer, access.authorize(plan, role, policy, layer))
    return go


@pytest.fixture(scope="session")
def truth(con):
    from ground_truth import GroundTruth
    return GroundTruth(con)


def run(con, query):
    """Execute a compiled query: {(period?, dimension values...): value}, no NULL values."""
    rows = con.execute(query.sql, query.params).fetchall()
    i = query.columns.index("value")
    return {tuple(r[:i]): r[i] for r in rows if r[i] is not None}


@pytest.fixture(scope="session")
def con():
    if not DB.exists():
        pytest.exit("No database. Run first: uv run python data/generate.py")
    # The same locked-down connection as the app. DuckDB also refuses two
    # connections to one file with different settings in the same process.
    con = connect(DB)
    yield con
    con.close()
