-- Coach review: the coach edits and releases AI feedback before the learner sees it.
-- Run once against an existing deployed database (automatic initialization creates
-- missing tables but never adds columns to a table that already exists).
-- Nothing is deleted; existing learners keep today's behaviour (no gate).

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS review_required BOOLEAN;
UPDATE users SET review_required = FALSE WHERE review_required IS NULL;
ALTER TABLE users ALTER COLUMN review_required SET NOT NULL;

ALTER TABLE practice_attempts ADD COLUMN IF NOT EXISTS released_evaluation JSON;
ALTER TABLE practice_attempts ADD COLUMN IF NOT EXISTS released_score FLOAT;
ALTER TABLE practice_attempts ADD COLUMN IF NOT EXISTS coach_note TEXT;
ALTER TABLE practice_attempts ADD COLUMN IF NOT EXISTS released_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE practice_attempts ADD COLUMN IF NOT EXISTS released_by INTEGER;

-- Constraints are not IF NOT EXISTS in PostgreSQL; skip them if this is a re-run.
ALTER TABLE practice_attempts
    ADD CONSTRAINT practice_attempts_released_by_fkey FOREIGN KEY (released_by) REFERENCES users (id);
ALTER TABLE practice_attempts
    ADD CONSTRAINT valid_released_score
    CHECK (released_score IS NULL OR (released_score >= 0 AND released_score <= 10));

COMMIT;
