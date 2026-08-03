"""Response shapes for the cross-check results.

`coverage` is not decoration. A list of zero findings means one of two very
different things — everything was checked and nothing was wrong, or nothing
could be checked — and the interface has to be able to tell them apart. Sending
the numbers with every response makes it impossible to render a reassuring
empty state by accident.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FindingType = Literal[
    "interaction",
    "duplicate_therapy",
    "allergy_conflict",
    "dosage_conflict",
    "lab_trend",
    "critical_value",
]
FindingSeverity = Literal["info", "warning", "danger"]


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    finding_type: FindingType
    severity: FindingSeverity
    title: str
    detail: str | None
    detail_source: str | None
    evidence: dict[str, Any] = {}
    event_ids: list[UUID] = []
    visit_ids: list[UUID] = []
    status: str
    dismissed_reason: str | None = None
    first_seen_at: datetime

    confidence: float | None = Field(
        default=None,
        description=(
            "How well the finding's underlying facts were read, 0-1: the weakest "
            "of the extracted rows it rests on. Null when none of them carry a "
            "score. Not a probability that the finding is real — the rule that "
            "produced it either matched or it did not."
        ),
    )


class CheckCoverage(BaseModel):
    """How much of the record the checks could actually see."""

    medicines_total: int
    medicines_checked: int
    unchecked: list[str] = []
    rules_available: int

    @property
    def complete(self) -> bool:
        return self.medicines_total == self.medicines_checked


class FindingListRead(BaseModel):
    coverage: CheckCoverage
    counts: dict[str, int]
    findings: list[FindingRead]


class DismissRequest(BaseModel):
    reason: str | None = Field(
        default=None, description="e.g. 'my doctor knows about this'"
    )
