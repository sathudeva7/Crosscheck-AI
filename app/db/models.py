"""SQLAlchemy models.

The SQL files in migrations/ are the source of truth for the schema; these
classes mirror them so Python can read and write the tables.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Mirrors the CHECK constraints in migrations/002_document.sql
DOC_TYPES = (
    "prescription",
    "lab_report",
    "discharge_summary",
    "doctor_note",
    "radiology_report",
    "pathology_report",
    "referral_letter",
    "vaccination_record",
    "operative_note",
    "mixed",
    "other",
    "unknown",
)
PAGE_DOC_TYPES = tuple(t for t in DOC_TYPES if t != "mixed") + ("continuation",)
DOC_STATUSES = ("uploaded", "parsing", "transcribing", "extracting", "ready", "failed")
MODALITIES = ("digital", "scanned", "handwritten")

# Mirrors the CHECK constraints in migrations/003_timeline.sql
EVENT_TYPES = (
    "medication",
    "lab_result",
    "vital_sign",
    "diagnosis",
    "allergy",
    "procedure",
    "note",
)
REVIEW_STATUSES = ("accepted", "needs_review", "confirmed", "corrected", "rejected")
# Statuses a user has set. Re-ingestion must leave these rows alone.
USER_DECIDED = ("confirmed", "corrected", "rejected")
RISKS = ("high", "medium", "low")
SOURCES = ("ai", "user")
SEVERITIES = ("none", "info", "warning", "danger")

# Mirrors the CHECK constraints in migrations/004_drug_reference.sql
DRUG_SOURCES = ("rxnorm", "model_verified", "user")
INTERACTION_SEVERITIES = ("Major", "Moderate", "Minor", "Unknown")

# Mirrors the CHECK constraints in migrations/005_finding.sql
FINDING_TYPES = (
    "interaction",
    "duplicate_therapy",
    "allergy_conflict",
    "dosage_conflict",
    "lab_trend",
    "critical_value",
)
FINDING_SEVERITIES = ("info", "warning", "danger")
FINDING_STATUSES = ("active", "dismissed")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Patient(Base):
    """One row per account.

    In this app the user *is* the patient, so this table doubles as the account
    record: `clerk_user_id` links it to the auth provider, and every document,
    timeline event, chunk, and finding hangs off `id`.
    """

    __tablename__ = "patient"
    __table_args__ = (
        CheckConstraint(
            "sex IN ('male', 'female', 'other', 'unknown')", name="patient_sex_check"
        ),
        {"comment": "Account holder and subject of every uploaded medical document."},
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="Surrogate key referenced by all patient-scoped tables.",
    )

    clerk_user_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        comment="Clerk user id ('sub' claim). Stubbed until JWT verification lands.",
    )

    email: Mapped[str | None] = mapped_column(
        String, comment="Contact email, mirrored from Clerk on first sign-in."
    )
    full_name: Mapped[str | None] = mapped_column(
        String,
        comment="Used to sanity-check the patient name extracted from uploads.",
    )

    dob: Mapped[date | None] = mapped_column(
        Date, comment="Date of birth. Lab reference ranges are age-dependent."
    )
    sex: Mapped[str | None] = mapped_column(
        String,
        comment="male | female | other | unknown. Lab reference ranges are sex-dependent.",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        comment="Free-text patient summary — background, chronic conditions, notes.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Row insert time.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Maintained by the patient_set_updated_at trigger.",
    )

    def __repr__(self) -> str:
        return f"<Patient {self.id} {self.full_name!r}>"


class Document(Base):
    """One uploaded medical document.

    Deliberately type-agnostic — nothing here is specific to prescriptions or
    lab reports. Write-once from the pipeline's side: user corrections edit the
    facts extracted from a document, never the document row itself.
    """

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("patient_id", "sha256", name="document_patient_sha_key"),
        CheckConstraint(_in_list("doc_type", DOC_TYPES), name="document_doc_type_check"),
        CheckConstraint(_in_list("status", DOC_STATUSES), name="document_status_check"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )

    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    # the uploaded file itself
    filename: Mapped[str | None] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)

    # what the document says about itself, filled in after extraction
    doc_date: Mapped[date | None] = mapped_column(Date)
    provider: Mapped[str | None] = mapped_column(String)
    clinician_name: Mapped[str | None] = mapped_column(String)
    clinician_specialty: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    patient_name_extracted: Mapped[str | None] = mapped_column(String)

    # pipeline progress
    status: Mapped[str] = mapped_column(String, nullable=False, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_no",
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} {self.doc_type} {self.status}>"


class DocumentPage(Base):
    """One page of a document, plus its transcription.

    The transcription is the expensive artifact — reading a scanned page costs
    a vision call. Caching it per page means extraction can be re-run with an
    improved schema without paying for vision again.
    """

    __tablename__ = "document_page"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="document_page_no_key"),
        CheckConstraint("page_no >= 1", name="document_page_no_positive"),
        CheckConstraint(_in_list("modality", MODALITIES), name="document_page_modality_check"),
        CheckConstraint(
            _in_list("doc_type", PAGE_DOC_TYPES), name="document_page_doc_type_check"
        ),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # a scanned folder can hold several kinds of document in one file
    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    modality: Mapped[str | None] = mapped_column(String)
    language: Mapped[str | None] = mapped_column(String)

    # signals triage decides from
    char_count: Mapped[int | None] = mapped_column(Integer)
    non_alnum_ratio: Mapped[Decimal | None] = mapped_column(Numeric)

    transcription: Mapped[str | None] = mapped_column(Text)
    transcription_model: Mapped[str | None] = mapped_column(String)
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Written by Postgres from `transcription`, never by us. Deferred because
    # nothing outside search wants it and it is roughly the size of the page.
    search_vector: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(transcription, ''))", persisted=True),
        deferred=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="pages")

    def __repr__(self) -> str:
        return f"<DocumentPage {self.document_id} p{self.page_no} {self.modality}>"


class Finding(Base):
    """A problem found across the patient's record.

    Derived and recomputed whenever the timeline changes, which is why `key`
    exists: it fingerprints what the finding is about, so a re-run updates the
    same row rather than duplicating it, and a dismissed warning stays
    dismissed.
    """

    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("patient_id", "key", name="finding_key"),
        CheckConstraint(_in_list("finding_type", FINDING_TYPES), name="finding_type_check"),
        CheckConstraint(_in_list("severity", FINDING_SEVERITIES), name="finding_severity_check"),
        CheckConstraint(_in_list("status", FINDING_STATUSES), name="finding_status_check"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )

    key: Mapped[str] = mapped_column(Text, nullable=False)
    finding_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    detail_source: Mapped[str | None] = mapped_column(String)

    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    event_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list
    )
    visit_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list
    )

    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    dismissed_reason: Mapped[str | None] = mapped_column(Text)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Finding {self.severity} {self.title!r}>"


class Drug(Base):
    """One active ingredient. Reference data, shared by every patient."""

    __tablename__ = "drug"
    __table_args__ = (
        CheckConstraint(_in_list("source", DRUG_SOURCES), name="drug_source_check"),
    )

    rxcui: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    atc_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    atc_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    source: Mapped[str] = mapped_column(String, nullable=False, default="rxnorm")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Drug {self.rxcui} {self.name}>"


class DrugAlias(Base):
    """A printed drug name and what it turned out to be.

    Grows as documents are read: every lookup, successful or not, is recorded
    so it is never paid for twice. Corrections land here too, which is how a
    user teaching the app one brand name helps every later upload.
    """

    __tablename__ = "drug_alias"
    __table_args__ = (
        UniqueConstraint("alias", name="drug_alias_key"),
        CheckConstraint(_in_list("source", DRUG_SOURCES), name="drug_alias_source_check"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    rxcui: Mapped[str | None] = mapped_column(
        Text, ForeignKey("drug.rxcui", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String, nullable=False, default="model_verified")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric)
    unresolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    drug: Mapped[Drug | None] = relationship()

    def __repr__(self) -> str:
        return f"<DrugAlias {self.alias!r} -> {self.rxcui}>"


class InteractionRule(Base):
    """A pair of ingredients known to interact.

    Keyed on ids, never names: DDInter says 'Acetylsalicylic acid' where a
    prescription says 'aspirin', and matching on text would miss the pair
    entirely and report a clean result.
    """

    __tablename__ = "interaction_rule"
    __table_args__ = (
        CheckConstraint("rxcui_a < rxcui_b", name="interaction_rule_order"),
        CheckConstraint(
            _in_list("severity", INTERACTION_SEVERITIES), name="interaction_rule_severity_check"
        ),
    )

    rxcui_a: Mapped[str] = mapped_column(Text, primary_key=True)
    rxcui_b: Mapped[str] = mapped_column(Text, primary_key=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    name_a: Mapped[str] = mapped_column(Text, nullable=False)
    name_b: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="ddinter")
    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<InteractionRule {self.name_a} + {self.name_b} {self.severity}>"


class TimelineVisit(Base):
    """One card on the timeline: a dated encounter.

    Not the same as an upload — a scanned folder can hold several visits in one
    file, which is why the natural key is (document_id, group_no) rather than
    the document alone.
    """

    __tablename__ = "timeline_visit"
    __table_args__ = (
        UniqueConstraint("document_id", "group_no", name="timeline_visit_group_key"),
        CheckConstraint(_in_list("severity", SEVERITIES), name="timeline_visit_severity_check"),
        CheckConstraint(_in_list("source", SOURCES), name="timeline_visit_source_check"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE")
    )
    group_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    visit_date: Mapped[date | None] = mapped_column(Date)
    date_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    date_raw: Mapped[str | None] = mapped_column(Text)

    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    provider: Mapped[str | None] = mapped_column(String)
    clinician_name: Mapped[str | None] = mapped_column(String)
    clinician_specialty: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)

    # colours the dot; set by the cross-check engine, so it starts at 'none'
    severity: Mapped[str] = mapped_column(String, nullable=False, default="none")
    source: Mapped[str] = mapped_column(String, nullable=False, default="ai")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    events: Mapped[list["TimelineEvent"]] = relationship(
        back_populates="visit",
        cascade="all, delete-orphan",
        order_by="TimelineEvent.event_type",
    )

    def __repr__(self) -> str:
        return f"<TimelineVisit {self.visit_date} {self.doc_type}>"


class TimelineEvent(Base):
    """One extracted fact: a drug, a lab value, a diagnosis.

    The editable layer. `document.extraction_json` keeps what the model
    originally said; corrections land here, and rows a user has decided on are
    never overwritten by re-ingestion.
    """

    __tablename__ = "timeline_event"
    __table_args__ = (
        UniqueConstraint("document_id", "extraction_path", name="timeline_event_path_key"),
        CheckConstraint(_in_list("event_type", EVENT_TYPES), name="timeline_event_type_check"),
        CheckConstraint(
            _in_list("review_status", REVIEW_STATUSES), name="timeline_event_review_status_check"
        ),
        CheckConstraint(_in_list("risk", RISKS), name="timeline_event_risk_check"),
        CheckConstraint(_in_list("source", SOURCES), name="timeline_event_source_check"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    visit_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("timeline_visit.id", ondelete="CASCADE"), nullable=False
    )

    # back to the source page, for "show me the original"
    document_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document.id", ondelete="SET NULL")
    )
    page_no: Mapped[int | None] = mapped_column(Integer)
    extraction_path: Mapped[str | None] = mapped_column(Text)

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str | None] = mapped_column(Text)
    display_text: Mapped[str | None] = mapped_column(Text)

    # medications
    drug_raw: Mapped[str | None] = mapped_column(Text)
    drug_name: Mapped[str | None] = mapped_column(Text)
    rxcui: Mapped[str | None] = mapped_column(String)
    atc_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    strength_value: Mapped[Decimal | None] = mapped_column(Numeric)
    strength_unit: Mapped[str | None] = mapped_column(String)
    dose_count: Mapped[Decimal | None] = mapped_column(Numeric)
    doses_per_day: Mapped[Decimal | None] = mapped_column(Numeric)
    frequency_raw: Mapped[str | None] = mapped_column(Text)
    frequency_label: Mapped[str | None] = mapped_column(Text)
    prn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_days: Mapped[Decimal | None] = mapped_column(Numeric)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    # lab results and vital signs
    analyte: Mapped[str | None] = mapped_column(Text)
    value_num: Mapped[Decimal | None] = mapped_column(Numeric)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String)
    operator: Mapped[str | None] = mapped_column(String)
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric)
    flag: Mapped[str | None] = mapped_column(String)
    computed_flag: Mapped[str | None] = mapped_column(String)

    # diagnoses, allergies, procedures
    description: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String)
    substance: Mapped[str | None] = mapped_column(Text)
    reaction: Mapped[str | None] = mapped_column(Text)
    severity_text: Mapped[str | None] = mapped_column(String)

    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # trust and review
    confidence: Mapped[Decimal | None] = mapped_column(Numeric)
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="needs_review")
    review_reasons: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    risk: Mapped[str] = mapped_column(String, nullable=False, default="low")
    source: Mapped[str] = mapped_column(String, nullable=False, default="ai")
    source_span: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    edited_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    visit: Mapped[TimelineVisit] = relationship(back_populates="events")

    @property
    def user_decided(self) -> bool:
        """Whether a person has ruled on this row, which makes it untouchable."""
        return self.source == "user" or self.review_status in USER_DECIDED

    def __repr__(self) -> str:
        return f"<TimelineEvent {self.event_type} {self.display_text!r}>"
