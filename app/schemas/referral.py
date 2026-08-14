"""Response shapes for finding a doctor.

Two fields here are not decoration and should not be dropped to tidy the
payload. `attempts` is the search showing its working — without it, a list of
doctors 150 km away looks like a bad result rather than the honest answer to a
question with no closer one. `source_url` is what makes every row checkable:
a reader can open the listing and see the same name and number, which is the
only way 'this data is real' means anything.
"""

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class SpecialtyRead(BaseModel):
    """One kind of doctor this finding could be taken to."""

    label: str
    reason: str = Field(description="Why this finding routed here, in plain words.")
    confidence: Confidence
    search_terms: list[str] = Field(
        default=[],
        description=(
            "The directory's own words for this specialty, best first. Empty "
            "means no doctor directory lists it and the search falls back to "
            "places on the map."
        ),
    )


class SpecialtyListRead(BaseModel):
    """Who to take a finding to. Never one answer, always with a GP among them."""

    finding_id: str
    finding_type: str
    severity: str
    title: str
    specialties: list[SpecialtyRead]
    disclaimer: str = (
        "This suggests a type of doctor to talk to. It is not a diagnosis, and it "
        "does not say which doctor you need — a general practitioner can review "
        "any of it and refer you on."
    )


class ReferralRead(BaseModel):
    """One doctor or clinic, as fetched a moment ago."""

    kind: Literal["doctor", "clinic"]
    name: str
    specialty: str | None = None
    clinic_name: str | None = None
    address: str | None = None
    city: str | None = None
    distance_km: float | None = Field(
        default=None, description="Straight-line distance, not driving distance."
    )
    rating: float | None = None
    reviews: int | None = None
    phone: str | None = None
    open_now: bool | None = None
    hours: list[str] = Field(
        default=[],
        description=(
            "Opening hours as the directory publishes them, one line per day "
            "starting Monday. All seven rather than today's, because which day "
            "it is depends on where the reader is and not where this runs."
        ),
    )
    website: str | None = None
    slmc_number: str | None = Field(
        default=None,
        description=(
            "Sri Lanka Medical Council registration, when the directory gives a "
            "real one. Null where it holds an internal id instead — an internal "
            "id shown as a registration number would be a false credential."
        ),
    )
    other_clinics: list[str] = []

    source: str
    source_url: str | None = None
    booking_url: str | None = None


class AttemptRead(BaseModel):
    """One rung of a widening ladder."""

    term: str
    radius_km: int
    found: int
    provider: str = Field(
        default="",
        description=(
            "Which directory produced this rung. The two ladders run "
            "independently, so a nearby hospital is not buried under bookable "
            "doctors who happen to be further away."
        ),
    )


class ReferralSearchRequest(BaseModel):
    """The two questions, plus what the finding already decided."""

    location: str = Field(description="City, town or area — e.g. 'Kandy', 'Jaffna'.")
    specialty: str | None = Field(
        default=None,
        description=(
            "Which of the suggested specialties the user picked. Omitted, the "
            "highest-ranked one for the finding is used."
        ),
    )
    availability: str | None = Field(
        default=None,
        description=(
            "When the patient can go — 'this week', 'evenings', 'weekends'. "
            "Echoed back and used to flag places open now; full opening-hours "
            "matching needs fields this deployment may not be billed for."
        ),
    )
    radius_km: int | None = Field(
        default=None,
        description="Start the ladder here instead of at the first configured rung.",
    )


class ReferralSearchRead(BaseModel):
    """What was searched for, how far, and what came back."""

    specialty: str
    location: str
    resolved_location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    availability: str | None = None

    results: list[ReferralRead] = []
    attempts: list[AttemptRead] = Field(
        default=[],
        description=(
            "Every radius tried, in order. This is what lets the interface say "
            "how far it looked rather than only that it found nothing."
        ),
    )
    widened_to_km: int | None = None
    notice: str | None = Field(
        default=None,
        description="The sentence explaining an empty or a widened result.",
    )
    sources: list[str] = []
    degraded: list[str] = Field(
        default=[],
        description="What was unavailable, so the screen can say so rather than showing less.",
    )
    attribution: list[str] = []
    disclaimer: str = (
        "Details come from public directories and can be out of date. Please call "
        "to confirm before travelling. This is not a diagnosis."
    )


class LocationSuggestions(BaseModel):
    """Place names to choose from while typing.

    Empty is not an error. Autocomplete being unavailable must not stop a
    search — what the user typed is still a reasonable guess at a town.
    """

    suggestions: list[str] = []


class ProviderHealth(BaseModel):
    name: str
    configured: bool
    reachable: bool
    detail: str | None = None


class ReferralHealthRead(BaseModel):
    """Whether the directories are answering. Check before a demo, not during."""

    providers: list[ProviderHealth]
    usable: bool = Field(
        description="True when at least one directory can answer a search."
    )
