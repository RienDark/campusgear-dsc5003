PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS departments (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS members (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 department_id INTEGER NOT NULL REFERENCES departments(id)
);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS equipment (
 id INTEGER PRIMARY KEY, asset_tag TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 category_id INTEGER NOT NULL REFERENCES categories(id), location TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','maintenance','retired'))
);
CREATE TABLE IF NOT EXISTS reservations (
 id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL REFERENCES members(id),
 equipment_id INTEGER NOT NULL REFERENCES equipment(id),
 start_at TEXT NOT NULL, end_at TEXT NOT NULL, purpose TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','checked_out','returned','cancelled')),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 CHECK(start_at < end_at)
);
-- Supports interval conflicts: equality columns before the start range.
CREATE INDEX IF NOT EXISTS idx_reservations_overlap ON reservations(equipment_id,status,start_at,end_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_checkout_per_equipment ON reservations(equipment_id) WHERE status='checked_out';
CREATE TRIGGER IF NOT EXISTS reservation_overlap_insert
BEFORE INSERT ON reservations
WHEN NEW.status IN ('confirmed','checked_out')
BEGIN
 SELECT RAISE(ABORT, 'Reservation overlap') WHERE EXISTS (
  SELECT 1 FROM reservations r WHERE r.equipment_id=NEW.equipment_id
  AND r.status IN ('confirmed','checked_out')
  AND r.start_at < NEW.end_at AND r.end_at > NEW.start_at
 );
END;
CREATE TRIGGER IF NOT EXISTS reservation_overlap_update
BEFORE UPDATE OF equipment_id,start_at,end_at,status ON reservations
WHEN NEW.status IN ('confirmed','checked_out')
BEGIN
 SELECT RAISE(ABORT, 'Reservation overlap') WHERE EXISTS (
  SELECT 1 FROM reservations r WHERE r.id<>NEW.id AND r.equipment_id=NEW.equipment_id
  AND r.status IN ('confirmed','checked_out')
  AND r.start_at < NEW.end_at AND r.end_at > NEW.start_at
 );
END;
CREATE TRIGGER IF NOT EXISTS reservation_transition
BEFORE UPDATE OF status ON reservations
WHEN NEW.status<>OLD.status AND NOT (
 (OLD.status='confirmed' AND NEW.status IN ('checked_out','cancelled')) OR
 (OLD.status='checked_out' AND NEW.status='returned')
)
BEGIN
 SELECT RAISE(ABORT, 'Invalid reservation transition');
END;
CREATE TABLE IF NOT EXISTS loans (
 id INTEGER PRIMARY KEY, reservation_id INTEGER NOT NULL UNIQUE REFERENCES reservations(id),
 checked_out_at TEXT NOT NULL, returned_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY, reservation_id INTEGER NOT NULL REFERENCES reservations(id),
 action TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
