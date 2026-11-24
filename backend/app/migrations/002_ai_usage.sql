-- One row per call to the AI parser: token counts and timing only. The typed text is never stored.
CREATE TABLE ai_usage (
  id            INTEGER PRIMARY KEY,
  at            INTEGER NOT NULL,
  model         TEXT NOT NULL,
  ok            INTEGER NOT NULL,            -- 1 if the reply was used, 0 on any failure
  error         TEXT,                        -- short error class, e.g. "timeout", "APIStatusError 429"
  input_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  ms            INTEGER NOT NULL,
  items         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ai_usage_at ON ai_usage (at);
