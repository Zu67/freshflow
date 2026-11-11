"""QuerySpec — the only thing the planner is allowed to produce.

The planner never writes SQL. It fills in this structure, which is validated
against the semantic layer before anything runs.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .semantic import SemanticModel

Op = Literal["in", "not_in", "eq", "contains"]


class Filter(BaseModel):
    field: str
    op: Op = "in"
    values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sane(self):
        if not self.values:
            raise ValueError("a filter needs at least one value")
        if self.op == "eq" and len(self.values) != 1:
            raise ValueError("an eq filter needs exactly one value")
        return self


class TimeRange(BaseModel):
    start: date
    end: date
    label: str = ""


class QuerySpec(BaseModel):
    intent: Literal["aggregate", "ranking", "trend", "comparison", "breakdown"] = "aggregate"
    measures: list[str]
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time: TimeRange
    compare_time: TimeRange | None = None
    scope: Literal["fresh", "all", "explicit"] = "fresh"
    order_by: str | None = None
    order_desc: bool = True
    limit: int = 20

    @field_validator("limit", mode="before")
    @classmethod
    def _default_limit(cls, v):
        """The planner sometimes emits null for a field it has no opinion on."""
        return 20 if v is None else v

    @field_validator("dimensions", "filters", "measures", mode="before")
    @classmethod
    def _default_list(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _sane(self):
        if not self.measures:
            raise ValueError("at least one measure is required")
        if self.limit < 1 or self.limit > 500:
            raise ValueError("limit must be between 1 and 500")
        return self

    def validate_against(self, model: SemanticModel) -> None:
        """Reject anything the semantic layer does not define."""
        for m in self.measures:
            model.measure(m)
        for d in self.dimensions:
            model.dimension(d)
        for f in self.filters:
            model.dimension(f.field)
        if self.order_by and self.order_by not in self.measures and self.order_by not in self.dimensions:
            raise ValueError(f"order_by '{self.order_by}' is not a selected measure or dimension")
        for tr in (self.time, self.compare_time):
            if tr and tr.start > tr.end:
                raise ValueError("time range starts after it ends")


class PlannerResult(BaseModel):
    """What the planner returns: either a spec, or a refusal with a reason."""

    answerable: bool
    spec: QuerySpec | None = None
    reason: str = ""
    restatement: str = ""
    notes: list[str] = Field(default_factory=list)
