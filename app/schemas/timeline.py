"""Response shapes for the timeline screens.

Two views of the same rows. `/timeline` groups events under the visit they came
from, which is how the screen is laid out. `/timeline/review` is a flat list
ordered by confidence, which is how someone works through corrections — and it
carries its visit's date and provider inline, so each item can be judged
without loading the visit around it.

Everything the interface needs to render is precomputed: `display_text` is
built at ingestion, and `abnormal` is derived here rather than asking the
frontend to compare a value against two bounds.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

EventType = Literal[
    "medication", "lab_result", "vital_sign", "diagnosis", "allergy", "procedure", "note"
]
ReviewStatus = Literal["accepted", "needs_review", "confirmed", "corrected", "rejected"]
Severity = Literal["none", "info", "warning", "danger"]


class TimelineEventRead(BaseModel):
    """One fact on a card."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: EventType
    event_date: date | None
    title: str | None
    display_text: str | None

    # medications
    drug_raw: str | None = None
    drug_name: str | None = None
    rxcui: str | None = None
    atc_codes: list[str] | None = None
    strength_value: Decimal | None = None
    strength_unit: str | None = None
    dose_count: Decimal | None = None
    doses_per_day: Decimal | None = None
    frequency_label: str | None = None
    prn: bool = False
    start_date: date | None = None
    end_date: date | None = None

    # results and vitals
    analyte: str | None = None
    value_num: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    operator: str | None = None
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    flag: str | None = None
    computed_flag: str | None = None

    # diagnoses, allergies, procedures
    description: str | None = None
    substance: str | None = None
    reaction: str | None = None

    details: dict[str, Any] = {}

    # trust
    confidence: Decimal | None = None
    review_status: ReviewStatus
    review_reasons: list[str] | None = None
    risk: str
    source: str

    # where it came from, for "show me the original"
    document_id: UUID | None = None
    page_no: int | None = None
    source_span: str | None = None

    @computed_field
    @property
    def abnormal(self) -> bool:
        """Whether to render this value in red.

        Our own flag first: it is computed from the printed reference range,
        so it still works when the lab printed no flag of its own.
        """
        return (self.computed_flag or self.flag or "").upper() in ("H", "L", "A")


class TimelineVisitRead(BaseModel):
    """One card: a dated encounter with its facts."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID | None
    visit_date: date | None
    date_ambiguous: bool
    date_raw: str | None
    doc_type: str
    provider: str | None
    clinician_name: str | None
    clinician_specialty: str | None
    summary: str | None
    severity: Severity
    events: list[TimelineEventRead] = []


class TimelineCounts(BaseModel):
    """The figures across the top of the screen."""

    visits: int
    medications: int
    lab_values: int
    needs_review: int


class TimelineRead(BaseModel):
    counts: TimelineCounts
    visits: list[TimelineVisitRead]


class ReviewItemRead(TimelineEventRead):
    """A review-queue entry: the fact plus enough of its visit to judge it."""

    visit_id: UUID
    visit_date: date | None = None
    visit_doc_type: str | None = None
    visit_provider: str | None = None


class ReviewQueueRead(BaseModel):
    total: int
    high_risk: int
    items: list[ReviewItemRead]


class TimelineEventUpdate(BaseModel):
    """Fields a person may change on an extracted fact.

    Only what the document says is editable. Confidence, the source quote, the
    page number and the signals are records of how the fact was obtained — a
    correction changes the fact, not the history of how it was read.

    Every field is optional and `exclude_unset` is used when applying, so
    sending `{"strength_value": null}` clears a value while omitting the key
    leaves it alone. The two are different instructions and must stay so.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    event_date: date | None = None

    drug_raw: str | None = None
    drug_name: str | None = None
    strength_value: Decimal | None = None
    strength_unit: str | None = None
    dose_count: Decimal | None = None
    doses_per_day: Decimal | None = None
    frequency_label: str | None = None
    prn: bool | None = None
    duration_days: Decimal | None = None

    analyte: str | None = None
    value_num: Decimal | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    flag: str | None = None

    description: str | None = None
    substance: str | None = None
    reaction: str | None = None

    note: str | None = Field(
        default=None, description="Why you changed it. Kept with the row."
    )


class TimelineEventCreate(TimelineEventUpdate):
    """A fact the extraction missed entirely.

    A medicine the app failed to read is more dangerous than one it read
    wrongly, because nothing flags an absence — the safety checks simply skip
    a drug that was never recorded.
    """

    event_type: EventType
    visit_id: UUID | None = Field(
        default=None, description="Attach to an existing visit. Omit to start a new one."
    )
    visit_date: date | None = Field(
        default=None, description="Date for the new visit when visit_id is omitted."
    )


class RejectRequest(BaseModel):
    reason: str | None = Field(
        default=None, description="e.g. 'not my medicine', 'cancelled by the doctor'"
    )
