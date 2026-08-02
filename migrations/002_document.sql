-- 002: document + document_page
--
-- `document` is one uploaded file. `document_page` is one page of it, and
-- holds the transcription -- the plain-text version of what the page says.
--
-- Deliberately type-agnostic: nothing here is specific to prescriptions or
-- lab reports. Per-type structure lives in the extraction schemas and in
-- timeline_event (003), so a new document type needs no change here.
--
-- Both tables are write-once from the pipeline's point of view. User
-- corrections never edit them; they edit the facts extracted from them.

CREATE TABLE IF NOT EXISTS document (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id    uuid NOT NULL REFERENCES patient(id) ON DELETE CASCADE,

    doc_type      text NOT NULL DEFAULT 'unknown',

    -- the uploaded file itself
    filename      text,
    mime_type     text NOT NULL,
    storage_path  text NOT NULL,
    sha256        text NOT NULL,
    size_bytes    bigint,
    page_count    int,

    -- what the document says about itself, filled in after extraction
    doc_date               date,
    provider               text,
    clinician_name         text,
    clinician_specialty    text,
    summary                text,
    patient_name_extracted text,

    -- pipeline progress
    status                   text NOT NULL DEFAULT 'uploaded',
    error_message            text,
    processing_started_at    timestamptz,
    processing_completed_at  timestamptz,
    extraction_json          jsonb,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- re-uploading the same file is a no-op rather than a duplicate timeline
    CONSTRAINT document_patient_sha_key UNIQUE (patient_id, sha256),

    CONSTRAINT document_doc_type_check CHECK (doc_type IN (
        'prescription',
        'lab_report',
        'discharge_summary',
        'doctor_note',
        'radiology_report',
        'pathology_report',
        'referral_letter',
        'vaccination_record',
        'operative_note',
        'mixed',      -- pages of differing types in one file
        'other',      -- classified, but not a type we model
        'unknown'     -- not classified yet
    )),
    CONSTRAINT document_status_check CHECK (status IN (
        'uploaded', 'parsing', 'transcribing', 'extracting', 'ready', 'failed'
    ))
);

COMMENT ON TABLE  document                         IS 'One uploaded medical document. Type-agnostic.';
COMMENT ON COLUMN document.patient_id              IS 'Owner. Set from the session at upload, never from the file.';
COMMENT ON COLUMN document.doc_type                IS 'Dominant type across pages, or ''mixed''. See document_page.doc_type.';
COMMENT ON COLUMN document.storage_path            IS 'Where the original file lives. The file is never modified.';
COMMENT ON COLUMN document.sha256                  IS 'Hash of the file bytes, used to detect re-uploads.';
COMMENT ON COLUMN document.page_count              IS 'Filled in once the file is opened.';
COMMENT ON COLUMN document.doc_date                IS 'Date printed on the document, not the upload date.';
COMMENT ON COLUMN document.provider                IS 'Issuing facility, e.g. ''City Medical Lab''.';
COMMENT ON COLUMN document.clinician_name          IS 'Primary prescriber or reporting clinician, e.g. ''Dr. Silva''.';
COMMENT ON COLUMN document.clinician_specialty     IS 'e.g. ''Cardiology''. Shown next to the name on the timeline.';
COMMENT ON COLUMN document.summary                 IS 'One-line generated summary shown under the timeline header.';
COMMENT ON COLUMN document.patient_name_extracted  IS 'Name read off the document; warn if it does not match the account.';
COMMENT ON COLUMN document.status                  IS 'uploaded -> parsing -> transcribing -> extracting -> ready | failed.';
COMMENT ON COLUMN document.error_message           IS 'Why processing failed, in language a user can act on.';
COMMENT ON COLUMN document.extraction_json         IS 'Raw merged model output. Kept as-is; corrections never rewrite it.';

CREATE INDEX IF NOT EXISTS document_patient_date_idx
    ON document (patient_id, doc_date DESC);
CREATE INDEX IF NOT EXISTS document_patient_status_idx
    ON document (patient_id, status);

DROP TRIGGER IF EXISTS document_set_updated_at ON document;
CREATE TRIGGER document_set_updated_at
    BEFORE UPDATE ON document
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


CREATE TABLE IF NOT EXISTS document_page (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    page_no       int NOT NULL,

    -- a scanned folder can hold several kinds of document in one file, so
    -- type is decided per page and extraction picks the schema per page
    doc_type      text NOT NULL DEFAULT 'unknown',

    -- how this page has to be read, decided by triage
    modality      text,
    language      text,

    -- signals triage decides from, kept for debugging and for the
    -- confidence score
    char_count       int,
    non_alnum_ratio  numeric,

    -- the expensive artifact: pass-1 output, cached so extraction can be
    -- re-run later without paying for vision again
    transcription        text,
    transcription_model  text,
    transcribed_at       timestamptz,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT document_page_no_key UNIQUE (document_id, page_no),
    CONSTRAINT document_page_no_positive CHECK (page_no >= 1),
    CONSTRAINT document_page_modality_check CHECK (modality IN (
        'digital', 'scanned', 'handwritten'
    )),
    CONSTRAINT document_page_doc_type_check CHECK (doc_type IN (
        'prescription',
        'lab_report',
        'discharge_summary',
        'doctor_note',
        'radiology_report',
        'pathology_report',
        'referral_letter',
        'vaccination_record',
        'operative_note',
        'continuation',   -- same document as the previous page
        'other',
        'unknown'
    ))
);

COMMENT ON TABLE  document_page                     IS 'One page of an uploaded document, plus its transcription.';
COMMENT ON COLUMN document_page.doc_type            IS 'Per-page type. ''continuation'' means it belongs to the previous page''s document.';
COMMENT ON COLUMN document_page.modality            IS 'digital = has a usable text layer; scanned/handwritten go to the vision model.';
COMMENT ON COLUMN document_page.language            IS 'Detected language(s), e.g. ''en'', ''si'', ''ta'', ''en+ta''.';
COMMENT ON COLUMN document_page.char_count          IS 'Characters in the embedded text layer. 0 means a pure scan.';
COMMENT ON COLUMN document_page.non_alnum_ratio     IS 'High ratio means the embedded text layer is junk OCR - treat as scanned.';
COMMENT ON COLUMN document_page.transcription       IS 'Plain-text version of the page. Source for chunks and for extraction.';
COMMENT ON COLUMN document_page.transcription_model IS 'Which model produced it; NULL when read straight from the text layer.';

CREATE INDEX IF NOT EXISTS document_page_document_idx
    ON document_page (document_id, page_no);

DROP TRIGGER IF EXISTS document_page_set_updated_at ON document_page;
CREATE TRIGGER document_page_set_updated_at
    BEFORE UPDATE ON document_page
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
