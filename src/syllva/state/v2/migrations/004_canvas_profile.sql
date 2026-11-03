-- R3-1 (R3 plan §6.1, §6.2): the Canvas connection, without the token.
-- The token lives only in the OS keychain (design §8, I9); token_state is the
-- DB-side intent that makes the keychain write recoverable (PENDING rows are
-- cleaned at start-up because the keychain cannot be enumerated).
CREATE TABLE canvas_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  profile_id TEXT NOT NULL UNIQUE CHECK (length(profile_id) = 33 AND substr(profile_id, 1, 1) = 'c'),
  origin TEXT NOT NULL CHECK (origin LIKE 'https://%'),
  user_id TEXT NOT NULL CHECK (length(user_id) BETWEEN 1 AND 64 AND user_id NOT GLOB '*[^0-9]*'),
  display_name TEXT NOT NULL,
  file_hosts TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(file_hosts) AND json_type(file_hosts) = 'array'),
  token_state TEXT NOT NULL CHECK (token_state IN ('PENDING', 'SET')),
  token_set_at TEXT,
  created_at TEXT NOT NULL,
  CHECK (token_state = 'PENDING' OR token_set_at IS NOT NULL)
);

-- Canvas course <-> local course, chosen by the user (one to one).
CREATE TABLE canvas_courses (
  id INTEGER PRIMARY KEY,
  canvas_course_id TEXT NOT NULL UNIQUE
    CHECK (length(canvas_course_id) BETWEEN 1 AND 64 AND canvas_course_id NOT GLOB '*[^0-9]*'),
  course_id INTEGER NOT NULL UNIQUE REFERENCES courses(id),
  name TEXT NOT NULL,
  code TEXT NOT NULL,
  term_name TEXT NOT NULL,
  linked_at TEXT NOT NULL
);
