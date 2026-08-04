"""Request and response shapes for asking questions about the record.

`confidence` is nullable on purpose, and null is not a failure. An answer that
correctly reports finding nothing — 'no record of a physiotherapy note' — rests
on no extracted fact, so it has no score to give. Sending 0.0 instead would
render as a red bar next to a perfectly good answer, and sending 1.0 would
claim certainty about an absence. The interface has to be able to show 'not
scored', so the field has to be able to say it.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.rag.agent import DISCLAIMER

ConfidenceBand = Literal["high", "medium", "low", "none"]
Role = Literal["user", "assistant"]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)

    # Which conversation this belongs to. Null starts a new one.
    #
    # The client used to send the earlier turns themselves. It no longer can,
    # and that is the point: replayed history is context the model reasons
    # from, so a client able to write it could put a sentence in the
    # assistant's mouth and have it treated as something already established.
    # The server reads the conversation from its own table instead. Ownership
    # is checked against the patient in the token, never against this field.
    session_id: UUID | None = None


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


class WebSource(BaseModel):
    """One published page an attributed sentence rests on.

    Deliberately not a `Citation`. A citation is a fact read off this patient's
    own document and carries the confidence that reading earned; this is general
    drug-reference publishing, which says nothing about them and has no such
    score. They are shown under separate headings for the same reason they are
    separate types here — a reader has to be able to tell which is which.
    """

    url: str
    title: str | None = None


class AskResponse(BaseModel):
    answer: str

    # Which conversation the answer landed in. Echoed back even when the
    # request named one, so a client that started a new conversation learns its
    # id from the same place either way.
    session_id: UUID

    confidence: float | None = Field(
        default=None,
        description="The weakest supporting fact. Null when the answer rests on none.",
    )
    band: ConfidenceBand = "none"
    basis: str | None = Field(default=None, description="Why the score is what it is.")

    citations: list[Citation] = []
    web: list[WebSource] = Field(
        default_factory=list,
        description="Published pages behind any attributed general-medicine sentence.",
    )
    grounded: bool = True
    unsupported: list[str] | None = Field(
        default=None, description="Claims the verifier could not trace to evidence."
    )

    tool_calls: list[str] = Field(
        default_factory=list, description="What was consulted, in order. For the trace view."
    )
    disclaimer: str


class SessionRead(BaseModel):
    """One conversation, as it appears in the list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    # What the list sorts by: a conversation is 'recent' by its last message,
    # not by when it was started.
    updated_at: datetime


class MessageRead(BaseModel):
    """One message, with whatever qualified it.

    Everything from `confidence` down is null or empty on a user message. The
    shape is shared rather than split in two because the transcript renders
    both in one pass, and a client checking `role` is clearer than one checking
    which of two response types it received.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: Role
    content: str
    created_at: datetime

    confidence: float | None = None
    band: ConfidenceBand = "none"
    basis: str | None = None
    citations: list[Citation] = []
    web: list[WebSource] = Field(default_factory=list)
    grounded: bool = True
    unsupported: list[str] | None = None
    tool_calls: list[str] = Field(default_factory=list)

    truncated: bool = False

    # Sent rather than left to the client to supply. It is safety wording, and
    # a copy in the frontend is a copy that can drift from the one the answer
    # was actually generated under.
    disclaimer: str = DISCLAIMER

    # A user message has none of the apparatus, and the columns are null rather
    # than absent. Coerced here so the transcript is one shape the whole way
    # down and the client never has to test a field for null before rendering
    # it — `role` is the only branch it needs.
    @field_validator(
        "band", "grounded", "citations", "web", "tool_calls", "truncated", mode="before"
    )
    @classmethod
    def _default_if_null(cls, value: Any, info: ValidationInfo) -> Any:
        if value is not None:
            return value
        return {"band": "none", "grounded": True, "truncated": False}.get(info.field_name, [])


class TranscriptRead(BaseModel):
    """A conversation and everything said in it."""

    session: SessionRead
    messages: list[MessageRead]


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


def from_agent(result: dict[str, Any], session_id: UUID) -> AskResponse:
    """Turn what `ask` returns into what the API promises."""
    return AskResponse(
        answer=result.get("answer") or "",
        session_id=session_id,
        confidence=result.get("confidence"),
        band=result.get("band") or "none",
        basis=result.get("basis"),
        citations=[Citation(**item) for item in result.get("citations") or []],
        web=[WebSource(**item) for item in result.get("web") or []],
        grounded=result.get("grounded", True),
        unsupported=result.get("unsupported"),
        tool_calls=result.get("tool_calls") or [],
        disclaimer=result["disclaimer"],
    )
