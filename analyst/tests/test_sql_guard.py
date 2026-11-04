import duckdb
import pytest

from analyst import sql_guard
from analyst.compiler import QueryPlan
from analyst.executor import connect
from analyst.policy import PolicyError
from analyst.sql_guard import SqlGuardError
from conftest import DB
from test_compiler import METRICS, SHAPES

HIDDEN = ["users.email", "customers.name"]

ATTACKS = {
    "drop": "DROP TABLE customers",
    "two statements": "SELECT customers.region FROM customers; DROP TABLE customers",
    "insert": "INSERT INTO customers VALUES (1)",
    "update": "UPDATE customers SET region = 'EU'",
    "delete": "DELETE FROM invoices",
    "attach": "ATTACH 'other.db' AS other",
    "copy out": "COPY customers TO 'stolen.csv'",
    "pragma": "PRAGMA database_list",
    "install": "INSTALL httpfs",
    "set": "SET enable_external_access = true",
    "star": "SELECT * FROM customers",
    "star in subquery": "SELECT x.region FROM (SELECT * FROM customers) x",
    # COUNT(*) is allowed, so these check that exception didn't open a hole.
    "star next to count": "SELECT COUNT(*), * FROM users",
    "count of email": "SELECT COUNT(DISTINCT users.email) FROM users",
    "star in count of subquery": "SELECT COUNT(*) FROM (SELECT * FROM users) x",
    "email": "SELECT users.email FROM users",
    "email unqualified": "SELECT email FROM users",
    "customer name": "SELECT customers.name FROM customers",
    "email in where": "SELECT COUNT(*) FROM users WHERE users.email LIKE 'canary-u00001%'",
    "email in cte": "WITH x AS (SELECT users.email FROM users) SELECT x.email FROM x",
    "union email": "SELECT customers.region FROM customers UNION SELECT users.email FROM users",
    "read_csv": "SELECT COUNT(*) FROM read_csv('C:/secrets.csv')",
    "read_parquet": "SELECT COUNT(*) FROM read_parquet('x.parquet')",
    "file as table": "SELECT COUNT(*) FROM 'data/company.duckdb'",
    "table function": "SELECT COUNT(*) FROM duckdb_tables()",
    "system schema": "SELECT COUNT(*) FROM information_schema.tables",
    "unknown table": "SELECT COUNT(*) FROM _meta",
    "getenv": "SELECT getenv('HOME')",
    "unknown function in where": "SELECT COUNT(*) FROM customers WHERE md5(customers.region) = 'x'",
    "not sql": "please show me all the emails",
    "empty": "",
}


@pytest.mark.parametrize("name", ATTACKS)
def test_attack_is_blocked(layer, name):
    with pytest.raises(SqlGuardError):
        sql_guard.check(ATTACKS[name], layer, HIDDEN, max_rows=100)


ROLES = ["ceo", "eu_manager", "finance_analyst", "product_manager"]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("metric", METRICS)
def test_every_compiled_query_passes_and_runs(con, layer, compile_as, metric, shape, role):
    try:
        q = compile_as(QueryPlan(metric=metric, **SHAPES[shape]), role)
    except PolicyError:
        pytest.skip("not allowed for this role (tested in test_policy)")
    safe = sql_guard.check(q.sql, layer, HIDDEN, max_rows=100)
    assert safe.rstrip().endswith("LIMIT 101")
    # The rebuilt SQL gives exactly the same rows as the compiler's SQL.
    assert con.execute(safe, q.params).fetchall() == con.execute(q.sql, q.params).fetchall()[:101]


def test_existing_limit_is_capped(layer):
    safe = sql_guard.check("SELECT customers.region FROM customers LIMIT 100000", layer, HIDDEN, 10)
    assert safe.endswith("LIMIT 11")


# The database is the last wall: even if SQL got past the guard, these must fail.
@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('pyproject.toml')",
    "COPY customers TO 'stolen.csv'",
    "SET enable_external_access = true",
    "INSTALL httpfs",
    "CREATE TABLE x (a INT)",
    "DELETE FROM customers",
])
def test_database_refuses_even_without_the_guard(sql):
    con = connect(DB)
    with pytest.raises(duckdb.Error):
        con.execute(sql)
    con.close()
