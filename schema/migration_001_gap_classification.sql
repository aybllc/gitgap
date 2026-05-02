-- gitgap Migration 001 — gap classification + content vector
-- Run against: data/gitgap.db (SQLite)
-- Safe to re-run — uses ADD COLUMN IF NOT EXISTS equivalent pattern

-- SQLite workaround: check existing columns with PRAGMA before adding
-- (SQLite 3.37+ supports ALTER TABLE ... ADD COLUMN natively)

ALTER TABLE gap_endpoints ADD COLUMN gap_class TEXT DEFAULT 'general';
ALTER TABLE gap_endpoints ADD COLUMN content_vector TEXT;
