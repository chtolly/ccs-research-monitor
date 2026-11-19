-- Review space database (Cloudflare D1). Private: nothing here is public.
-- Create/update with:  npx wrangler d1 execute ccs-review --remote --file api/schema.sql

-- People who may log in, and their role.
CREATE TABLE IF NOT EXISTS people (
  email      TEXT PRIMARY KEY,          -- lower case
  name       TEXT NOT NULL,
  role       TEXT NOT NULL CHECK (role IN ('editor', 'approver')),
  added_by   TEXT,
  added_at   TEXT NOT NULL
);

-- Everything that waits for (or went through) review.
-- kind = 'item'    : a new item (from the weekly agent run or a manual submission)
-- kind = 'related' : new "you might also be interested in" links for an item that is already public
CREATE TABLE IF NOT EXISTS queue (
  id                TEXT PRIMARY KEY,
  kind              TEXT NOT NULL DEFAULT 'item',
  status            TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
  origin            TEXT NOT NULL CHECK (origin IN ('agent', 'manual')),
  data              TEXT NOT NULL,      -- JSON with the item fields
  url_norm          TEXT,
  title             TEXT,
  target_id         TEXT,               -- for kind = 'related'
  submitted_by      TEXT NOT NULL,      -- email, or 'agent'
  submitted_by_name TEXT NOT NULL,
  submitted_at      TEXT NOT NULL,
  decided_by        TEXT,
  decided_by_name   TEXT,
  decided_at        TEXT,
  reason            TEXT
);
CREATE INDEX IF NOT EXISTS queue_status ON queue (status);
CREATE INDEX IF NOT EXISTS queue_url ON queue (url_norm);

-- Who did what, and when. Never edited or deleted by the app.
CREATE TABLE IF NOT EXISTS audit (
  n          INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  actor      TEXT NOT NULL,             -- email, or 'agent'
  actor_name TEXT NOT NULL,
  action     TEXT NOT NULL,
  item_id    TEXT,
  details    TEXT
);
