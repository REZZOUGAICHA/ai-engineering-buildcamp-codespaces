import copy

import duckdb
import pytest
import yaml

from analyst import semantic
from analyst.semantic import SemanticError

RAW = yaml.safe_load(semantic.METRICS_FILE.read_text(encoding="utf-8"))


def broken(change):
    raw = copy.deepcopy(RAW)
    change(raw)
    return raw


def test_real_config_loads_and_matches_db(layer, con):
    assert len(layer.metrics) == 8
    semantic.check_against_db(layer, con)


def test_every_aggregation_type_is_used(layer):
    assert {m.aggregation for m in layer.metrics.values()} == {"additive", "distinct_count", "ratio"}


@pytest.mark.parametrize("word, metric", [
    ("revenue", "revenue"), ("Sales", "revenue"), ("  MAU ", "active_users"),
    ("attrition", "churn_rate"), ("engagement", None), ("", None),
])
def test_find_by_name_or_synonym(layer, word, metric):
    assert layer.find(word) == metric


BROKEN_CONFIGS = {
    "unknown source": lambda r: r["metrics"]["revenue"].update(source="payments"),
    "unknown dimension": lambda r: r["metrics"]["revenue"]["allowed_dimensions"].append("country"),
    "dimension not joined": lambda r: r["sources"]["invoices"].update(joins=[]),
    "time column not in source": lambda r: r["metrics"]["revenue"].update(time_column="events.event_ts"),
    "duplicate synonym": lambda r: r["metrics"]["arpu"]["synonyms"].append("sales"),
    "synonym equals another metric's name": lambda r: r["metrics"]["arpu"]["synonyms"].append("Revenue"),
    "ratio with expression": lambda r: r["metrics"]["arpu"].update(expression="SUM(1)"),
    "ratio without denominator": lambda r: r["metrics"]["arpu"].pop("denominator"),
    "additive without expression": lambda r: r["metrics"]["revenue"].pop("expression"),
    "additive with numerator": lambda r: r["metrics"]["revenue"].update(numerator="SUM(1)"),
    "unknown aggregation": lambda r: r["metrics"]["revenue"].update(aggregation="median"),
    "unknown direction": lambda r: r["metrics"]["revenue"].update(direction="up"),
    "typo in a key": lambda r: r["metrics"]["revenue"].update(synonym=["x"]),
    "missing owner": lambda r: r["metrics"]["revenue"].pop("owner"),
}


@pytest.mark.parametrize("name", BROKEN_CONFIGS)
def test_broken_config_fails_loudly(name):
    with pytest.raises(SemanticError):
        semantic.parse(broken(BROKEN_CONFIGS[name]))


def test_unquoted_on_in_yaml_fails_loudly():
    # YAML reads a bare `on` as the boolean True, so the join would lose its condition.
    text = semantic.METRICS_FILE.read_text(encoding="utf-8").replace('"on":', "on:")
    assert True in yaml.safe_load(text)["sources"]["invoices"]["joins"][0]
    with pytest.raises(SemanticError):
        semantic.parse(yaml.safe_load(text))


def test_column_missing_from_db_fails_loudly(con):
    layer = semantic.parse(broken(lambda r: r["dimensions"]["region"].update(column="customers.country")))
    with pytest.raises(SemanticError, match="customers.country"):
        semantic.check_against_db(layer, con)


@pytest.mark.parametrize("values", [["EU", "NA", "APAC"], ["EU", "NA", "APAC", "LATAM", "Europe"]])
def test_dimension_values_must_match_db(con, values):
    # A missing value would hide real data; an extra one would let a filter
    # like region = "Europe" pass and quietly return nothing.
    layer = semantic.parse(broken(lambda r: r["dimensions"]["region"].update(values=values)))
    with pytest.raises(SemanticError, match="differ from database"):
        semantic.check_against_db(layer, con)
