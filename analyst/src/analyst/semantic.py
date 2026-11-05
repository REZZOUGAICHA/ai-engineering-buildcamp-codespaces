"""Load and validate the semantic layer (config/metrics.yaml).

Any mistake in the config raises SemanticError when it is loaded, so a bad
definition can never quietly produce wrong numbers later.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
METRICS_FILE = CONFIG_DIR / "metrics.yaml"


class SemanticError(ValueError):
    pass


# Words that don't change which metric is meant ("total revenue" is revenue).
# Kept short on purpose: find() must never match a different metric that only
# contains a known word ("net revenue retention" is not revenue).
FILLER_WORDS = {"total", "overall", "our", "the", "company", "company's", "all"}


class Strict(BaseModel):
    # Unknown keys are errors: a typo like "synonym:" must not be ignored.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Dimension(Strict):
    column: str
    description: str
    # The allowed values. A filter like region = "Europe" is caught as an
    # error instead of silently returning nothing.
    values: list[str]

    @property
    def table(self):
        return self.column.split(".")[0]


class Join(Strict):
    table: str
    on: str


class Source(Strict):
    table: str
    joins: list[Join] = []
    filters: str | None = None

    @property
    def tables(self):
        return {self.table} | {j.table for j in self.joins}


class Metric(Strict):
    name: str
    description: str
    owner: str
    source: str
    aggregation: Literal["additive", "distinct_count", "ratio"]
    expression: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    filters: str | None = None
    time_column: str
    allowed_dimensions: list[str]
    non_additive_dimensions: list[str] = []
    synonyms: list[str] = []
    unit: str
    direction: Literal["up_is_good", "down_is_good"]
    caveats: str | None = None

    @model_validator(mode="after")
    def check_formula(self):
        if self.aggregation == "ratio":
            if not (self.numerator and self.denominator) or self.expression:
                raise ValueError("a ratio metric needs numerator and denominator, and no expression")
        elif not self.expression or self.numerator or self.denominator:
            raise ValueError(f"an {self.aggregation} metric needs expression, "
                             "and no numerator or denominator")
        return self


class SemanticLayer(Strict):
    dimensions: dict[str, Dimension]
    sources: dict[str, Source]
    metrics: dict[str, Metric]

    @model_validator(mode="after")
    def check_references(self):
        for name, m in self.metrics.items():
            if m.source not in self.sources:
                raise ValueError(f"metric {name}: unknown source {m.source!r}")
            source = self.sources[m.source]
            if m.time_column.split(".")[0] not in source.tables:
                raise ValueError(f"metric {name}: time column {m.time_column} "
                                 f"is not in source {m.source}")
            for d in m.allowed_dimensions + m.non_additive_dimensions:
                if d not in self.dimensions:
                    raise ValueError(f"metric {name}: unknown dimension {d!r}")
                if self.dimensions[d].table not in source.tables:
                    raise ValueError(f"metric {name}: dimension {d} needs table "
                                     f"{self.dimensions[d].table}, not joined in source {m.source}")

        # Each word must point to exactly one metric, or the agent can't tell which is meant.
        seen = {}
        for name, m in self.metrics.items():
            for word in [name, *m.synonyms]:
                key = word.strip().lower()
                if key in seen and seen[key] != name:
                    raise ValueError(f"{word!r} is used by both {seen[key]} and {name}")
                seen[key] = name
        return self

    def metric(self, name):
        if name not in self.metrics:
            raise SemanticError(f"unknown metric {name!r}")
        return self.metrics[name]

    def find(self, word):
        """Metric name for a metric name or synonym (any case), else None.
        Filler words ("total", "our", ...) are ignored, and "active users" means
        active_users; nothing looser."""
        key = " ".join(w for w in word.lower().split() if w not in FILLER_WORDS)
        if not key:
            return None
        for name, m in self.metrics.items():
            if key.replace(" ", "_") == name or key in (s.lower() for s in m.synonyms):
                return name
        return None


def parse(raw):
    """Build a SemanticLayer from the parsed YAML dict."""
    try:
        metrics = {name: {"name": name, **body} for name, body in (raw.get("metrics") or {}).items()}
        return SemanticLayer(**{**raw, "metrics": metrics})
    except (ValidationError, TypeError, AttributeError) as e:
        raise SemanticError(f"invalid semantic layer: {e}") from e


def load(path=METRICS_FILE):
    return parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def check_against_db(layer, con):
    """Every table and column the config names must exist in the database."""
    columns = {(t, c) for t, c in con.execute(
        "SELECT table_name, column_name FROM duckdb_columns()").fetchall()}
    tables = {t for t, _ in columns}
    for s_name, s in layer.sources.items():
        for t in s.tables:
            if t not in tables:
                raise SemanticError(f"source {s_name}: table {t!r} not in database")
    for d_name, d in layer.dimensions.items():
        if tuple(d.column.split(".")) not in columns:
            raise SemanticError(f"dimension {d_name}: column {d.column!r} not in database")
    for d_name, d in layer.dimensions.items():
        table, column = d.column.split(".")
        found = {v for (v,) in con.execute(f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall()}
        if found != set(d.values):
            raise SemanticError(f"dimension {d_name}: config values {sorted(d.values)} "
                                f"differ from database values {sorted(found, key=str)}")
    for m_name, m in layer.metrics.items():
        if tuple(m.time_column.split(".")) not in columns:
            raise SemanticError(f"metric {m_name}: time column {m.time_column!r} not in database")
