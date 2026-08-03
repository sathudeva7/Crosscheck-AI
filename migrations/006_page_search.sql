-- 006: searching the text of pages
--
-- Everything before this migration finds facts the extractor already pulled
-- out into structured rows. That covers drugs, doses and lab values, and misses
-- everything the extractor has no column for: the advice at the end of a
-- consultation note, the reason a drug was stopped, what the surgeon wrote
-- about the wound. Without this, the only way to reach that text is to read a
-- whole document and hope the sentence is in it.
--
-- Two indexes, because one kind of search is not enough here.
--
--   1. `search_vector` — Postgres full-text search. Splits the page into words,
--      reduces them to stems, and matches on meaning-of-word rather than exact
--      spelling: a search for 'prescribe' finds 'prescribed' and 'prescribing'.
--      This is the lane that answers questions.
--
--   2. a trigram index on the raw text — matches character runs instead of
--      words. This is the lane that finds 'Zoclar' inside 'Zoclar-500', finds
--      'diet' inside 'dietary' (which the stemmer, awkwardly, does not: it
--      turns 'dietary' into 'dietari'), and survives the one-character errors
--      that scanning a printed page produces.
--
-- Neither can match by meaning -- 'what should I eat' will not find 'avoid
-- fatty foods', because those sentences share no words at all. That gap is
-- what embeddings would close, and it is deliberately not closed here: this
-- migration costs one table rewrite, and the answer to most questions in a
-- record this size is a word the question already contains.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Generated rather than computed at query time, for one specific reason: a
-- full-text index built on an expression is only used when the query repeats
-- that expression exactly, down to the text search configuration. Get it
-- slightly wrong and Postgres silently falls back to reading every page --
-- correct answers, quietly, at a hundred times the cost. A stored column
-- cannot drift from its index.
ALTER TABLE document_page
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(transcription, ''))) STORED;

COMMENT ON COLUMN document_page.search_vector IS
    'Full-text index of transcription. Maintained by Postgres; never written directly.';

CREATE INDEX IF NOT EXISTS document_page_search_idx
    ON document_page USING gin (search_vector);

-- Note the config is 'english' while a page may be in any language. A Hindi or
-- Tamil page still gets indexed -- its words are tokenised and stored, so an
-- exact term matches -- it just gets no stemming and no stop-word removal.
-- Searching across languages needs a translation step at transcription time,
-- not a different index here.

CREATE INDEX IF NOT EXISTS document_page_trgm_idx
    ON document_page USING gin (transcription gin_trgm_ops);
