-- ULS v1.2 — SQLite initial schema (spec §8)
-- Durable orchestration state only. No canonical academic source bodies.
-- Migrations must be repeatable/idempotent (spec §42 acceptance).

PRAGMA foreign_keys = ON;

-- Applied-migration bookkeeping (spec §8 required table: schema_migrations)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

-- §8.1 jobs
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    job_key          TEXT UNIQUE NOT NULL,           -- §8.1.1 deterministic duplicate guard
    operation        TEXT NOT NULL,
    stage            TEXT NOT NULL,
    status           TEXT NOT NULL,                  -- PENDING|PROCESSING|READY|PARTIAL|NEEDS_REVIEW|FAILED
    course_key       TEXT,
    source_file_id   TEXT,
    source_hash      TEXT,
    target_entity_id TEXT,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    error_class      TEXT,
    last_error       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    completed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_course ON jobs(course_key);

-- §8.2 source_files
CREATE TABLE IF NOT EXISTS source_files (
    source_file_id     TEXT PRIMARY KEY,
    provider           TEXT NOT NULL,
    provider_file_id   TEXT NOT NULL,
    course_key         TEXT NOT NULL,
    source_kind        TEXT NOT NULL,
    original_filename  TEXT,
    current_hash       TEXT,
    canonical_entity_id TEXT,                        -- §8.6.1 source-bound idempotent allocation
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    UNIQUE (provider, provider_file_id)
);

-- §8.3 source_versions
CREATE TABLE IF NOT EXISTS source_versions (
    id                  TEXT PRIMARY KEY,
    source_file_id      TEXT NOT NULL,
    source_hash         TEXT NOT NULL,
    version             INTEGER NOT NULL,
    canonical_entity_id TEXT NOT NULL,
    source_ref_json     TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    processor_version   TEXT,
    UNIQUE (source_file_id, source_hash),
    UNIQUE (source_file_id, version),
    FOREIGN KEY (source_file_id) REFERENCES source_files(source_file_id)
);

-- §8.4 processing_records
CREATE TABLE IF NOT EXISTS processing_records (
    id                TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL,
    operation         TEXT NOT NULL,
    processor_version TEXT NOT NULL,
    input_hash        TEXT,
    output_ref_json   TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

-- §8.5 checkpoints
CREATE TABLE IF NOT EXISTS checkpoints (
    provider         TEXT NOT NULL,
    scope            TEXT NOT NULL,
    checkpoint_value TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (provider, scope)
);

-- §8.6 entity_allocations
CREATE TABLE IF NOT EXISTS entity_allocations (
    course_key    TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    next_sequence INTEGER NOT NULL,
    PRIMARY KEY (course_key, entity_type)
);
