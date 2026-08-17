-- What each answer cost to produce.
--
-- One question is three to eight model calls: a lookup decision per gathering
-- round, then composing, then verifying. None of that is visible from the
-- answer, and the gathering rounds replay every tool result gathered so far,
-- so the expensive questions are not the ones that look expensive.
--
-- Columns rather than one JSON blob because the questions asked of this are
-- aggregates — spend per patient, per day, per model — and those want a SUM
-- over a column, not a cast out of JSONB on every row.
--
-- All nullable, and null means 'not known' rather than zero: a user message
-- never had a cost, and every answer written before this migration ran has no
-- honest number to report. Defaulting them to 0 would put those rows into the
-- totals as free answers, which they were not.

ALTER TABLE qa_message
    ADD COLUMN IF NOT EXISTS cost_model    TEXT,
    ADD COLUMN IF NOT EXISTS cost_calls    INTEGER,
    ADD COLUMN IF NOT EXISTS input_tokens  INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    -- Numeric, not double precision. Fractions of a cent summed over a few
    -- thousand questions is where binary floating point starts to embarrass
    -- itself, and this figure may one day be shown to whoever pays it.
    ADD COLUMN IF NOT EXISTS cost_usd      NUMERIC(12, 6),
    -- Spend per graph node. Diagnostic, never summed across patients, so JSONB
    -- is the right shape for it.
    ADD COLUMN IF NOT EXISTS cost_by_node  JSONB;

-- Spend-over-time is the query this table will actually be asked for. Partial,
-- because only assistant rows ever carry a cost and they are half the table.
CREATE INDEX IF NOT EXISTS qa_message_cost_idx
    ON qa_message (created_at)
    WHERE cost_usd IS NOT NULL;
