"""Request and response shapes for asking questions about the record.

`confidence` is nullable on purpose, and null is not a failure. An answer that
correctly reports finding nothing — 'no record of a physiotherapy note' — rests
on no extracted fact, so it has no score to give. Sending 0.0 instead would
render as a red bar next to a perfectly good answer, and sending 1.0 would
claim certainty about an absence. The interface has to be able to show 'not
scored', so the field has to be able to say it.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ConfidenceBand = Literal["high", "medium", "low", "none"]


class Turn(BaseModel):
    """One earlier message in the same conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)

    # Sent by the client because nothing is persisted yet. It is replayed to
    # the model so a follow-up like 'and the one before that?' has something to
    # resolve against — but it is never treated as evidence. Every fact in a
    # new answer still has to come back from a tool on this turn, so a client
    # that fabricates an assistant turn cannot put a claim into an answer.
    history: list[Turn] = Field(default_factory=list, max_length=20)


class Citation(BaseModel):
    """One extracted fact the answer stands on."""

    tool: str
    event_id: UUID | None = None
    document_id: UUID | None = None
    page_no: int | None = None
    date: str | None = None
    modality: str | None = None
    confidence: float | None = None
    text: str | None = None


class AskResponse(BaseModel):
    answer: str

    confidence: float | None = Field(
        default=None,
        description="The weakest supporting fact. Null when the answer rests on none.",
    )
    band: ConfidenceBand = "none"
    basis: str | None = Field(default=None, description="Why the score is what it is.")

    citations: list[Citation] = []
    grounded: bool = True
    unsupported: list[str] | None = Field(
        default=None, description="Claims the verifier could not trace to evidence."
    )

    tool_calls: list[str] = Field(
        default_factory=list, description="What was consulted, in order. For the trace view."
    )
    disclaimer: str


def from_agent(result: dict[str, Any]) -> AskResponse:
    """Turn what `ask` returns into what the API promises."""
    return AskResponse(
        answer=result.get("answer") or "",
        confidence=result.get("confidence"),
        band=result.get("band") or "none",
        basis=result.get("basis"),
        citations=[Citation(**item) for item in result.get("citations") or []],
        grounded=result.get("grounded", True),
        unsupported=result.get("unsupported"),
        tool_calls=result.get("tool_calls") or [],
        disclaimer=result["disclaimer"],
    )
