"""SQLite store (full text stays local). Default location: $NEWS_DATA or /workspace/news-data."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA = Path(os.environ.get("NEWS_DATA", "/workspace/news-data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,          -- normalized
  url_raw TEXT, final_url TEXT,
  outlet TEXT NOT NULL, title TEXT,
  published_at TEXT, fetched_at TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL, extractor_version TEXT, normalization TEXT,
  meta_json TEXT, meta_hash TEXT, article_root TEXT, article_leaf TEXT,
  n_paragraphs INTEGER, status TEXT NOT NULL,   -- ok | short | empty | duplicate | error
  error TEXT,
  batch_id INTEGER REFERENCES batches(id), batch_index INTEGER, batch_proof TEXT
);
CREATE INDEX IF NOT EXISTS articles_root ON articles(article_root);
CREATE INDEX IF NOT EXISTS articles_batch ON articles(batch_id);
CREATE TABLE IF NOT EXISTS paragraphs (
  id INTEGER PRIMARY KEY,
  article_id INTEGER NOT NULL REFERENCES articles(id),
  idx INTEGER NOT NULL, text TEXT NOT NULL, leaf TEXT NOT NULL,
  proof TEXT NOT NULL,               -- JSON path to article_root
  UNIQUE(article_id, idx)
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  root TEXT UNIQUE NOT NULL, manifest_hash TEXT NOT NULL, manifest_path TEXT, manifest_uri TEXT,
  article_count INTEGER NOT NULL, registrant TEXT,
  tx TEXT, block_number INTEGER, block_time INTEGER,   -- unix seconds, from the block
  status TEXT NOT NULL, created_at TEXT NOT NULL      -- built | registered | failed
);
CREATE TABLE IF NOT EXISTS feed_runs (
  id INTEGER PRIMARY KEY, outlet TEXT, started_at TEXT, entries INTEGER, new INTEGER, ok INTEGER, error TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or DATA / "news.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c
