"""Measurements over time, as the Lab Trends screen reads them.

Three fields are nullable in a way that matters, and the interface has to be
able to render each as an absence rather than a zero:

    `value` is null when the result was words — 'positive for S. aureus'. Those
    readings are real and are listed, but they never go on a line.

    `status` is null when the document recorded no reference range. Without one
    there is no such thing as high or low, and supplying a verdict anyway would
    be diagnosis rather than reading.

    `direction` is null with fewer than two comparable readings. One result is
    a result, not a trend.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

Direction = str  # 'rising' | 'falling' | 'steady'
Status = str  # 'high' | 'low' | 'normal'


class LabReading(BaseModel):
    """One result."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    date: date | None
    value: float | None = Field(default=None, description="Null when the result was words.")
    text: str | None = Field(default=None, description="What was printed, when not a number.")
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    operator: str | None = Field(
        default=None, description="'<' or '>' — the lab declining to state a value exactly."
    )
    document_id: str | None = None
    page_no: int | None = None
    status: Status | None = Field(
        default=None, description="Null when no reference range was recorded."
    )
    plottable: bool = Field(description="Whether this point may be drawn on the line.")
    needs_review: bool = Field(description="Extracted but not yet confirmed by a person.")


class LabSeries(BaseModel):
    """Every reading of one test, oldest first."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    unit: str | None = None
    readings: list[LabReading]

    ref_low: float | None = None
    ref_high: float | None = None

    latest: LabReading | None = None
    direction: Direction | None = Field(
        default=None, description="Null with fewer than two comparable readings."
    )
    change: float | None = None

    source_names: list[str] = Field(
        default_factory=list,
        description=(
            "The spellings this series was assembled from. Shown when there is more "
            "than one, because a merge the reader cannot see is one they cannot correct."
        ),
    )
    chartable: bool = Field(description="At least two plottable readings.")
