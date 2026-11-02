-- Syllva v2 initial schema (implementation spec §3.7, design §4).
-- Applied by syllva.state.v2.migrate inside one transaction. No data is imported
-- from v1.2 (user decision 2026-11-02).
--
-- Ownership (design §2 I2, spec §5.1):
--   SOURCE: blobs, blob_refs, source_files, source_versions, derivatives
--   AI:     ai_artifacts, artifact_bases, artifact_citations
--   USER:   user_notes, user_edits
--   catalog (structure, written by ingester or the user, never by agent output):
--           courses, course_aliases, sessions, materials, material_usage, activities, exams
-- Human-only columns (design §2 I4) are guarded here as the last line of defence:
-- CHECK constraints require a gate log reference, and triggers require that the
-- referenced human_gate_log row matches the action and target and is used once.

-- Human-Proof audit log first: academic tables reference it (spec §3.7 FK order).
CREATE TABLE human_gate_log (
  id            INTEGER PRIMARY KEY,
  action        TEXT NOT NULL CHECK (action IN ('VERIFY_MATERIAL','CONFIRM_EXAM_SCOPE','SET_SUBMISSION_STATUS','ADOPT_AI_ARTIFACT')),
  target_kind   TEXT NOT NULL CHECK (target_kind IN ('material','exam','activity','ai_artifact')),
  target_id     TEXT NOT NULL,
  request_hash  TEXT NOT NULL CHECK (length(request_hash) = 64),
  nonce_id      TEXT NOT NULL UNIQUE,
  confirmed_at  TEXT NOT NULL,
  shell_version TEXT NOT NULL
);
CREATE TABLE human_gate_uses (
  gate_id INTEGER PRIMARY KEY REFERENCES human_gate_log(id),
  used_by TEXT NOT NULL
);
-- The audit trail is append-only.
CREATE TRIGGER human_gate_log_no_update BEFORE UPDATE ON human_gate_log
BEGIN SELECT RAISE(ABORT, 'GATE_LOG_APPEND_ONLY'); END;
CREATE TRIGGER human_gate_log_no_delete BEFORE DELETE ON human_gate_log
BEGIN SELECT RAISE(ABORT, 'GATE_LOG_APPEND_ONLY'); END;
CREATE TRIGGER human_gate_uses_no_update BEFORE UPDATE ON human_gate_uses
BEGIN SELECT RAISE(ABORT, 'GATE_LOG_APPEND_ONLY'); END;
CREATE TRIGGER human_gate_uses_no_delete BEFORE DELETE ON human_gate_uses
BEGIN SELECT RAISE(ABORT, 'GATE_LOG_APPEND_ONLY'); END;

-- SOURCE: originals ------------------------------------------------------------
CREATE TABLE blobs (
  sha256        TEXT PRIMARY KEY CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),
  created_at    TEXT NOT NULL
);
CREATE TABLE blob_refs (
  sha256     TEXT NOT NULL REFERENCES blobs(sha256),
  owner_kind TEXT NOT NULL CHECK (owner_kind IN ('source_version','derivative','ai_artifact','user_note','user_edit')),
  owner_id   INTEGER NOT NULL,
  PRIMARY KEY (sha256, owner_kind, owner_id)
);
CREATE TABLE source_files (
  id              INTEGER PRIMARY KEY,
  source_id       TEXT NOT NULL UNIQUE,
  provider        TEXT NOT NULL CHECK (provider IN ('local','canvas','github')),
  display_name    TEXT NOT NULL,
  mime_type       TEXT,
  origin          TEXT NOT NULL CHECK (origin IN ('USER_DROP','CANVAS','GITHUB')),
  deleted_at      TEXT,
  created_at      TEXT NOT NULL
);
CREATE TABLE source_versions (
  id              INTEGER PRIMARY KEY,
  source_file_id  INTEGER NOT NULL REFERENCES source_files(id),
  source_version  INTEGER NOT NULL CHECK (source_version >= 1),
  source_hash     TEXT NOT NULL CHECK (source_hash GLOB 'sha256:*' AND length(source_hash) = 71),
  blob_sha256     TEXT NOT NULL REFERENCES blobs(sha256),
  supersedes_id   INTEGER REFERENCES source_versions(id),
  created_at      TEXT NOT NULL,
  UNIQUE (source_file_id, source_version),
  UNIQUE (source_file_id, source_hash)
);

-- catalog: academic entities ---------------------------------------------------
CREATE TABLE courses (
  id INTEGER PRIMARY KEY,
  course_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  code TEXT,
  semester TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE course_aliases (
  course_id INTEGER NOT NULL REFERENCES courses(id),
  alias_nfc TEXT NOT NULL,
  PRIMARY KEY (course_id, alias_nfc)
);
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL UNIQUE,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  number INTEGER,
  held_on TEXT,
  title TEXT,
  created_at TEXT NOT NULL
);
-- processing_status uses the existing domain ProcessingStatus values (spec §4.4 "유지").
CREATE TABLE materials (
  id INTEGER PRIMARY KEY,
  material_id TEXT NOT NULL UNIQUE,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  session_id INTEGER REFERENCES sessions(id),
  source_file_id INTEGER NOT NULL REFERENCES source_files(id),
  kind TEXT NOT NULL,
  role TEXT NOT NULL,
  processing_status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (processing_status IN ('PENDING','PROCESSING','READY','PARTIAL','NEEDS_REVIEW','FAILED')),
  created_at TEXT NOT NULL
);
-- Design §2 I3: PARTIAL never silently becomes READY. The only way out of PARTIAL
-- is back through PROCESSING (reprocessing), FAILED, or NEEDS_REVIEW.
CREATE TRIGGER materials_partial_not_ready BEFORE UPDATE OF processing_status ON materials
WHEN OLD.processing_status = 'PARTIAL' AND NEW.processing_status = 'READY'
BEGIN SELECT RAISE(ABORT, 'PARTIAL_TO_READY'); END;

CREATE TABLE material_usage (
  material_id INTEGER PRIMARY KEY REFERENCES materials(id),
  verified    INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  verified_gate_id INTEGER REFERENCES human_gate_log(id),
  CHECK (verified = 0 OR verified_gate_id IS NOT NULL),
  CHECK (verified = 1 OR verified_gate_id IS NULL)
);
CREATE TRIGGER material_usage_gate_insert BEFORE INSERT ON material_usage
WHEN NEW.verified = 1
BEGIN
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g JOIN materials m ON m.id = NEW.material_id
    WHERE g.id = NEW.verified_gate_id AND g.action = 'VERIFY_MATERIAL'
      AND g.target_kind = 'material' AND g.target_id = m.material_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.verified_gate_id, 'material_usage:' || NEW.material_id);
END;
CREATE TRIGGER material_usage_gate_update BEFORE UPDATE OF verified, verified_gate_id ON material_usage
WHEN NEW.verified = 1 AND (OLD.verified = 0 OR NEW.verified_gate_id IS NOT OLD.verified_gate_id)
BEGIN
  SELECT RAISE(ABORT, 'GATE_REQUIRED') WHERE NEW.verified_gate_id IS NULL;
  SELECT RAISE(ABORT, 'GATE_REUSED') WHERE NEW.verified_gate_id IS OLD.verified_gate_id;
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g JOIN materials m ON m.id = NEW.material_id
    WHERE g.id = NEW.verified_gate_id AND g.action = 'VERIFY_MATERIAL'
      AND g.target_kind = 'material' AND g.target_id = m.material_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.verified_gate_id, 'material_usage:' || NEW.material_id);
END;

CREATE TABLE activities (
  id INTEGER PRIMARY KEY,
  activity_id TEXT NOT NULL UNIQUE,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  title TEXT NOT NULL,
  due_at TEXT,
  submission_status TEXT NOT NULL DEFAULT 'NOT_SUBMITTED'
    CHECK (submission_status IN ('NOT_SUBMITTED','SUBMITTED','LATE_SUBMITTED','EXCUSED')),
  submission_gate_id INTEGER REFERENCES human_gate_log(id),
  created_at TEXT NOT NULL,
  CHECK (submission_status = 'NOT_SUBMITTED' OR submission_gate_id IS NOT NULL)
);
CREATE TRIGGER activities_gate_insert BEFORE INSERT ON activities
WHEN NEW.submission_status <> 'NOT_SUBMITTED' OR NEW.submission_gate_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g
    WHERE g.id = NEW.submission_gate_id AND g.action = 'SET_SUBMISSION_STATUS'
      AND g.target_kind = 'activity' AND g.target_id = NEW.activity_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.submission_gate_id, 'activities:' || NEW.activity_id);
END;
-- Every change of submission status (including back to NOT_SUBMITTED) is a human action
-- and needs a new gate log row.
CREATE TRIGGER activities_gate_update BEFORE UPDATE OF submission_status, submission_gate_id ON activities
WHEN NEW.submission_status IS NOT OLD.submission_status OR NEW.submission_gate_id IS NOT OLD.submission_gate_id
BEGIN
  SELECT RAISE(ABORT, 'GATE_REQUIRED') WHERE NEW.submission_gate_id IS NULL;
  SELECT RAISE(ABORT, 'GATE_REUSED') WHERE NEW.submission_gate_id IS OLD.submission_gate_id;
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g
    WHERE g.id = NEW.submission_gate_id AND g.action = 'SET_SUBMISSION_STATUS'
      AND g.target_kind = 'activity' AND g.target_id = NEW.activity_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.submission_gate_id, 'activities:' || NEW.activity_id);
END;

CREATE TABLE exams (
  id INTEGER PRIMARY KEY,
  exam_id TEXT NOT NULL UNIQUE,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  held_on TEXT,
  scope_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (scope_confirmed IN (0,1)),
  scope_gate_id INTEGER REFERENCES human_gate_log(id),
  created_at TEXT NOT NULL,
  CHECK (scope_confirmed = 0 OR scope_gate_id IS NOT NULL),
  CHECK (scope_confirmed = 1 OR scope_gate_id IS NULL)
);
CREATE TRIGGER exams_gate_insert BEFORE INSERT ON exams
WHEN NEW.scope_confirmed = 1
BEGIN
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g
    WHERE g.id = NEW.scope_gate_id AND g.action = 'CONFIRM_EXAM_SCOPE'
      AND g.target_kind = 'exam' AND g.target_id = NEW.exam_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.scope_gate_id, 'exams:' || NEW.exam_id);
END;
CREATE TRIGGER exams_gate_update BEFORE UPDATE OF scope_confirmed, scope_gate_id ON exams
WHEN NEW.scope_confirmed = 1 AND (OLD.scope_confirmed = 0 OR NEW.scope_gate_id IS NOT OLD.scope_gate_id)
BEGIN
  SELECT RAISE(ABORT, 'GATE_REQUIRED') WHERE NEW.scope_gate_id IS NULL;
  SELECT RAISE(ABORT, 'GATE_REUSED') WHERE NEW.scope_gate_id IS OLD.scope_gate_id;
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g
    WHERE g.id = NEW.scope_gate_id AND g.action = 'CONFIRM_EXAM_SCOPE'
      AND g.target_kind = 'exam' AND g.target_id = NEW.exam_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.scope_gate_id, 'exams:' || NEW.exam_id);
END;

-- SOURCE: derivatives ----------------------------------------------------------
-- kind is restricted to deterministic derivatives: a summary is never a
-- normalization output (design §2 I3); summaries live in ai_artifacts.
CREATE TABLE derivatives (
  id INTEGER PRIMARY KEY,
  source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
  kind TEXT NOT NULL CHECK (kind IN ('normalized','chunks')),
  schema_version TEXT NOT NULL,
  processor_version TEXT NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  completeness TEXT NOT NULL CHECK (completeness IN ('COMPLETE','PARTIAL')),
  created_at TEXT NOT NULL,
  UNIQUE (source_version_id, kind, schema_version, processor_version)
);
CREATE TRIGGER derivatives_partial_not_complete BEFORE UPDATE OF completeness ON derivatives
WHEN OLD.completeness = 'PARTIAL' AND NEW.completeness = 'COMPLETE'
BEGIN SELECT RAISE(ABORT, 'PARTIAL_TO_COMPLETE'); END;

-- processing (v1.2-IS §8.1, §8.4-§8.6 meaning kept; no provider file ids) -----
CREATE TABLE jobs (
  id               TEXT PRIMARY KEY,
  job_key          TEXT NOT NULL UNIQUE,
  operation        TEXT NOT NULL,
  stage            TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('PENDING','PROCESSING','READY','PARTIAL','NEEDS_REVIEW','FAILED')),
  course_key       TEXT,
  source_id        TEXT,
  source_hash      TEXT,
  target_entity_id TEXT,
  attempt_count    INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  error_class      TEXT,
  error_code       TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  completed_at     TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_course ON jobs(course_key);
CREATE TABLE processing_records (
  id                TEXT PRIMARY KEY,
  job_id            TEXT NOT NULL REFERENCES jobs(id),
  operation         TEXT NOT NULL,
  processor_version TEXT NOT NULL,
  input_hash        TEXT,
  output_derivative_id INTEGER REFERENCES derivatives(id),
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  status            TEXT NOT NULL CHECK (status IN ('PENDING','PROCESSING','READY','PARTIAL','NEEDS_REVIEW','FAILED'))
);
CREATE TABLE checkpoints (
  scope            TEXT PRIMARY KEY,
  checkpoint_value TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE TABLE entity_allocations (
  course_key    TEXT NOT NULL,
  entity_type   TEXT NOT NULL,
  next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1),
  PRIMARY KEY (course_key, entity_type)
);

-- AI ---------------------------------------------------------------------------
CREATE TABLE ai_artifacts (
  id INTEGER PRIMARY KEY,
  artifact_id TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT 'AI' CHECK (owner = 'AI'),
  kind TEXT NOT NULL CHECK (kind IN ('summary','question','answer','grading','classification_hint')),
  based_on_source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
  based_on_source_hash TEXT NOT NULL,
  processor_version TEXT NOT NULL CHECK (length(processor_version) > 0),
  agent_adapter TEXT NOT NULL CHECK (length(agent_adapter) > 0),
  prompt_template_version TEXT NOT NULL CHECK (length(prompt_template_version) > 0),
  body_blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  citation_status TEXT NOT NULL CHECK (citation_status IN ('CITED','PARTIAL_CITED','UNCITED')),
  created_at TEXT NOT NULL
);
CREATE TABLE artifact_bases (
  artifact_id INTEGER NOT NULL REFERENCES ai_artifacts(id),
  source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
  source_hash TEXT NOT NULL,
  PRIMARY KEY (artifact_id, source_version_id)
);
CREATE TABLE artifact_citations (
  artifact_id INTEGER NOT NULL REFERENCES ai_artifacts(id),
  ordinal INTEGER NOT NULL,
  locator TEXT NOT NULL,
  source_version_id INTEGER NOT NULL REFERENCES source_versions(id),
  PRIMARY KEY (artifact_id, ordinal)
);
-- AI output is immutable once stored; it is never relabelled or rewritten.
CREATE TRIGGER ai_artifacts_immutable BEFORE UPDATE ON ai_artifacts
BEGIN SELECT RAISE(ABORT, 'AI_ARTIFACT_IMMUTABLE'); END;

-- USER -------------------------------------------------------------------------
CREATE TABLE user_notes (
  id INTEGER PRIMARY KEY,
  note_id TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT 'USER' CHECK (owner = 'USER'),
  course_id INTEGER REFERENCES courses(id),
  body_blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  adopted_from_artifact_id INTEGER REFERENCES ai_artifacts(id),
  adopt_gate_id INTEGER REFERENCES human_gate_log(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (adopted_from_artifact_id IS NULL OR adopt_gate_id IS NOT NULL),
  CHECK (adopted_from_artifact_id IS NOT NULL OR adopt_gate_id IS NULL)
);
CREATE TRIGGER user_notes_gate_insert BEFORE INSERT ON user_notes
WHEN NEW.adopted_from_artifact_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'GATE_MISMATCH') WHERE NOT EXISTS (
    SELECT 1 FROM human_gate_log g JOIN ai_artifacts a ON a.id = NEW.adopted_from_artifact_id
    WHERE g.id = NEW.adopt_gate_id AND g.action = 'ADOPT_AI_ARTIFACT'
      AND g.target_kind = 'ai_artifact' AND g.target_id = a.artifact_id);
  INSERT INTO human_gate_uses(gate_id, used_by) VALUES (NEW.adopt_gate_id, 'user_notes:' || NEW.note_id);
END;
-- Adoption provenance cannot be changed after the fact.
CREATE TRIGGER user_notes_adoption_immutable BEFORE UPDATE OF adopted_from_artifact_id, adopt_gate_id ON user_notes
WHEN NEW.adopted_from_artifact_id IS NOT OLD.adopted_from_artifact_id OR NEW.adopt_gate_id IS NOT OLD.adopt_gate_id
BEGIN SELECT RAISE(ABORT, 'ADOPTION_IMMUTABLE'); END;
CREATE TABLE user_edits (
  id INTEGER PRIMARY KEY,
  record_kind TEXT NOT NULL,
  record_id INTEGER NOT NULL,
  previous_blob_sha256 TEXT REFERENCES blobs(sha256),
  edited_at TEXT NOT NULL
);
