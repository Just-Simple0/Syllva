-- Revision replacement (implementation spec §11.4, design §4.4).
--
-- A replacement candidate carries the material it replaces from the moment it is
-- received. The user's replace action is the confirmation, so such a candidate
-- goes RECEIVED -> CONFIRMED directly (it is never classified), and only onto the
-- material it named. A new candidate is never confirmed that way.

ALTER TABLE intake_candidates ADD COLUMN replaces_material INTEGER REFERENCES materials(id);

-- the reference table is read-only at runtime; the migration reopens it for one row
DROP TRIGGER intake_state_transitions_ro_insert;
INSERT INTO intake_state_transitions(from_state, to_state) VALUES ('RECEIVED', 'CONFIRMED');
CREATE TRIGGER intake_state_transitions_ro_insert BEFORE INSERT ON intake_state_transitions
BEGIN SELECT RAISE(ABORT, 'READ_ONLY_TABLE'); END;

CREATE TRIGGER intake_candidates_replacement_confirm BEFORE UPDATE OF state ON intake_candidates
WHEN OLD.state = 'RECEIVED' AND NEW.state = 'CONFIRMED'
  AND (OLD.replaces_material IS NULL OR NEW.material_id IS NOT OLD.replaces_material)
BEGIN SELECT RAISE(ABORT, 'REPLACEMENT_ONLY'); END;

CREATE TRIGGER intake_candidates_replacement_not_classified BEFORE UPDATE OF state ON intake_candidates
WHEN OLD.state = 'RECEIVED' AND NEW.state = 'CLASSIFYING' AND OLD.replaces_material IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'REPLACEMENT_ONLY'); END;

CREATE TRIGGER intake_candidates_replacement_identity BEFORE UPDATE OF replaces_material ON intake_candidates
WHEN NEW.replaces_material IS NOT OLD.replaces_material
BEGIN SELECT RAISE(ABORT, 'IDENTITY_IMMUTABLE'); END;

-- a material keeps one FIRST candidate; replacements add further candidates for it
DROP INDEX idx_intake_candidates_material;
CREATE UNIQUE INDEX idx_intake_candidates_material ON intake_candidates(material_id)
  WHERE material_id IS NOT NULL AND replaces_material IS NULL;
