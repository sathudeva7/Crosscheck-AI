-- 001: patient
--
-- The user *is* the patient in this app, so this table doubles as the account
-- record. clerk_user_id is the link to the auth provider.

CREATE TABLE IF NOT EXISTS patient (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_user_id text NOT NULL UNIQUE,
    email         text,
    full_name     text,
    dob           date,
    sex           text,
    description   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT patient_sex_check
        CHECK (sex IN ('male', 'female', 'other', 'unknown'))
);

COMMENT ON TABLE  patient               IS 'Account holder and subject of every uploaded medical document.';
COMMENT ON COLUMN patient.id            IS 'Surrogate key referenced by all patient-scoped tables.';
COMMENT ON COLUMN patient.clerk_user_id IS 'Clerk user id (''sub'' claim). Stubbed until JWT verification lands.';
COMMENT ON COLUMN patient.email         IS 'Contact email, mirrored from Clerk on first sign-in.';
COMMENT ON COLUMN patient.full_name     IS 'Used to sanity-check the patient name extracted from uploads.';
COMMENT ON COLUMN patient.dob           IS 'Date of birth. Lab reference ranges are age-dependent.';
COMMENT ON COLUMN patient.sex           IS 'male | female | other | unknown. Lab reference ranges are sex-dependent.';
COMMENT ON COLUMN patient.description   IS 'Free-text patient summary - background, chronic conditions, notes.';
COMMENT ON COLUMN patient.created_at    IS 'Row insert time.';
COMMENT ON COLUMN patient.updated_at    IS 'Maintained by the patient_set_updated_at trigger.';

-- Reused by every table that follows, so it lives in this first migration.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS patient_set_updated_at ON patient;
CREATE TRIGGER patient_set_updated_at
    BEFORE UPDATE ON patient
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
