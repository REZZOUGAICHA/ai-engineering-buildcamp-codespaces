import os
import urllib.request

import duckdb

DATA_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet"
PARQUET_FILE = "yellow_tripdata_2024-01.parquet"

con = duckdb.connect("taxi.db")


def setup_database():
    """Download the parquet file and load it into DuckDB."""
    if not os.path.exists(PARQUET_FILE):
        print(f"Downloading {DATA_URL}...")
        urllib.request.urlretrieve(DATA_URL, PARQUET_FILE)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS trips AS
        SELECT * FROM '{PARQUET_FILE}'
    """)
    count = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    print(f"Loaded {count} rows")
    return count


MAX_ROWS = 50


class SQLTools:
    """
    Tools for exploring and querying the NYC taxi trips table in DuckDB.
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection = con) -> None:
        """
        Initialize the SQLTools instance.

        Args:
            connection: DuckDB connection that has the `trips` table.
        """
        self.con = connection

    def get_schema(self) -> str:
        """
        Get the columns of the `trips` table and their types.

        Returns:
            str: One line per column, as "column_name: TYPE".
        """
        rows = self.con.execute("DESCRIBE trips").fetchall()
        # DESCRIBE returns (column_name, column_type, null, key, default, extra)
        return "\n".join(f"{name}: {col_type}" for name, col_type, *_ in rows)

    def run_sql(self, query: str) -> str:
        """
        Run a SQL query on the `trips` table and return the result as text.

        Args:
            query (str): The DuckDB SQL query to run.

        Returns:
            str: Column headers and up to 50 data rows, tab-separated,
                or an error message if the query failed.
        """
        try:
            cursor = self.con.execute(query)
        except duckdb.Error as e:
            # return the error so the agent can fix its query and try again
            return f"Error: {e}"

        if cursor.description is None:
            return "Query ran, no rows returned."

        headers = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(MAX_ROWS)

        lines = ["\t".join(headers)]
        lines += ["\t".join(str(value) for value in row) for row in rows]
        return "\n".join(lines)
