"""The forms the model fills in when reading a document.

These describe what a document *means*, not what it looks like — which is why
one form covers a printed lab report, a handwritten prescription pad and a
two-column clinic form alike. Layout varies endlessly; the underlying facts
(which drug, how much, how often) do not.

Three rules run through the whole file:

1. Every field is optional. Formats differ in what they include, and one
   missing duration must not fail an entire extraction. The only required
   fields are the ones used to check the model's work.

2. Values stay raw. "500mg" is kept as the string "500mg", not split into
   500 and "mg"; "27-Apr-2020" stays a string, not a date. Splitting and date
   parsing happen in normalize.py, where a wrong guess is visible and fixable.
   A model that silently converts units or resolves an ambiguous date hides
   its mistake.

3. Everything the model claims is grounded. Each item carries the exact text
   it was read from, so ground.py can check it really appears in the page.

The field descriptions are not comments — the model reads them as
instructions. Wording them precisely is part of making extraction reliable.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Grounded(BaseModel):
    """Mixin: how we check any single extracted item."""

    source_span: str = Field(
        description=(
            "The exact text from the page this item was read from, copied "
            "character for character. Do not paraphrase or reformat it."
        )
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "How certain you are this item is correct, from 0 to 1. Lower it "
            "when handwriting is unclear or the layout is ambiguous."
        ),
    )
    legible: bool = Field(
        default=True,
        description=(
            "False if any part of this item could not be read clearly. Set "
            "the unreadable fields to null rather than guessing at them."
        ),
    )


# --- items -------------------------------------------------------------------


class Medication(Grounded):
    drug: str | None = Field(
        default=None,
        description="Drug name exactly as written, brand or generic. Null if illegible.",
    )
    strength: str | None = Field(
        default=None, description="Dose strength as printed, e.g. '500mg', '1/2 tablet'."
    )
    form: str | None = Field(default=None, description="e.g. tablet, capsule, syrup, injection.")
    route: str | None = Field(default=None, description="e.g. oral, PO, IV, topical.")
    frequency: str | None = Field(
        default=None,
        description=(
            "How often it is taken, exactly as written. Keep shorthand as-is: "
            "'1-0-1', 'BD', 'TDS', 'OD', 'nocte', '1 Morning, 1 Night'."
        ),
    )
    duration: str | None = Field(default=None, description="e.g. '10 days', '1 month'.")
    quantity: str | None = Field(default=None, description="Total dispensed, e.g. 'Tot: 20 Tab'.")
    refills: str | None = Field(default=None, description="Refills authorised, if stated.")
    prn: bool = Field(
        default=False, description="True if taken only as needed (PRN, SOS, 'if required')."
    )
    instructions: str | None = Field(
        default=None, description="Other directions, e.g. 'before food', 'with water'."
    )


class LabResult(Grounded):
    analyte: str | None = Field(
        default=None, description="Test name as printed, e.g. 'Creatinine', 'HbA1c'."
    )
    value: str | None = Field(
        default=None,
        description=(
            "Result exactly as printed, including symbols. Keep '<0.01', '>200', "
            "'Positive', 'Not detected' as they appear. Do not convert anything."
        ),
    )
    unit: str | None = Field(
        default=None,
        description="Unit as printed, e.g. 'mg/dL', 'g/dL', '%'. Never infer a missing unit.",
    )
    reference_range: str | None = Field(
        default=None, description="Normal range as printed, e.g. '0.7 - 1.3', '> 90'."
    )
    flag: str | None = Field(
        default=None, description="Abnormality marker printed by the lab, e.g. 'H', 'L', 'A'."
    )
    specimen: str | None = Field(default=None, description="e.g. serum, plasma, urine.")
    method: str | None = Field(default=None, description="Assay method, if stated.")
    collected_at: str | None = Field(
        default=None,
        description="Collection date/time for this result if it differs from the report header.",
    )


class Diagnosis(Grounded):
    description: str | None = Field(default=None, description="Condition as written.")
    code: str | None = Field(
        default=None,
        description="Diagnostic code only if printed on the document. Never supply one yourself.",
    )
    category: Literal["principal", "secondary", "comorbidity", "unknown"] = Field(
        default="unknown", description="How the document presents this diagnosis."
    )
    onset: str | None = Field(default=None, description="Onset or diagnosis date, as written.")


class Allergy(Grounded):
    substance: str | None = Field(default=None, description="Drug, food or substance.")
    reaction: str | None = Field(default=None, description="e.g. rash, anaphylaxis.")
    severity: str | None = Field(default=None, description="e.g. mild, severe, as stated.")


class Procedure(Grounded):
    description: str | None = Field(default=None, description="Procedure or operation performed.")
    performed_at: str | None = Field(default=None, description="Date, as written.")


class VitalSign(Grounded):
    name: str | None = Field(
        default=None, description="e.g. 'Temperature', 'Blood Pressure', 'Pulse', 'Weight'."
    )
    value: str | None = Field(
        default=None, description="As printed, e.g. '36', '120/80'. Keep both parts of a BP."
    )
    unit: str | None = Field(default=None, description="e.g. 'deg C', 'mmHg', 'kg'.")
    measured_at: str | None = Field(
        default=None,
        description=(
            "Date this reading was taken, as written. Charts plot several dated "
            "readings — give each point its own date."
        ),
    )


# --- shared header -----------------------------------------------------------


class DocumentHeader(BaseModel):
    """Facts every medical document carries, whatever its type."""

    document_date: str | None = Field(
        default=None,
        description=(
            "Date on the document, exactly as printed, e.g. '27-Apr-2020', "
            "'14/03/2026'. Do not reformat or resolve ambiguity."
        ),
    )
    provider: str | None = Field(
        default=None, description="Issuing clinic, hospital or laboratory."
    )
    clinician_name: str | None = Field(
        default=None,
        description="Prescribing or reporting clinician. Null if blacked out on the document.",
    )
    clinician_specialty: str | None = Field(
        default=None, description="e.g. 'Cardiology', or qualifications such as 'M.B.B.S., M.D.'."
    )
    clinician_registration: str | None = Field(
        default=None, description="Medical registration or council number, if printed."
    )
    patient_name: str | None = Field(default=None, description="Patient name as printed.")
    patient_identifier: str | None = Field(
        default=None, description="Patient/file/MRN number printed on the document."
    )
    summary: str | None = Field(
        default=None,
        description=(
            "One plain sentence describing what this document records, for the "
            "patient's timeline. E.g. 'Anticoagulation started after atrial "
            "fibrillation diagnosis.'"
        ),
    )


class _BaseExtraction(BaseModel):
    header: DocumentHeader = Field(description="Facts from the top of the document.")
    unmapped: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Anything clinically meaningful on the page that no other field "
            "fits, as label/value pairs. Use this rather than dropping it."
        ),
    )


# --- one form per document type ----------------------------------------------


class PrescriptionExtraction(_BaseExtraction):
    """A prescription: who prescribed what, how much, how often, how long."""

    medications: list[Medication] = Field(
        default_factory=list,
        description=(
            "One entry per prescribed item, in the order listed. Do not include "
            "any item that is struck through — those were cancelled."
        ),
    )
    allergies: list[Allergy] = Field(
        default_factory=list, description="Allergies noted anywhere on the document."
    )
    vitals: list[VitalSign] = Field(
        default_factory=list,
        description="Vital signs recorded, including every dated point plotted on a chart.",
    )
    diagnoses: list[Diagnosis] = Field(
        default_factory=list, description="Conditions the prescription is written for, if stated."
    )
    advice: str | None = Field(default=None, description="General advice given to the patient.")
    follow_up: str | None = Field(default=None, description="Follow-up date or instruction.")


class LabReportExtraction(_BaseExtraction):
    """A laboratory report: measurements with units and reference ranges."""

    panel_name: str | None = Field(default=None, description="e.g. 'Renal Profile', 'Full Blood Count'.")
    collected_at: str | None = Field(
        default=None,
        description=(
            "When the sample was taken, as printed. This is the clinically "
            "meaningful date — keep it separate from the reporting date."
        ),
    )
    reported_at: str | None = Field(
        default=None, description="When the report was issued, as printed."
    )
    ordered_by: str | None = Field(default=None, description="Requesting clinician, if stated.")
    results: list[LabResult] = Field(
        default_factory=list, description="One entry per test result, including normal ones."
    )
    vitals: list[VitalSign] = Field(
        default_factory=list, description="Any vital signs recorded on the report."
    )
    interpretation: str | None = Field(
        default=None, description="Comments or interpretation printed by the lab."
    )


class DischargeSummaryExtraction(_BaseExtraction):
    """A hospital discharge summary: an episode of care from admission to discharge."""

    admission_date: str | None = Field(default=None, description="As printed.")
    discharge_date: str | None = Field(default=None, description="As printed.")
    facility: str | None = Field(default=None, description="Hospital or ward.")
    diagnoses: list[Diagnosis] = Field(
        default_factory=list, description="Admission and discharge diagnoses."
    )
    procedures: list[Procedure] = Field(default_factory=list, description="Operations performed.")
    medications: list[Medication] = Field(
        default_factory=list, description="Medications the patient is discharged on."
    )
    allergies: list[Allergy] = Field(default_factory=list, description="Allergies recorded.")
    results: list[LabResult] = Field(
        default_factory=list, description="Investigation results quoted in the summary."
    )
    vitals: list[VitalSign] = Field(default_factory=list, description="Vital signs recorded.")
    hospital_course: str | None = Field(
        default=None, description="Narrative account of the admission, kept as written."
    )
    condition_at_discharge: str | None = Field(default=None, description="As stated.")
    follow_up_instructions: str | None = Field(default=None, description="As stated.")


class GenericExtraction(_BaseExtraction):
    """Fallback for document types with no dedicated form.

    Radiology reports, pathology reports, referral letters and anything
    unexpected land here. It still yields a dated, attributed entry for the
    timeline instead of failing, and anything structured goes into `unmapped`.
    """

    document_kind: str | None = Field(
        default=None, description="What kind of document this appears to be, in your own words."
    )
    findings: str | None = Field(default=None, description="Main findings or content, as written.")
    medications: list[Medication] = Field(
        default_factory=list, description="Any medications mentioned."
    )
    results: list[LabResult] = Field(
        default_factory=list, description="Any measured results reported."
    )
    diagnoses: list[Diagnosis] = Field(
        default_factory=list, description="Any conditions mentioned."
    )
    allergies: list[Allergy] = Field(default_factory=list, description="Any allergies mentioned.")
    vitals: list[VitalSign] = Field(default_factory=list, description="Any vital signs recorded.")


# extract.py picks the form from the page's doc_type. Types without a dedicated
# form fall back to GenericExtraction rather than failing.
SCHEMA_BY_DOC_TYPE: dict[str, type[_BaseExtraction]] = {
    "prescription": PrescriptionExtraction,
    "lab_report": LabReportExtraction,
    "discharge_summary": DischargeSummaryExtraction,
}


def schema_for(doc_type: str) -> type[_BaseExtraction]:
    """The form to use for a document type."""
    return SCHEMA_BY_DOC_TYPE.get(doc_type, GenericExtraction)
