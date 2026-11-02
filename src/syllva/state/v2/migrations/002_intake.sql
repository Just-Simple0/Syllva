-- Intake v2 (implementation spec §11, design §4.4): one row per received file.
--
-- The state machine (§11.2) is enforced HERE, not only in Python: the allowed
-- (from, to) pairs live in a read-only reference table and a trigger rejects any
-- other change. PARTIAL never becomes READY (design §2 I3): the pair is absent.
-- A terminal-with-cause state always carries its cause code (handoff: no FAILED
-- without a reason).

CREATE TABLE intake_state_transitions (
  from_state TEXT NOT NULL,
  to_state   TEXT NOT NULL,
  PRIMARY KEY (from_state, to_state)
) WITHOUT ROWID;

INSERT INTO intake_state_transitions(from_state, to_state) VALUES
  ('RECEIVED', 'CLASSIFYING'),
  ('CLASSIFYING', 'NEEDS_REVIEW'),
  ('CLASSIFYING', 'AUTO_CONFIRMED'),
  ('CLASSIFYING', 'RECEIVED'),          -- crash recovery: an orphaned classification restarts
  ('NEEDS_REVIEW', 'CONFIRMED'),
  ('NEEDS_REVIEW', 'DISMISSED'),
  ('AUTO_CONFIRMED', 'PROCESSING'),
  ('AUTO_CONFIRMED', 'QUEUED'),
  ('CONFIRMED', 'PROCESSING'),
  ('CONFIRMED', 'QUEUED'),
  ('QUEUED', 'PROCESSING'),
  ('PROCESSING', 'READY'),
  ('PROCESSING', 'PARTIAL'),
  ('PROCESSING', 'FAILED'),
  ('PROCESSING', 'QUEUED'),             -- crash recovery (§11.2 "PROCESSING 고아는 QUEUED로")
  ('READY', 'QUEUED'),                  -- reprocess with a newer processor
  ('PARTIAL', 'QUEUED'),                -- reprocess; never straight to READY (I3)
  ('FAILED', 'QUEUED'),                 -- retry
  ('DUPLICATE', 'DISMISSED'),
  ('UNSUPPORTED', 'DISMISSED');

CREATE TRIGGER intake_state_transitions_ro_insert BEFORE INSERT ON intake_state_transitions
BEGIN SELECT RAISE(ABORT, 'READ_ONLY_TABLE'); END;
CREATE TRIGGER intake_state_transitions_ro_update BEFORE UPDATE ON intake_state_transitions
BEGIN SELECT RAISE(ABORT, 'READ_ONLY_TABLE'); END;
CREATE TRIGGER intake_state_transitions_ro_delete BEFORE DELETE ON intake_state_transitions
BEGIN SELECT RAISE(ABORT, 'READ_ONLY_TABLE'); END;

CREATE TABLE intake_candidates (
  id INTEGER PRIMARY KEY,
  candidate_id TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL CHECK (channel IN ('USER_DROP','CANVAS')),
  display_name TEXT NOT NULL CHECK (length(display_name) > 0),
  mime_type TEXT,
  size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
  source_version_id INTEGER REFERENCES source_versions(id),
  duplicate_of_source_id TEXT,
  state TEXT NOT NULL CHECK (state IN (
    'RECEIVED','CLASSIFYING','NEEDS_REVIEW','AUTO_CONFIRMED','CONFIRMED','QUEUED','PROCESSING',
    'READY','PARTIAL','FAILED','DUPLICATE','UNSUPPORTED','DISMISSED')),
  reason_code TEXT,
  reason_message TEXT,
  suggestion_json TEXT,
  material_id INTEGER REFERENCES materials(id),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (state NOT IN ('FAILED','UNSUPPORTED','DUPLICATE') OR reason_code IS NOT NULL),
  CHECK (state NOT IN ('UNSUPPORTED') OR source_version_id IS NULL),
  CHECK (state IN ('UNSUPPORTED') OR source_version_id IS NOT NULL),
  CHECK (state <> 'DUPLICATE' OR duplicate_of_source_id IS NOT NULL),
  CHECK (state NOT IN ('CONFIRMED','QUEUED','PROCESSING','READY','PARTIAL','FAILED') OR material_id IS NOT NULL)
);
CREATE INDEX idx_intake_candidates_state ON intake_candidates(state);
-- One live candidate per stored source version: a second drop of the same bytes is a DUPLICATE row.
CREATE UNIQUE INDEX idx_intake_candidates_version ON intake_candidates(source_version_id)
  WHERE source_version_id IS NOT NULL AND state NOT IN ('DUPLICATE','DISMISSED');
CREATE UNIQUE INDEX idx_intake_candidates_material ON intake_candidates(material_id) WHERE material_id IS NOT NULL;

CREATE TRIGGER intake_candidates_initial_state BEFORE INSERT ON intake_candidates
WHEN NEW.state NOT IN ('RECEIVED','DUPLICATE','UNSUPPORTED')
BEGIN SELECT RAISE(ABORT, 'INTAKE_INITIAL_STATE'); END;

CREATE TRIGGER intake_candidates_transition BEFORE UPDATE OF state ON intake_candidates
WHEN OLD.state <> NEW.state AND NOT EXISTS (
  SELECT 1 FROM intake_state_transitions WHERE from_state = OLD.state AND to_state = NEW.state)
BEGIN SELECT RAISE(ABORT, 'INTAKE_TRANSITION'); END;

-- The received bytes and the material a candidate became never change afterwards.
CREATE TRIGGER intake_candidates_identity BEFORE UPDATE OF candidate_id, channel, source_version_id, size_bytes ON intake_candidates
WHEN NEW.candidate_id IS NOT OLD.candidate_id OR NEW.channel IS NOT OLD.channel
  OR NEW.source_version_id IS NOT OLD.source_version_id OR NEW.size_bytes IS NOT OLD.size_bytes
BEGIN SELECT RAISE(ABORT, 'IDENTITY_IMMUTABLE'); END;
CREATE TRIGGER intake_candidates_material_once BEFORE UPDATE OF material_id ON intake_candidates
WHEN OLD.material_id IS NOT NULL AND NEW.material_id IS NOT OLD.material_id
BEGIN SELECT RAISE(ABORT, 'IDENTITY_IMMUTABLE'); END;

CREATE TRIGGER intake_candidates_no_delete BEFORE DELETE ON intake_candidates
BEGIN SELECT RAISE(ABORT, 'INTAKE_APPEND_ONLY'); END;
