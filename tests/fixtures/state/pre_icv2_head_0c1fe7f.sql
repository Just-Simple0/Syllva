-- SQLite dump of a state store created by HEAD 0c1fe7f (before intake classification v2).
-- Seeds: intake-a/b/c; ASSIGN_COURSE receipt over a+b; applied FILE_DETAILS over c with a HUMAN plan.
BEGIN TRANSACTION;
CREATE TABLE checkpoints (
    provider         TEXT NOT NULL,
    scope            TEXT NOT NULL,
    checkpoint_value TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (provider, scope)
);
CREATE TABLE entity_allocations (
    course_key    TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    next_sequence INTEGER NOT NULL,
    PRIMARY KEY (course_key, entity_type)
);
CREATE TABLE entity_reservations (
    reservation_id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    entity_kind TEXT NOT NULL,
    entity_app_id TEXT NOT NULL,
    parent_folder_id TEXT NOT NULL,
    marker_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','APPLIED','RECONCILE_REQUIRED','RELEASED')),
    plan_revision TEXT NOT NULL,
    source_file_id TEXT,
    receipt_id TEXT,
    plan_hash TEXT,
    source_snapshot_hash TEXT,
    target_snapshot_hash TEXT,
    operation_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    released_at TEXT,
    UNIQUE(entity_kind, entity_app_id)
);
CREATE TABLE intake_items (
    intake_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_file_id TEXT NOT NULL,
    semester TEXT NOT NULL,
    original_parent_id TEXT NOT NULL,
    observed_parent_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    observed_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    course_candidates_json TEXT NOT NULL DEFAULT '[]',
    selected_course_key TEXT,
    selected_kind TEXT,
    file_intake_page_id TEXT,
    input_request_page_id TEXT,
    request_revision_hash TEXT,
    plan_revision TEXT,
    pending_request_key TEXT,
    canonical_entity_id TEXT,
    canonical_source_json TEXT,
    content_status TEXT NOT NULL DEFAULT 'Pending',
    last_error_code TEXT,
    last_error TEXT,
    last_successful_stage TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(provider, provider_file_id)
);
INSERT INTO "intake_items" VALUES('intake-a','google_drive','file-intake-a','2026-2','upload','upload','intake-a.pdf','application/pdf','md5:abc',1,'OBSERVED','UNKNOWN','[]',NULL,'TRANSCRIPT',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Pending',NULL,NULL,NULL,'2026-10-09T17:23:06.170994+00:00','2026-10-09T17:23:06.172767+00:00');
INSERT INTO "intake_items" VALUES('intake-b','google_drive','file-intake-b','2026-2','upload','upload','intake-b.pdf','application/pdf','md5:abc',1,'OBSERVED','UNKNOWN','[]',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Pending',NULL,NULL,NULL,'2026-10-09T17:23:06.171319+00:00','2026-10-09T17:23:06.171319+00:00');
INSERT INTO "intake_items" VALUES('intake-c','google_drive','file-intake-c','2026-2','upload','upload','intake-c.pdf','application/pdf','md5:abc',1,'OBSERVED','UNKNOWN','[]',NULL,'MATERIAL_PDF',NULL,NULL,NULL,'plan-c',NULL,NULL,NULL,'Pending',NULL,NULL,NULL,'2026-10-09T17:23:06.171556+00:00','2026-10-09T17:23:06.172528+00:00');
CREATE TABLE intake_observations (
    id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    source_hash TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(intake_id, source_hash, source_version)
);
CREATE TABLE intake_plans (
    plan_id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL REFERENCES intake_items(intake_id),
    request_revision_hash TEXT NOT NULL,
    plan_revision TEXT NOT NULL,
    resolved_workspace_fingerprint TEXT NOT NULL,
    target_snapshot_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    created_at TEXT NOT NULL
);
INSERT INTO "intake_plans" VALUES('plan_4e8d506f70364f2c8bea86816201113f','intake-c','rev2','plan-c','wf','{}','h','PLANNED','2026-10-09T17:23:06.172286+00:00');
CREATE TABLE intake_stage_events (
    event_id TEXT PRIMARY KEY,
    intake_id TEXT,
    operation_key TEXT,
    stage TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE jobs (
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
CREATE TABLE note_artifacts (
    artifact_id TEXT PRIMARY KEY,
    note_key TEXT NOT NULL REFERENCES note_jobs(note_key),
    output_identity TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    writer_version TEXT NOT NULL,
    ai_region_id TEXT,
    ai_block_ids_json TEXT NOT NULL DEFAULT '[]',
    last_publish_hash TEXT,
    state TEXT NOT NULL CHECK(state IN ('STAGED','VERIFIED','PUBLISHED','STALE')),
    created_at TEXT NOT NULL,
    verified_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(note_key, output_hash, manifest_hash)
);
CREATE TABLE note_attempts (
    note_key TEXT NOT NULL REFERENCES note_jobs(note_key),
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    state TEXT NOT NULL CHECK(state IN (
        'REQUESTED','WAITING_CONTEXT','GENERATING','STAGED','PUBLISHING',
        'READY','PARTIAL','FAILED','CANCELLED','STALE'
    )),
    seed_artifact_id TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    next_retry_at TEXT,
    last_successful_stage TEXT,
    error_class TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT,
    PRIMARY KEY(note_key, attempt_no)
);
CREATE TABLE note_jobs (
    note_key TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    evidence_manifest_hash TEXT NOT NULL,
    learner_request_hash TEXT NOT NULL,
    template_version TEXT NOT NULL,
    generator_config_version TEXT NOT NULL,
    current_attempt_no INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE note_request_references (
    reference_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    provider_request_id TEXT NOT NULL,
    note_key TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    head_generation INTEGER NOT NULL CHECK(head_generation >= 1),
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','CANCEL_REQUESTED','CANCELLED','SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY(note_key, attempt_no) REFERENCES note_attempts(note_key, attempt_no)
);
CREATE TABLE processing_records (
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
CREATE TABLE provider_write_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    operation_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    target_id TEXT,
    prewrite_committed_at TEXT NOT NULL,
    reservation_id TEXT,
    stage TEXT,
    dispatched_at TEXT,
    response_state TEXT NOT NULL,
    readback_json TEXT,
    error_class TEXT,
    pre_dispatch_snapshot_json TEXT
);
CREATE TABLE range_intent_claims (
    usage_slot_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    intent_generation INTEGER NOT NULL,
    identity_json TEXT NOT NULL,
    PRIMARY KEY(usage_slot_key, request_id),
    UNIQUE(usage_slot_key, receipt_id)
);
CREATE TABLE range_intent_heads (
    usage_slot_key TEXT PRIMARY KEY,
    session_app_id TEXT NOT NULL,
    material_app_id TEXT NOT NULL,
    usage_role TEXT NOT NULL,
    current_usage_app_id TEXT,
    current_request_id TEXT NOT NULL,
    current_receipt_id TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    current_proposal_id TEXT,
    current_target_entity_id TEXT,
    current_operation TEXT NOT NULL CHECK(current_operation IN ('create_usage', 'update_range')),
    intent_generation INTEGER NOT NULL CHECK(intent_generation >= 1),
    reservation_state TEXT NOT NULL DEFAULT 'NONE'
        CHECK(reservation_state IN ('NONE', 'DISPATCHED', 'RECONCILE_REQUIRED')),
    reserved_generation INTEGER,
    reserved_target_id TEXT,
    dispatch_attempt_no INTEGER NOT NULL DEFAULT 0,
    apply_lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
, current_usage_provider TEXT, current_usage_provider_row_id TEXT, slot_identity_json TEXT, active INTEGER NOT NULL DEFAULT 1, inactive_reason TEXT, input_mode TEXT);
CREATE TABLE range_outbox_invalidations (
 proposal_id TEXT PRIMARY KEY, usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL,
 reason TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE range_producer_actions (
 usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL, action_json TEXT NOT NULL,
 PRIMARY KEY(usage_slot_key,generation)
);
CREATE TABLE range_producer_attempts (
 token TEXT PRIMARY KEY, usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL,
 baseline_json TEXT NOT NULL, phase TEXT NOT NULL, outcome TEXT, readback_json TEXT
);
CREATE TABLE range_producer_intents (
 usage_slot_key TEXT NOT NULL, generation INTEGER NOT NULL, receipt_id TEXT NOT NULL,
 mode TEXT NOT NULL, candidate_id TEXT, expected_json TEXT, status TEXT NOT NULL,
 PRIMARY KEY (usage_slot_key,generation)
);
CREATE TABLE range_request_receipts (
 request_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE,
 source_id TEXT NOT NULL, workspace TEXT NOT NULL, usage_slot_key TEXT NOT NULL,
 generation INTEGER NOT NULL, user_hash TEXT NOT NULL, user_json TEXT NOT NULL,
 identity_json TEXT NOT NULL, observation_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE request_receipts (
    receipt_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    input_requests_data_source_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_revision_hash TEXT NOT NULL,
    provider_page_id TEXT,
    normalized_user_hash TEXT,
    target_snapshot_hash TEXT NOT NULL,
    submitted_at TEXT,
    plan_revision TEXT,
    state TEXT NOT NULL,
    workspace_fingerprint TEXT NOT NULL DEFAULT '',
    request_type TEXT,
    intake_ids_json TEXT,
    UNIQUE(provider, input_requests_data_source_id, request_key)
);
INSERT INTO "request_receipts" VALUES('receipt_14795ae79b5a412dbc138d3756c968e1','notion','ds','k-assign','rev',NULL,NULL,'t',NULL,NULL,'Draft','wf','ASSIGN_COURSE','["intake-a", "intake-b"]');
INSERT INTO "request_receipts" VALUES('receipt_26e193808a2b45b989e450d5a15ac185','notion','ds','k-details','rev2',NULL,NULL,'t',NULL,NULL,'Applied','wf','FILE_DETAILS','["intake-c"]');
CREATE TABLE schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
INSERT INTO "schema_migrations" VALUES('001_initial','2026-10-09T17:23:06.155116+00:00');
CREATE TABLE semester_registrations (
    semester TEXT PRIMARY KEY,
    config_fingerprint TEXT NOT NULL,
    workspace_fingerprint TEXT NOT NULL,
    drive_static_ids_json TEXT NOT NULL,
    notion_resolved_ids_json TEXT NOT NULL,
    provider_account_binding_id TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TABLE session_source_bindings (
    binding_id TEXT PRIMARY KEY,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_file_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(course_key, session_id),
    UNIQUE(provider, provider_file_id)
);
CREATE TABLE source_files (
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
CREATE TABLE source_versions (
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
CREATE TABLE study_note_heads (
    provider TEXT NOT NULL,
    session_provider_page_id TEXT NOT NULL,
    course_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    current_request_id TEXT NOT NULL,
    current_receipt_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 1),
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    inactive_reason TEXT,
    evidence_mode TEXT,
    selected_materials_json TEXT NOT NULL DEFAULT '[]',
    receipt_hash TEXT NOT NULL,
    current_note_key TEXT,
    current_attempt_no INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(provider, session_provider_page_id)
);
CREATE TABLE usage_apply_guards (
    invocation_token TEXT PRIMARY KEY,
    usage_slot_key TEXT NOT NULL,
    generation INTEGER NOT NULL,
    proposal_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('HELD','MUTATING','RESOLVED','RELEASED')),
    baseline_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    baseline_mode TEXT,
    readback_json TEXT,
    outcome TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE usage_proposal_outbox (
    proposal_id TEXT PRIMARY KEY,
    usage_slot_key TEXT NOT NULL REFERENCES range_intent_heads(usage_slot_key),
    request_id TEXT NOT NULL,
    intent_generation INTEGER NOT NULL CHECK(intent_generation >= 1),
    action_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    publish_state TEXT NOT NULL DEFAULT 'PREPARED'
        CHECK(publish_state IN ('PREPARED', 'PUBLISHED', 'RECONCILE_REQUIRED')),
    queue_page_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(usage_slot_key, intent_generation)
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_course ON jobs(course_key);
CREATE INDEX idx_intake_items_status ON intake_items(status);
CREATE INDEX idx_intake_items_semester ON intake_items(semester);
CREATE INDEX idx_request_receipts_page ON request_receipts(provider_page_id);
CREATE UNIQUE INDEX idx_entity_reservations_active_intake_kind
    ON entity_reservations(intake_id, entity_kind)
    WHERE state <> 'RELEASED';
CREATE INDEX idx_intake_stage_events_intake ON intake_stage_events(intake_id, event_id);
CREATE UNIQUE INDEX usage_apply_guard_owner
    ON usage_apply_guards(usage_slot_key) WHERE phase != 'RELEASED';
CREATE UNIQUE INDEX idx_note_attempts_one_active
    ON note_attempts(note_key)
    WHERE state IN ('REQUESTED','WAITING_CONTEXT','GENERATING','STAGED','PUBLISHING');
CREATE INDEX idx_note_request_references_attempt
    ON note_request_references(note_key, attempt_no, state);
CREATE UNIQUE INDEX idx_note_artifacts_one_reusable
    ON note_artifacts(note_key)
    WHERE state IN ('VERIFIED','PUBLISHED');
CREATE UNIQUE INDEX range_one_producer_owner ON range_producer_attempts(usage_slot_key)
 WHERE phase != 'RELEASED';
COMMIT;
