"""The QueryPlan: a strict description of what to compute. It never contains SQL."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dimension: str
    values: list[str] = Field(min_length=1)


class QueryPlan(BaseModel):
    """What to compute. Filled by the planner LLM later; never contains SQL."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric: str
    start: date  # first day included
    end: date    # first day NOT included, so March 2026 is 2026-03-01 to 2026-04-01
    grain: Literal["total", "month"] = "total"
    dimensions: list[str] = []
    filters: list[Filter] = []

    @model_validator(mode="after")
    def check_period(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("a dimension is listed twice")
        return self
