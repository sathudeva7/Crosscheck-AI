-- 003: timeline_visit + timeline_event
--
-- The two levels the timeline screen actually has.
--
--   timeline_visit  one card: a date, a place, a clinician, a summary
--   timeline_event  one fact inside it: a drug, a lab value, a diagnosis
--
-- A visit is not the same as an upload. A scanned folder can hold three
-- visits in one file, so a visit is (document, group_no) -- the group being
-- what extraction read as one document.
--
-- These tables are the *derived* layer, and the only layer a user may edit.
-- document.extraction_json stays exactly as the model produced it, so there is
-- always a record of what the AI said even after a correction. Re-running
-- ingestion refreshes rows here, but never touches one a user has confirmed,
-- corrected or rejected -- see timeline_event.source and review_status.
--
-- Column or JSON: a field is a column when we filter, sort or join on it
-- (dates, rxcui, atc_codes, numeric values, review status) and lives in
-- `details` when we only display it (route, refills, specimen, method).

CREATE TABLE IF NOT EXISTS timeline_visit (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id    uuid NOT NULL REFERENCES patient(id) ON DELETE CASCADE,
    document_id   uuid REFERENCES document(id) ON DELETE CASCADE,

    -- which extraction group inside that document; 0 for a single-document file
    group_no      int NOT NULL DEFAULT 0,

    visit_date       date,
    date_ambiguous   boolean NOT NULL DEFAULT false,
    date_raw         text,

    doc_type             text NOT NULL DEFAULT 'unknown',
    provider             text,
    clinician_name       text,
    clinician_specialty  text,
    summary              text,

    -- drives the colour of the dot on the timeline; set by the cross-check
    -- engine (005), so everything starts at 'none'
    severity      text NOT NULL DEFAULT 'none',

    source        text NOT NULL DEFAULT 'ai',

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- re-ingesting a document updates its visits instead of duplicating them
    CONSTRAINT timeline_visit_group_key UNIQUE (document_id, group_no),

    CONSTRAINT timeline_visit_severity_check CHECK (severity IN (
        'none',      -- nothing found
        'info',      -- worth knowing, not a risk
        'warning',   -- duplicate therapy, moderate interaction
        'danger'     -- major interaction, allergy contradiction, critical value
    )),
    CONSTRAINT timeline_visit_source_check CHECK (source IN ('ai', 'user'))
);

COMMENT ON TABLE  timeline_visit                     IS 'One card on the timeline: a dated encounter with its facts hanging off it.';
COMMENT ON COLUMN timeline_visit.document_id         IS 'NULL when the user added the visit by hand rather than uploading a file.';
COMMENT ON COLUMN timeline_visit.group_no            IS 'Which document within the upload. A scanned folder yields several visits.';
COMMENT ON COLUMN timeline_visit.visit_date          IS 'The clinically meaningful date: sample collection for a lab, discharge for an admission.';
COMMENT ON COLUMN timeline_visit.date_ambiguous      IS 'True for dates like 04-07-2019 where day/month order cannot be settled. Show a marker.';
COMMENT ON COLUMN timeline_visit.date_raw            IS 'The date exactly as printed, so a wrong parse is visible against the source.';
COMMENT ON COLUMN timeline_visit.severity            IS 'Highest severity of any finding in this visit. Colours the timeline dot.';

CREATE INDEX IF NOT EXISTS timeline_visit_patient_date_idx
    ON timeline_visit (patient_id, visit_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS timeline_visit_document_idx
    ON timeline_visit (document_id);

DROP TRIGGER IF EXISTS timeline_visit_set_updated_at ON timeline_visit;
CREATE TRIGGER timeline_visit_set_updated_at
    BEFORE UPDATE ON timeline_visit
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


CREATE TABLE IF NOT EXISTS timeline_event (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id    uuid NOT NULL REFERENCES patient(id) ON DELETE CASCADE,
    visit_id      uuid NOT NULL REFERENCES timeline_visit(id) ON DELETE CASCADE,

    -- where this fact came from, so the interface can show the original page
    document_id      uuid REFERENCES document(id) ON DELETE SET NULL,
    page_no          int,
    extraction_path  text,

    event_type    text NOT NULL,
    event_date    date,
    title         text,
    display_text  text,

    -- medications -----------------------------------------------------------
    drug_raw        text,
    drug_name       text,
    rxcui           text,
    atc_codes       text[],
    strength_value  numeric,
    strength_unit   text,
    dose_count      numeric,
    doses_per_day   numeric,
    frequency_raw   text,
    frequency_label text,
    prn             boolean NOT NULL DEFAULT false,
    duration_days   numeric,
    start_date      date,
    end_date        date,

    -- lab results and vital signs -------------------------------------------
    analyte        text,
    value_num      numeric,
    value_text     text,
    unit           text,
    operator       text,
    ref_low        numeric,
    ref_high       numeric,
    flag           text,
    computed_flag  text,

    -- diagnoses, allergies, procedures --------------------------------------
    description   text,
    code          text,
    substance     text,
    reaction      text,
    severity_text text,

    -- everything shown but never searched
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- trust and review ------------------------------------------------------
    confidence     numeric,
    review_status  text NOT NULL DEFAULT 'needs_review',
    review_reasons text[],
    risk           text NOT NULL DEFAULT 'low',
    source         text NOT NULL DEFAULT 'ai',
    source_span    text,
    signals        jsonb NOT NULL DEFAULT '{}'::jsonb,
    edited_fields  jsonb,
    reviewed_at    timestamptz,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- one row per extracted fact: re-ingesting updates rather than duplicates.
    -- User-added rows have no extraction_path, so the index skips them.
    CONSTRAINT timeline_event_path_key UNIQUE (document_id, extraction_path),

    CONSTRAINT timeline_event_type_check CHECK (event_type IN (
        'medication',
        'lab_result',
        'vital_sign',
        'diagnosis',
        'allergy',
        'procedure',
        'note'
    )),
    CONSTRAINT timeline_event_review_status_check CHECK (review_status IN (
        'accepted',      -- confident enough to show without asking
        'needs_review',  -- shown, but flagged for the user to check
        'confirmed',     -- the user checked it and it was right
        'corrected',     -- the user changed it
        'rejected'       -- not the patient's, or cancelled. Hidden, not deleted.
    )),
    CONSTRAINT timeline_event_risk_check CHECK (risk IN ('high', 'medium', 'low')),
    CONSTRAINT timeline_event_source_check CHECK (source IN ('ai', 'user')),
    CONSTRAINT timeline_event_flag_check CHECK (
        flag IS NULL OR flag IN ('H', 'L', 'N', 'A')
    ),
    CONSTRAINT timeline_event_computed_flag_check CHECK (
        computed_flag IS NULL OR computed_flag IN ('H', 'L', 'N')
    )
);

COMMENT ON TABLE  timeline_event                   IS 'One extracted fact. The editable layer; extraction_json stays as the AI produced it.';
COMMENT ON COLUMN timeline_event.extraction_path   IS 'Where it sits in extraction_json, e.g. ''medications[0]''. NULL for user-added rows.';
COMMENT ON COLUMN timeline_event.page_no           IS 'Page the source_span was found on, for ''show me the original''.';
COMMENT ON COLUMN timeline_event.display_text      IS 'Ready-to-render label, e.g. ''Warfarin 5 mg - once daily''. Built once at save time.';
COMMENT ON COLUMN timeline_event.drug_raw          IS 'Drug name exactly as printed, e.g. ''Betaloc''.';
COMMENT ON COLUMN timeline_event.drug_name         IS 'Active ingredient, e.g. ''metoprolol''. Two brands of one drug share this.';
COMMENT ON COLUMN timeline_event.rxcui             IS 'RxNorm ingredient id. Joins to interaction_rule (004) - names have too many spellings.';
COMMENT ON COLUMN timeline_event.atc_codes         IS 'WHO drug classes. Two drugs sharing the first 3 characters are the same family.';
COMMENT ON COLUMN timeline_event.dose_count        IS 'Tablets per dose, e.g. 0.5 for ''1/2''. Distinct from strength.';
COMMENT ON COLUMN timeline_event.doses_per_day     IS 'Numeric, for comparing regimens. 1-0-1, BD and ''1 morning 1 night'' all give 2.';
COMMENT ON COLUMN timeline_event.frequency_label   IS 'Human wording for the screen: ''twice daily'', ''nightly'', ''when needed''.';
COMMENT ON COLUMN timeline_event.end_date          IS 'start_date + duration. Lets a query ask which drugs are still being taken.';
COMMENT ON COLUMN timeline_event.value_num         IS 'Numeric result, for trends. NULL when the result is text such as ''Positive''.';
COMMENT ON COLUMN timeline_event.operator          IS '''<'' or ''>'' for results like ''<0.01'', which must not be plotted as a plain number.';
COMMENT ON COLUMN timeline_event.flag              IS 'Abnormality marker printed by the lab.';
COMMENT ON COLUMN timeline_event.computed_flag     IS 'Worked out from the reference range. Disagreement with flag forces a review.';
COMMENT ON COLUMN timeline_event.details           IS 'Displayed but never searched: route, refills, instructions, specimen, method.';
COMMENT ON COLUMN timeline_event.confidence        IS 'model confidence x span match x page quality. Not the model''s own claim alone.';
COMMENT ON COLUMN timeline_event.review_reasons    IS 'Plain-language reasons, shown to the user as-is.';
COMMENT ON COLUMN timeline_event.source            IS '''user'' rows were added or corrected by hand and are never overwritten by re-ingestion.';
COMMENT ON COLUMN timeline_event.source_span       IS 'The exact text this was read from, verified against the page transcription.';
COMMENT ON COLUMN timeline_event.signals           IS 'The confidence parts, kept so a score can be explained rather than asserted.';
COMMENT ON COLUMN timeline_event.edited_fields     IS 'What the user changed and what it was before, e.g. {"drug_name": {"from": "...", "to": "..."}}.';

-- the timeline itself
CREATE INDEX IF NOT EXISTS timeline_event_patient_date_idx
    ON timeline_event (patient_id, event_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS timeline_event_visit_idx
    ON timeline_event (visit_id);

-- the review queue: lowest confidence first
CREATE INDEX IF NOT EXISTS timeline_event_review_idx
    ON timeline_event (patient_id, review_status, confidence);

-- interaction checking joins on rxcui
CREATE INDEX IF NOT EXISTS timeline_event_rxcui_idx
    ON timeline_event (patient_id, rxcui)
    WHERE rxcui IS NOT NULL;

-- duplicate-therapy checking searches the class array
CREATE INDEX IF NOT EXISTS timeline_event_atc_idx
    ON timeline_event USING gin (atc_codes);

-- lab trends: every reading of one analyte in date order
CREATE INDEX IF NOT EXISTS timeline_event_analyte_idx
    ON timeline_event (patient_id, analyte, event_date)
    WHERE analyte IS NOT NULL;

-- which medicines are still being taken
CREATE INDEX IF NOT EXISTS timeline_event_active_drug_idx
    ON timeline_event (patient_id, end_date)
    WHERE event_type = 'medication';

DROP TRIGGER IF EXISTS timeline_event_set_updated_at ON timeline_event;
CREATE TRIGGER timeline_event_set_updated_at
    BEFORE UPDATE ON timeline_event
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
