import contextlib
import sqlite3
import time
from collections.abc import Callable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS licenses (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  key_hash TEXT NOT NULL UNIQUE,
  plan TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','past_due','canceled','revoked')),
  period_end INTEGER,
  provider TEXT NOT NULL DEFAULT '',
  provider_ref TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS licenses_ref ON licenses(provider, provider_ref)
  WHERE provider_ref != '';
CREATE TABLE IF NOT EXISTS devices (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id TEXT NOT NULL REFERENCES licenses(id),
  device_id TEXT NOT NULL,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  UNIQUE (license_id, device_id));
CREATE TABLE IF NOT EXISTS lots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0),
  remaining INTEGER NOT NULL CHECK (remaining >= 0 AND remaining <= amount),
  expires_at INTEGER,
  source TEXT UNIQUE,
  created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS lots_account ON lots(account_id, expires_at);
CREATE TABLE IF NOT EXISTS reservations (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  idem TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('held','settled','expired')),
  ops TEXT NOT NULL,
  cost INTEGER NOT NULL,
  alloc TEXT NOT NULL,
  done TEXT,
  charged INTEGER,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  settled_at INTEGER,
  UNIQUE (account_id, idem));
CREATE INDEX IF NOT EXISTS reservations_open ON reservations(state, expires_at);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('grant','hold','release','expire')),
  amount INTEGER NOT NULL,
  ref TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS entries_account ON entries(account_id, id);
CREATE TABLE IF NOT EXISTS webhook_events (
  provider TEXT NOT NULL, event_id TEXT NOT NULL, received_at INTEGER NOT NULL,
  PRIMARY KEY (provider, event_id));
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  sent_at INTEGER);
"""


class DB:
    """SQLite with one write transaction at a time. Swap this class for Postgres at scale: every
    other module only needs a connection that supports execute() and named row access."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = path, clock
        conn = sqlite3.connect(path, timeout=15)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def now(self) -> int:
        return int(self.clock())

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()
