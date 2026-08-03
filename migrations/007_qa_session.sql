-- 007: qa_session, qa_message
--
-- Conversations with the assistant, kept server-side.
--
-- Until now the browser held the conversation and sent it back with every
-- question. That made the client the authority on what had been said, which is
-- the wrong place for it: a modified client could put words in the assistant's
-- mouth and have them replayed to the model as established context. History
-- lives here instead, and the server reads its own record.
--
-- An assistant message stores the whole apparatus around the answer, not just
-- the sentence -- confidence, band, citations, the ungrounded flag. Reopening a
-- conversation has to bring those back, or old answers appear bare next to new
-- ones and the reader is left to assume they were never checked.
--
-- What is *not* stored: the tool results themselves. Evidence is re-fetched on
-- every turn from the timeline, which is the live record. Freezing a copy here
-- would let a conversation keep quoting a document after it was deleted.

CREATE TABLE IF NOT EXISTS qa_session (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id uuid NOT NULL REFERENCES patient(id) ON DELETE CASCADE,

    title      text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  qa_session            IS 'One conversation with the assistant.';
COMMENT ON COLUMN qa_session.title      IS 'The opening question, shortened. Never model-written.';
COMMENT ON COLUMN qa_session.updated_at IS 'Last message time - what the conversation list sorts by.';

-- The list view is the only way in: a patient's conversations, newest activity
-- first. Ordering in the index keeps that a straight read.
CREATE INDEX IF NOT EXISTS qa_session_patient_idx
    ON qa_session (patient_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS qa_message (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES qa_session(id) ON DELETE CASCADE,

    role       text NOT NULL,
    content    text NOT NULL,

    -- Everything below describes how well an answer was supported, and is null
    -- on a user message. `confidence` is nullable even for an answer: one that
    -- correctly reports finding nothing rests on no extracted fact, so it has
    -- no score, and storing 0 would be a claim rather than an absence.
    confidence  real,
    band        text,
    basis       text,
    grounded    boolean,
    unsupported jsonb,
    citations   jsonb NOT NULL DEFAULT '[]'::jsonb,
    tool_calls  jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- An answer whose stream was cut off mid-sentence. Kept rather than
    -- discarded, because a visibly truncated answer is more honest than a
    -- question that appears to have been ignored.
    truncated  boolean NOT NULL DEFAULT false,

    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT qa_message_role_check
        CHECK (role IN ('user', 'assistant'))
);

COMMENT ON TABLE  qa_message             IS 'One message in a conversation, with the answer apparatus attached.';
COMMENT ON COLUMN qa_message.confidence  IS 'Weakest supporting fact, 0-1. Null when the answer rests on none.';
COMMENT ON COLUMN qa_message.band        IS 'high | medium | low | none.';
COMMENT ON COLUMN qa_message.grounded    IS 'False when the verifier could not trace every claim to evidence.';
COMMENT ON COLUMN qa_message.citations   IS 'The extracted rows the answer stood on, as returned at the time.';
COMMENT ON COLUMN qa_message.truncated   IS 'The reader disconnected before the answer finished.';

-- Replaying a conversation reads it whole, in order. Both the transcript and
-- the model's history come through this one index.
CREATE INDEX IF NOT EXISTS qa_message_session_idx
    ON qa_message (session_id, created_at);


DROP TRIGGER IF EXISTS qa_session_set_updated_at ON qa_session;
CREATE TRIGGER qa_session_set_updated_at
    BEFORE UPDATE ON qa_session
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
