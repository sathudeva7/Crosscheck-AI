-- Questions that were refused rather than answered.
--
-- A blocked turn is still a turn. It has to appear in the transcript on reload,
-- or a reader who typed something odd sees their question sitting there with
-- nothing after it and reasonably concludes the app is broken. So the refusal
-- is stored as an ordinary assistant message and these two columns say what it
-- was.
--
-- `blocked` is NOT NULL DEFAULT FALSE, matching `truncated`: every existing row
-- was answered normally, and that is a fact about them rather than a gap. It is
-- also asked as a boolean by the screen on every message, and a nullable
-- boolean would make the client test for three states to render two.
--
-- `block_reason` is the technique the guard named — 'instruction override',
-- 'role reassignment'. Nullable, because a row that was not blocked has no
-- reason to give, and it is never sent to the browser: naming the recognised
-- phrasing is free feedback on the next attempt. It is here so that a month of
-- attempts can be counted and grouped, which is what tuning the guard needs.

ALTER TABLE qa_message
    ADD COLUMN IF NOT EXISTS blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS block_reason TEXT;

-- Partial, because blocked rows are a rounding error in this table and the only
-- query anyone runs against them is 'show me the attempts'.
CREATE INDEX IF NOT EXISTS qa_message_blocked_idx
    ON qa_message (created_at)
    WHERE blocked;
