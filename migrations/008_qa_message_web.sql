-- Published sources behind an attributed sentence in an answer.
--
-- Separate from `citations` on purpose. A citation is a fact read off this
-- patient's own document and carries the confidence that reading earned; this
-- is general drug-reference publishing, which says nothing about them and has
-- no such score. The screen shows them under two headings, and a transcript
-- reopened a week later has to be able to show the same two.
--
-- Defaulted rather than nullable so every row already written reads back as
-- 'no web sources' instead of null, and nothing downstream has to test for it.

ALTER TABLE qa_message
    ADD COLUMN IF NOT EXISTS web JSONB NOT NULL DEFAULT '[]'::jsonb;
