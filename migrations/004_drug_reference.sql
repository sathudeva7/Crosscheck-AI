-- 004: drug + drug_alias + interaction_rule
--
-- Reference data, not patient data. Nothing here belongs to anyone: it is the
-- shared knowledge every patient's safety checks are run against.
--
--   drug              an active ingredient, its RxNorm id and its WHO classes
--   drug_alias        a printed name -> that ingredient
--   interaction_rule  a pair of ingredients known to interact
--
-- Everything is keyed on rxcui rather than on names, and that is the whole
-- point. DDInter calls aspirin 'Acetylsalicylic acid' and paracetamol
-- 'Acetaminophen'; RxNorm and most prescriptions do not. Matching by name
-- would silently miss warfarin + aspirin, the best known interaction there is,
-- and show a clean result. Both spellings map to rxcui 1191, and numbers have
-- no synonyms.
--
-- interaction_rule is seeded from DDInter 2.0 (ddinter.scbdd.com), which is
-- free for academic use. RxNav's own interaction API was withdrawn by the NLM
-- and now returns 404, so an external source is required rather than optional.

CREATE TABLE IF NOT EXISTS drug (
    rxcui         text PRIMARY KEY,
    name          text NOT NULL,
    atc_codes     text[] NOT NULL DEFAULT '{}',
    atc_names     text[] NOT NULL DEFAULT '{}',

    -- where the identity came from, so a weak one can be found later
    source        text NOT NULL DEFAULT 'rxnorm',

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT drug_source_check CHECK (source IN (
        'rxnorm',          -- the name is itself an RxNorm concept
        'model_verified',  -- a model named it and RxNorm confirmed it exists
        'user'             -- typed in by a person during review
    ))
);

COMMENT ON TABLE  drug            IS 'One active ingredient. Reference data shared by every patient.';
COMMENT ON COLUMN drug.rxcui      IS 'RxNorm ingredient id. The key everything else joins on.';
COMMENT ON COLUMN drug.name       IS 'Ingredient name, e.g. ''metoprolol''.';
COMMENT ON COLUMN drug.atc_codes  IS 'WHO classes. Sharing the first 3 characters means the same drug family.';

CREATE INDEX IF NOT EXISTS drug_name_idx ON drug (lower(name));
CREATE INDEX IF NOT EXISTS drug_atc_idx ON drug USING gin (atc_codes);

DROP TRIGGER IF EXISTS drug_set_updated_at ON drug;
CREATE TRIGGER drug_set_updated_at
    BEFORE UPDATE ON drug
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


CREATE TABLE IF NOT EXISTS drug_alias (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- the printed name, lowercased and stripped of form and strength, so
    -- 'TAB. Betaloc 100mg' and 'betaloc' are one entry
    alias         text NOT NULL,
    rxcui         text REFERENCES drug(rxcui) ON DELETE CASCADE,

    source        text NOT NULL DEFAULT 'model_verified',
    confidence    numeric,

    -- names we looked up and could not identify are recorded too, so the same
    -- dead end is not paid for again on every upload
    unresolved    boolean NOT NULL DEFAULT false,
    reason        text,

    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT drug_alias_key UNIQUE (alias),
    CONSTRAINT drug_alias_source_check CHECK (source IN (
        'rxnorm', 'model_verified', 'user'
    )),
    CONSTRAINT drug_alias_resolved_check CHECK (
        (unresolved AND rxcui IS NULL) OR (NOT unresolved AND rxcui IS NOT NULL)
    )
);

COMMENT ON TABLE  drug_alias            IS 'Printed drug name -> ingredient. Grows as documents are read and corrections made.';
COMMENT ON COLUMN drug_alias.alias      IS 'Lowercased printed name with form and strength removed.';
COMMENT ON COLUMN drug_alias.source     IS '''user'' aliases come from corrections and outrank the others.';
COMMENT ON COLUMN drug_alias.unresolved IS 'True when the name could not be identified. Cached so the same lookup is not repeated.';

CREATE INDEX IF NOT EXISTS drug_alias_rxcui_idx ON drug_alias (rxcui);


CREATE TABLE IF NOT EXISTS interaction_rule (
    rxcui_a       text NOT NULL,
    rxcui_b       text NOT NULL,

    severity      text NOT NULL,
    name_a        text NOT NULL,
    name_b        text NOT NULL,
    source        text NOT NULL DEFAULT 'ddinter',
    description   text,

    created_at    timestamptz NOT NULL DEFAULT now(),

    -- the smaller id is always first, so a lookup is one comparison rather
    -- than two
    CONSTRAINT interaction_rule_pkey PRIMARY KEY (rxcui_a, rxcui_b),
    CONSTRAINT interaction_rule_order CHECK (rxcui_a < rxcui_b),
    CONSTRAINT interaction_rule_severity_check CHECK (severity IN (
        'Major', 'Moderate', 'Minor',
        'Unknown'   -- the pair is known to interact but the source does not grade it
    ))
);

COMMENT ON TABLE  interaction_rule             IS 'Known drug-drug interactions, keyed by ingredient id rather than by name.';
COMMENT ON COLUMN interaction_rule.rxcui_a     IS 'Always the smaller of the pair, so lookups need not try both orders.';
COMMENT ON COLUMN interaction_rule.severity    IS '''Unknown'' means the pair is listed but ungraded - report it as unrated, never as safe.';
COMMENT ON COLUMN interaction_rule.name_a      IS 'The source''s own name for the drug, shown so a warning can cite its wording.';

CREATE INDEX IF NOT EXISTS interaction_rule_b_idx ON interaction_rule (rxcui_b);
CREATE INDEX IF NOT EXISTS interaction_rule_severity_idx ON interaction_rule (severity);
