-- One-tap presets. Kept here, never in the sheet.
CREATE TABLE favourites (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  payload     TEXT NOT NULL,             -- JSON: amount (or null), direction, category, required, currency, notes
  position    INTEGER NOT NULL,
  created_at  INTEGER NOT NULL
);

-- Edits to rows the app wrote: what the entry was before and after, for the audit trail.
CREATE TABLE entry_edits (
  id          INTEGER PRIMARY KEY,
  entry_id    TEXT NOT NULL REFERENCES entries (id),
  before      TEXT NOT NULL,
  after       TEXT NOT NULL,
  sheet_row   INTEGER,
  at          INTEGER NOT NULL
);
CREATE INDEX entry_edits_entry ON entry_edits (entry_id, at);
