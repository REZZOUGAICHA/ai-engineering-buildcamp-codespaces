import re
from dataclasses import dataclass

import pytest

from judge import assert_criteria
from sql_agent import sql_agent
from utils import collect_tools


# The data never changes, so we know the true answer. It comes from running
# SELECT COUNT(*) FROM trips WHERE passenger_count > 5 directly in DuckDB.
TRIPS_MORE_THAN_5_PASSENGERS = 22413


@pytest.mark.asyncio
async def test_count_trips_more_than_5_passengers():
    result = await sql_agent.run("How many trips had more than 5 passengers?")
    output = result.output

    assert isinstance(output.sql_query, str)
    assert output.sql_query.strip() != ""

    # the model may write the number as "22413" or "22,413"
    result_text = output.result_text.replace(",", "")
    assert str(TRIPS_MORE_THAN_5_PASSENGERS) in result_text


@pytest.mark.asyncio
async def test_schema_is_checked_before_running_sql():
    result = await sql_agent.run("What is the most common payment type?")

    tool_names = [call.tool_name for call in collect_tools(result)]
    print("tool calls:", tool_names)

    assert tool_names[0] == "get_schema"
    assert "run_sql" in tool_names


@pytest.mark.asyncio
async def test_highest_average_fare_hour_judge():
    question = "Which hour of the day has the highest average fare amount?"
    result = await sql_agent.run(question)
    print("agent output:", result.output)

    criteria = [
        "the SQL query correctly calculates average fare by hour of day",
        "the result identifies a specific hour as having the highest average fare",
        "the result includes the actual average fare amount",
    ]
    feedback = await assert_criteria(criteria, result, question)
    print("judge feedback:", feedback.feedback)


def numbers_in(text: str) -> list[float]:
    """All numbers in a text, e.g. "about $4.17 (4,169 trips)" -> [4.17, 4169.0]."""
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


@dataclass
class Case:
    question: str
    columns: list[str]          # must appear in the SQL query
    expected: list[str | float] # the result must contain one of these
    sql_pattern: str = ""       # optional regex the SQL must match


# Expected values come from running the SQL directly in DuckDB.
CASES = [
    Case(
        question="What is the average tip amount for credit card payments?",
        columns=["tip_amount", "payment_type"],
        expected=[4.17],  # payment_type = 1 is credit card in the NYC TLC data dictionary
    ),
    Case(
        question="Which pickup location (PULocationID) has the most trips?",
        columns=["pulocationid"],
        expected=[132.0],  # 145,240 trips; 161 is a close second with 143,471
    ),
    Case(
        question="What is the average fare for trips longer than 10 miles?",
        columns=["fare_amount", "trip_distance"],
        expected=[62.88],
    ),
    Case(
        question="How many trips had zero passengers recorded?",
        columns=["passenger_count"],
        expected=[31465.0],  # = 0; IS NULL would give 140,162
        sql_pattern=r"passenger_count\s*=\s*0",
    ),
    Case(
        question="What is the busiest day of the week for taxi trips?",
        columns=["tpep_pickup_datetime"],
        expected=["wednesday", 3.0],  # dayofweek(): 0 = Sunday, so Wednesday = 3
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.question[:40])
async def test_more_questions(case: Case):
    result = await sql_agent.run(case.question)
    output = result.output
    print("\nSQL:", output.sql_query, "\nresult:", output.result_text)

    # tools: schema first, then at least one query
    tool_names = [call.tool_name for call in collect_tools(result)]
    assert tool_names[0] == "get_schema"
    assert "run_sql" in tool_names

    # columns
    sql = output.sql_query.lower()
    for column in case.columns:
        assert column in sql, f"column {column} missing from SQL: {output.sql_query}"
    if case.sql_pattern:
        assert re.search(case.sql_pattern, sql), f"SQL doesn't match {case.sql_pattern}"

    # value: text values must appear, numbers must match within 0.01
    text = output.result_text.lower()
    found = numbers_in(text)
    assert any(
        (isinstance(e, str) and e in text)
        or (isinstance(e, float) and any(abs(n - e) < 0.01 for n in found))
        for e in case.expected
    ), f"none of {case.expected} in result: {output.result_text}"
