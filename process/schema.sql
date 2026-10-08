-- The canonical event store. One database holds every log side by side -- the
-- real human log, the fitted synthetic log, and the log mined back out of the
-- agent's spans -- which is what makes them comparable.

PRAGMA journal_mode = WAL;

-- Provenance travels with the data. Every derived artifact cites a log_id, so
-- a number in a report can always be traced to a licensed source.
CREATE TABLE IF NOT EXISTS logs (
  log_id        TEXT PRIMARY KEY,
  source        TEXT NOT NULL DEFAULT '',
  doi           TEXT NOT NULL DEFAULT '',
  license       TEXT NOT NULL DEFAULT '',
  attribution   TEXT NOT NULL DEFAULT '',
  sha256        TEXT NOT NULL DEFAULT '',
  ingested_at   TEXT NOT NULL,
  event_count   INTEGER NOT NULL DEFAULT 0,
  case_count    INTEGER NOT NULL DEFAULT 0,
  tie_broken    INTEGER NOT NULL DEFAULT 0,
  notes         TEXT NOT NULL DEFAULT ''
);

-- Interned per log, so the hot loops compare small ints.
CREATE TABLE IF NOT EXISTS activities (
  log_id      TEXT NOT NULL,
  activity_id INTEGER NOT NULL,
  name        TEXT NOT NULL,
  PRIMARY KEY (log_id, activity_id)
);

CREATE TABLE IF NOT EXISTS resources (
  log_id      TEXT NOT NULL,
  resource_id INTEGER NOT NULL,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'unknown',
  PRIMARY KEY (log_id, resource_id)
);

CREATE TABLE IF NOT EXISTS cases (
  log_id      TEXT NOT NULL,
  case_id     TEXT NOT NULL,
  case_index  INTEGER NOT NULL,
  variant_id  INTEGER,
  start_ts    INTEGER NOT NULL,
  end_ts      INTEGER NOT NULL,
  event_count INTEGER NOT NULL,
  attrs       TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (log_id, case_id)
);

CREATE TABLE IF NOT EXISTS events (
  log_id      TEXT NOT NULL,
  seq         INTEGER NOT NULL,   -- position within the log's global ordering
  case_index  INTEGER NOT NULL,
  activity_id INTEGER NOT NULL,
  resource_id INTEGER NOT NULL,
  ts          INTEGER NOT NULL,
  ts_precision TEXT NOT NULL DEFAULT 'second',
  tie_broken  INTEGER NOT NULL DEFAULT 0,
  value_cents INTEGER NOT NULL DEFAULT 0,
  attrs       TEXT,
  PRIMARY KEY (log_id, seq)
);

CREATE TABLE IF NOT EXISTS variants (
  log_id            TEXT NOT NULL,
  variant_id        INTEGER NOT NULL,
  activity_sequence TEXT NOT NULL,
  case_count        INTEGER NOT NULL,
  PRIMARY KEY (log_id, variant_id)
);

-- Object-centric side. Only four object types are genuinely present in
-- BPI 2019 (purchase document, item, vendor, company); goods receipts and
-- invoices are real objects only in the synthetic and agent logs, where we
-- control the instrumentation. Any synthesis must be recorded in logs.notes.
CREATE TABLE IF NOT EXISTS objects (
  log_id      TEXT NOT NULL,
  object_id   TEXT NOT NULL,
  object_type TEXT NOT NULL,
  attrs       TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (log_id, object_id)
);

CREATE TABLE IF NOT EXISTS event_objects (
  log_id    TEXT NOT NULL,
  seq       INTEGER NOT NULL,
  object_id TEXT NOT NULL,
  qualifier TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (log_id, seq, object_id, qualifier)
);

CREATE INDEX IF NOT EXISTS events_by_case     ON events (log_id, case_index, seq);
CREATE INDEX IF NOT EXISTS events_by_activity ON events (log_id, activity_id);
CREATE INDEX IF NOT EXISTS events_by_time     ON events (log_id, ts);
CREATE INDEX IF NOT EXISTS events_by_resource ON events (log_id, resource_id);
CREATE INDEX IF NOT EXISTS cases_by_index     ON cases (log_id, case_index);
CREATE INDEX IF NOT EXISTS eo_by_object       ON event_objects (log_id, object_id);
