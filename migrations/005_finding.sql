-- 005: finding
--
-- One row per problem detected across the patient's whole record: an
-- interaction between drugs from two different visits, a duplicate therapy, a
-- lab value drifting the wrong way.
--
-- Findings are derived, and are recomputed whenever the timeline changes --
-- adding a document today can create a finding about a prescription from three
-- years ago. `key` is what makes that safe to repeat: it is a fingerprint of
-- what the finding is about (its type and the drugs involved), so the same
-- problem always lands on the same row instead of being duplicated on every
-- run, and a finding the user dismissed stays dismissed.
--
-- A dismissed finding is kept even after its cause disappears. It is a
-- tombstone: without it, deleting and re-adding a document would resurrect a
-- warning the user has already considered and waved away.
--
-- What is *not* here: any judgement made by a model. Whether a finding exists
-- is decided by SQL against interaction_rule and the ATC classes. A model only
-- writes `detail`, the sentence explaining a finding that already exists.

CREATE TABLE IF NOT EXISTS finding (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id    uuid NOT NULL REFERENCES patient(id) ON DELETE CASCADE,

    key           text NOT NULL,
    finding_type  text NOT NULL,
    severity      text NOT NULL,

    title         text NOT NULL,
    detail        text,
    detail_source text,

    -- the facts the finding was built from: the rule that matched, the ATC
    -- codes, the dates, the assumption used about overlap
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,

    event_ids     uuid[] NOT NULL DEFAULT '{}',
    visit_ids     uuid[] NOT NULL DEFAULT '{}',

    status            text NOT NULL DEFAULT 'active',
    dismissed_reason  text,
    dismissed_at      timestamptz,

    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT finding_key UNIQUE (patient_id, key),

    CONSTRAINT finding_type_check CHECK (finding_type IN (
        'interaction',        -- a pair listed in interaction_rule
        'duplicate_therapy',  -- two drugs of the same ATC family
        'allergy_conflict',   -- a drug matching a recorded allergy
        'dosage_conflict',    -- one drug at differing strengths at once
        'lab_trend',          -- a measurement moving the wrong way
        'critical_value'      -- a single result far outside its range
    )),
    CONSTRAINT finding_severity_check CHECK (severity IN ('info', 'warning', 'danger')),
    CONSTRAINT finding_status_check CHECK (status IN ('active', 'dismissed')),
    CONSTRAINT finding_detail_source_check CHECK (
        detail_source IS NULL OR detail_source IN ('model', 'template')
    )
);

COMMENT ON TABLE  finding                  IS 'A problem found across the record. Recomputed whenever the timeline changes.';
COMMENT ON COLUMN finding.key              IS 'Fingerprint of what the finding is about, e.g. ''interaction:2541:6918''. Keeps re-runs idempotent.';
COMMENT ON COLUMN finding.severity         IS 'info | warning | danger. Drives the colour of the timeline dot.';
COMMENT ON COLUMN finding.detail           IS 'Plain-language explanation. Written once and never regenerated.';
COMMENT ON COLUMN finding.detail_source    IS '''template'' means the model was unavailable; the finding itself is unaffected.';
COMMENT ON COLUMN finding.evidence         IS 'What it was built from: matched rule, ATC codes, dates, overlap assumption.';
COMMENT ON COLUMN finding.event_ids        IS 'The timeline rows involved, so the user can see exactly what caused it.';
COMMENT ON COLUMN finding.visit_ids        IS 'The cards to mark. An interaction spans two visits and marks both.';
COMMENT ON COLUMN finding.status           IS '''dismissed'' rows are kept as tombstones so a waved-away warning cannot come back.';
COMMENT ON COLUMN finding.first_seen_at    IS 'When it was first detected. Unchanged by later re-runs.';

CREATE INDEX IF NOT EXISTS finding_patient_idx
    ON finding (patient_id, status, severity);
CREATE INDEX IF NOT EXISTS finding_visits_idx
    ON finding USING gin (visit_ids);
CREATE INDEX IF NOT EXISTS finding_events_idx
    ON finding USING gin (event_ids);

DROP TRIGGER IF EXISTS finding_set_updated_at ON finding;
CREATE TRIGGER finding_set_updated_at
    BEFORE UPDATE ON finding
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
