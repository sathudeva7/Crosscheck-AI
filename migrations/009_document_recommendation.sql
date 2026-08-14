-- 009: which specialist a lab report points to
--
-- A lab report is the one document a patient holds without yet knowing what it
-- means. A prescription comes from a doctor who has already decided; results
-- come back, and the next step is finding someone to read them. This column
-- holds that answer for one document: which kinds of doctor usually review a
-- report like this, ranked, with a reason for each.
--
-- Stored rather than computed on demand because it is produced by a model with
-- a tool that reads the live channelling directories, which costs seconds and
-- several calls. Deciding once at upload — while the patient is already waiting
-- for extraction — and reading it back from here means opening the report later
-- is a database read.
--
-- Being stored has a second effect worth naming: the answer is frozen with the
-- document. The same report shows the same recommendation next month, which is
-- what makes it something a patient can act on and a clinician can query,
-- rather than a number that quietly changes between two people looking at the
-- same screen.
--
-- What it does NOT hold: any doctor, clinic or appointment. Those are fetched
-- live per search and never written down. This is a *kind* of doctor -- a
-- specialty name that came back from the directories' own catalogues -- so
-- searching for one is still a live question asked at the moment it is asked.
--
-- Null means the question has not been asked, or the model could not answer.
-- Both are shown as 'no recommendation available' rather than as an empty list,
-- because a report we failed to read is not a report with nothing to say.

ALTER TABLE document
    ADD COLUMN IF NOT EXISTS recommendation_json JSONB;

COMMENT ON COLUMN document.recommendation_json IS
    'Which kinds of doctor usually review this report, ranked, with reasons. '
    'Decided once at upload. Holds no doctor, clinic or appointment data.';
