"""What a lab report says, and who usually reads one like it.

The shape the document card is built from. Two fields carry the weight.

`readings` is every measurement with the range beside it, including the ones
reported in words — and for those, no verdict. 'detected' is shown next to
'expected: Not detected' and the reader draws the conclusion, because an HIV
result decided wrongly by either a rule or a model is the worst thing this
application could do.

`interpretation` is the laboratory's own sentence, quoted exactly. It is
present only when the note is about this patient rather than the methodology
boilerplate printed on every copy of the report. Quoting it is the one way this
app can say something specific about a condition without diagnosing anything:
the words are the pathologist's, and they are attributed.
"""

from pydantic import BaseModel, Field


class ReadingRead(BaseModel):
    """One measurement, as the report printed it."""

    analyte: str
    value: str
    unit: str | None = None
    reference: str | None = Field(
        default=None, description="The normal range, in the report's own words."
    )
    abnormal: bool = Field(
        default=False,
        description=(
            "Outside its range, decided by comparing the parsed number to the "
            "parsed bounds. Always false for a result reported in words — those "
            "carry no verdict at all."
        ),
    )
    lab_flagged: bool = Field(
        default=False, description="The laboratory marked this abnormal itself."
    )
    qualitative: bool = Field(
        default=False,
        description="Reported in words rather than numbers — 'detected', 'Positive'.",
    )


class SuggestedSpecialtyRead(BaseModel):
    """One kind of doctor this report could be taken to."""

    specialty: str
    reason: str
    confidence: str
    directories: list[str] = Field(
        default=[],
        description=(
            "Which channelling networks list this specialty. A name in both is "
            "the strongest signal it is a real specialty rather than one "
            "clinic's own wording."
        ),
    )


class ReportRecommendationRead(BaseModel):
    """The card shown when a lab report is opened."""

    document_id: str
    filename: str | None = None
    doc_type: str
    provider: str | None = None
    doc_date: str | None = None

    panel_names: list[str] = []
    about: str = Field(
        default="",
        description="What the report looks at, in plain words. Never a diagnosis.",
    )
    result_count: int = 0
    abnormal_count: int = 0
    readings: list[ReadingRead] = []

    interpretation: str | None = Field(
        default=None,
        description=(
            "The laboratory's own note, quoted exactly, and only when it is "
            "about this patient's result rather than methodology."
        ),
    )

    specialties: list[SuggestedSpecialtyRead] = []
    error: str | None = Field(
        default=None,
        description=(
            "Why there is no recommendation. Shown as a sentence rather than as "
            "an empty list — a report we could not read is not a report with "
            "nothing to say."
        ),
    )

    disclaimer: str = (
        "This says which kind of doctor usually reads a report like this. It is "
        "not a diagnosis and does not say what any result means. Please talk to "
        "a doctor about your results."
    )
