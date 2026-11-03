-- R3-2 (R3 plan §6.2, §6.3): Canvas collection runs, collected items, and the
-- Canvas side of activities. Nothing here holds a URL, a download verifier or HTML.

-- One row per collection attempt of one Canvas course. A row still RUNNING at
-- start-up was interrupted (FAILED/INTERRUPTED). Only a COMPLETE run writes items.
CREATE TABLE canvas_collections (
  id INTEGER PRIMARY KEY,
  canvas_course_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'INCOMPLETE', 'FAILED')),
  reason TEXT,
  items INTEGER,
  CHECK ((status = 'RUNNING') = (finished_at IS NULL)),
  CHECK (status IN ('RUNNING', 'COMPLETE') OR reason IS NOT NULL)
);
CREATE INDEX canvas_collections_course ON canvas_collections(canvas_course_id, id);

-- What Canvas showed in the last COMPLETE collection (a mirror, not a source version:
-- no AI output or citation points at these rows). Items missing from a later COMPLETE
-- collection become active = 0; rows are never deleted.
CREATE TABLE canvas_items (
  id INTEGER PRIMARY KEY,
  canvas_course_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ASSIGNMENT', 'ANNOUNCEMENT', 'MODULE_ITEM')),
  resource_id TEXT NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 64 AND resource_id NOT GLOB '*[^0-9]*'),
  title TEXT NOT NULL,
  module_name TEXT,
  week INTEGER CHECK (week IS NULL OR week >= 1),
  item_type TEXT,
  due_at TEXT,
  canvas_updated_at TEXT,
  attachment_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(attachment_json) AND json_type(attachment_json) = 'array'),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE (canvas_course_id, kind, resource_id)
);
CREATE TRIGGER canvas_items_no_delete BEFORE DELETE ON canvas_items
BEGIN
  SELECT RAISE(ABORT, 'CANVAS_ITEM_DELETE');
END;

-- Activities from Canvas assignments. The sync writes only title/due_at/canvas_updated_at/
-- updated_at of origin='CANVAS' rows; submission_status stays gate-only (001 triggers).
ALTER TABLE activities ADD COLUMN origin TEXT NOT NULL DEFAULT 'USER' CHECK (origin IN ('CANVAS', 'USER'));
ALTER TABLE activities ADD COLUMN canvas_assignment_id TEXT
  CHECK (canvas_assignment_id IS NULL OR (length(canvas_assignment_id) BETWEEN 1 AND 64
                                          AND canvas_assignment_id NOT GLOB '*[^0-9]*'));
ALTER TABLE activities ADD COLUMN canvas_updated_at TEXT;
ALTER TABLE activities ADD COLUMN updated_at TEXT;
CREATE UNIQUE INDEX activities_canvas_assignment ON activities(canvas_assignment_id)
  WHERE canvas_assignment_id IS NOT NULL;

-- human_gate_uses refers to activities by id string: a deleted activity would orphan
-- the audit trail (R3 plan §6.2).
CREATE TRIGGER activities_no_delete BEFORE DELETE ON activities
BEGIN
  SELECT RAISE(ABORT, 'ACTIVITY_DELETE');
END;
