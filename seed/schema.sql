-- The agent's world. Distinct from the event store in process/schema.sql: this
-- is current state plus the case's own business history, which is what the
-- agent reads and writes. The event store holds logs for mining.

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS actors (
  actor_id         TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  role             TEXT NOT NULL CHECK (role IN ('buyer','ap_clerk','controller')),
  company_code     TEXT,
  purchasing_group TEXT
);

CREATE TABLE IF NOT EXISTS vendors (
  vendor_id TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  country   TEXT NOT NULL DEFAULT 'NL'
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number        TEXT PRIMARY KEY,
  vendor_id        TEXT NOT NULL,
  company_code     TEXT NOT NULL,
  purchasing_group TEXT NOT NULL,
  document_type    TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  released_at      TEXT
);

CREATE TABLE IF NOT EXISTS po_items (
  item_key        TEXT PRIMARY KEY,      -- "<po_number>_<item_no>", as in BPI 2019
  po_number       TEXT NOT NULL,
  item_no         TEXT NOT NULL,
  description     TEXT NOT NULL,
  flow            TEXT NOT NULL,         -- an Item Category value from facts.yaml
  item_type       TEXT NOT NULL,
  spend_area      TEXT NOT NULL,
  ordered_quantity REAL NOT NULL,
  po_value_cents  INTEGER NOT NULL,
  payment_blocked INTEGER NOT NULL DEFAULT 0,
  block_reason    TEXT,
  deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS goods_receipts (
  gr_id       TEXT PRIMARY KEY,
  item_key    TEXT NOT NULL,
  quantity    REAL NOT NULL,
  value_cents INTEGER NOT NULL,
  reference   TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  recorded_by TEXT NOT NULL,
  cancelled_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id       TEXT PRIMARY KEY,
  item_key         TEXT NOT NULL,
  vendor_invoice_no TEXT NOT NULL,
  value_cents      INTEGER NOT NULL,
  invoice_date     TEXT NOT NULL,
  recorded_at      TEXT NOT NULL,
  recorded_by      TEXT NOT NULL,
  cleared_at       TEXT,
  status           TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','cleared','queued_for_approval','cancelled'))
);

CREATE TABLE IF NOT EXISTS approval_queue (
  queue_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key    TEXT NOT NULL,
  invoice_id  TEXT,
  requested_by TEXT NOT NULL,
  requested_activity TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  trigger     TEXT NOT NULL CHECK (trigger IN ('above_limit','sod_conflict','tolerance_override')),
  evidence    TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_decision (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  queue_id    INTEGER NOT NULL,
  approver_id TEXT NOT NULL,
  decision    TEXT NOT NULL CHECK (decision IN ('approve','deny')),
  reason      TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

-- The case's own business history, in the SAME activity alphabet as the event
-- log. This is what makes stateful segregation of duties enforceable, and what
-- the world contributes to the mined log. It is the world's history only --
-- never the agent's trace log, which would make conformance self-referential.
CREATE TABLE IF NOT EXISTS case_events (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key    TEXT NOT NULL,
  activity    TEXT NOT NULL,
  actor_id    TEXT NOT NULL,
  actor_kind  TEXT NOT NULL DEFAULT 'human',
  value_cents INTEGER NOT NULL DEFAULT 0,
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_cases (
  case_id          TEXT PRIMARY KEY,
  entity_type      TEXT NOT NULL,
  entity_id        TEXT NOT NULL,
  issue_type       TEXT NOT NULL,
  description      TEXT NOT NULL,
  expected_handling TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS items_by_po     ON po_items (po_number);
CREATE INDEX IF NOT EXISTS gr_by_item      ON goods_receipts (item_key);
CREATE INDEX IF NOT EXISTS inv_by_item     ON invoices (item_key);
CREATE INDEX IF NOT EXISTS events_by_item  ON case_events (item_key, occurred_at, event_id);
CREATE INDEX IF NOT EXISTS queue_by_item   ON approval_queue (item_key);
