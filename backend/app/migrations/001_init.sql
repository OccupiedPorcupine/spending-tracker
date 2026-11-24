-- Ledger: every entry the app has queued or written. The audit trail and the retry log.
CREATE TABLE entries (
  id           TEXT PRIMARY KEY,          -- client-generated UUID, also written to column G
  payload      TEXT NOT NULL,             -- JSON: amount, direction, category, required, currency, notes, date, source
  status       TEXT NOT NULL CHECK (status IN ('pending', 'written', 'failed', 'deleted')),
  sheet_id     TEXT NOT NULL,             -- spreadsheet the entry was meant for
  sheet_row    INTEGER,                   -- row number at write time (informational only; never used to find the row)
  fingerprint  TEXT,                      -- amount|serial date|notes as last written, checked before edit/delete
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  written_at   INTEGER
);
CREATE INDEX entries_status ON entries (status, created_at);

CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

-- Passkeys. One user, several devices.
CREATE TABLE credentials (
  id          TEXT PRIMARY KEY,           -- base64url credential ID
  public_key  BLOB NOT NULL,
  sign_count  INTEGER NOT NULL DEFAULT 0,
  name        TEXT NOT NULL,
  transports  TEXT,
  aaguid      TEXT,
  created_at  INTEGER NOT NULL,
  last_used   INTEGER
);

-- One-time enrolment codes printed by `spend enroll`. Only the hash is stored.
CREATE TABLE enroll_codes (
  id          INTEGER PRIMARY KEY,
  code_hash   TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  expires_at  INTEGER NOT NULL,
  failures    INTEGER NOT NULL DEFAULT 0,
  used_at     INTEGER,
  burned_at   INTEGER
);

-- WebAuthn challenges, single use, short-lived.
CREATE TABLE challenges (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('register', 'login')),
  challenge   BLOB NOT NULL,
  code_id     INTEGER,                    -- registration: the enrolment code this challenge was issued for
  created_at  INTEGER NOT NULL
);

-- Server-side sessions. The cookie holds a random 256-bit ID; only its SHA-256 is stored.
CREATE TABLE sessions (
  id_hash       TEXT PRIMARY KEY,
  credential_id TEXT REFERENCES credentials (id) ON DELETE CASCADE,
  created_at    INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  user_agent    TEXT
);
