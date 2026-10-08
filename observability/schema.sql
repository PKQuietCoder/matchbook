-- Local span capture. SQLite, no Docker, no collector, no account.
--
-- Spans are the raw material the bridge turns into an event log, so the schema
-- keeps what a process needs: the business object a span acted on, the tool
-- name, the outcome, and the timing. Attributes stay as JSON because span
-- attributes are open-ended by nature.

CREATE TABLE IF NOT EXISTS runs (
  run_id       TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  actor_id     TEXT NOT NULL,
  role         TEXT NOT NULL,
  model        TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  scenario_id  TEXT,
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  killswitch   TEXT NOT NULL DEFAULT 'off'
);

CREATE TABLE IF NOT EXISTS spans (
  span_id     TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  parent_id   TEXT,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,          -- 'request' | 'model' | 'tool'
  step        INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  ok          INTEGER,
  error       TEXT,
  item_key    TEXT,                   -- the business object: the case id, later
  activity    TEXT,                   -- the business activity, if this span is one
  actor       TEXT,
  value_cents INTEGER,
  attributes  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
  message_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  step       INTEGER NOT NULL,
  role       TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool'
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS spans_by_run  ON spans (run_id, step, started_at);
CREATE INDEX IF NOT EXISTS spans_by_item ON spans (item_key);
CREATE INDEX IF NOT EXISTS msgs_by_run   ON messages (run_id, step);
