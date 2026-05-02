-- gitgap — Fresh Install Schema
-- Gap detection pipeline: PMC ingestion → JATS parsing → gateway filter → gap index
-- Author: Eric D. Martin | ORCID 0009-0006-5944-1742

CREATE DATABASE IF NOT EXISTS gitgap CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gitgap;

-- ── Ingested papers ──────────────────────────────────────────────────────────
-- Raw record of every paper pulled from PMC.
-- JATS parsed fields stored here. Full XML path for reference.

CREATE TABLE IF NOT EXISTS `papers` (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    pmcid           VARCHAR(20) UNIQUE NOT NULL,
    doi             VARCHAR(255),
    title           TEXT,
    journal         VARCHAR(500),
    pub_year        INT,
    abstract_text   MEDIUMTEXT,
    methods_text    MEDIUMTEXT,
    conclusions_text MEDIUMTEXT,
    full_text_path  VARCHAR(500),       -- local path to raw JATS XML if cached
    ingested_at     DATETIME NOT NULL,
    INDEX idx_doi (doi),
    INDEX idx_pub_year (pub_year),
    INDEX idx_journal (journal(100))
) ENGINE=InnoDB;


-- ── Gap endpoints ─────────────────────────────────────────────────────────────
-- Explicit future research declarations extracted from conclusions sections.
-- Phase 1: explicit language only (binary GO/NO-GO)
-- Phase 2: greyscale implicit signals

CREATE TABLE IF NOT EXISTS `gap_endpoints` (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    paper_id        INT NOT NULL,
    declaration_text TEXT NOT NULL,     -- exact extracted text
    section_source  VARCHAR(50),        -- conclusions / discussion / abstract
    phase           TINYINT DEFAULT 1,  -- 1=explicit, 2=greyscale implicit
    confidence      DECIMAL(3,2),       -- 1.00=black, 0.75=dark grey, 0.50=mid, 0.25=light
    gateway_term    VARCHAR(100),       -- which trigger phrase matched
    keeper_reviewed TINYINT DEFAULT 0,  -- 0=flagged by system, 1=reviewed by keeper
    keeper_verdict  ENUM('pass','fail','pending') DEFAULT 'pending',
    created_at      DATETIME NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id),
    INDEX idx_phase (phase),
    INDEX idx_confidence (confidence),
    INDEX idx_keeper (keeper_reviewed, keeper_verdict)
) ENGINE=InnoDB;


-- ── Gateway terms ─────────────────────────────────────────────────────────────
-- The trigger vocabulary. Seeded below. Expandable.

CREATE TABLE IF NOT EXISTS `gateway_terms` (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    term            VARCHAR(200) UNIQUE NOT NULL,
    phase           TINYINT DEFAULT 1,
    confidence      DECIMAL(3,2) DEFAULT 1.00,
    active          TINYINT DEFAULT 1
) ENGINE=InnoDB;


-- ── Convergence index ────────────────────────────────────────────────────────
-- When multiple papers declare the same gap, convergence score rises.
-- Simple text similarity clustering handled at application layer.
-- This table stores confirmed convergence groups.

CREATE TABLE IF NOT EXISTS `convergence_groups` (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    group_label     TEXT,               -- auto-generated label from declarations
    convergence_score INT DEFAULT 1,    -- count of papers pointing here
    domain          VARCHAR(200),
    created_at      DATETIME NOT NULL,
    updated_at      DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS `convergence_members` (
    group_id        INT NOT NULL,
    gap_endpoint_id INT NOT NULL,
    PRIMARY KEY (group_id, gap_endpoint_id),
    FOREIGN KEY (group_id) REFERENCES convergence_groups(id),
    FOREIGN KEY (gap_endpoint_id) REFERENCES gap_endpoints(id)
) ENGINE=InnoDB;


-- ── Ingest log ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS `ingest_log` (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    query_term      VARCHAR(500),
    pmcids_fetched  INT DEFAULT 0,
    pmcids_parsed   INT DEFAULT 0,
    gaps_found      INT DEFAULT 0,
    started_at      DATETIME NOT NULL,
    completed_at    DATETIME,
    status          ENUM('running','complete','failed') DEFAULT 'running',
    notes           TEXT
) ENGINE=InnoDB;


-- ── Seed: Phase 1 gateway terms (explicit declarations) ──────────────────────

INSERT IGNORE INTO `gateway_terms` (term, phase, confidence) VALUES
('future work', 1, 1.00),
('further research', 1, 1.00),
('future research', 1, 1.00),
('future studies', 1, 1.00),
('future investigation', 1, 1.00),
('remains an open question', 1, 1.00),
('open question', 1, 0.90),
('left for future', 1, 1.00),
('beyond the scope', 1, 0.90),
('we did not address', 1, 1.00),
('warrants further', 1, 1.00),
('requires further', 1, 0.95),
('needs further', 1, 0.95),
('deserves further', 1, 0.95),
('future directions', 1, 1.00),
('further work', 1, 1.00),
('future experiments', 1, 1.00),
('to be investigated', 1, 0.90),
('remains to be determined', 1, 1.00),
('remains unclear', 1, 0.85),
('not yet understood', 1, 0.90),
('still unknown', 1, 0.85);


-- ── Seed: Phase 2 gateway terms (greyscale implicit) ─────────────────────────

INSERT IGNORE INTO `gateway_terms` (term, phase, confidence) VALUES
('we note that', 2, 0.50),
('interestingly', 2, 0.40),
('this suggests', 2, 0.55),
('one limitation', 2, 0.65),
('we did not account', 2, 0.70),
('it is possible that', 2, 0.45),
('could be explored', 2, 0.60),
('might be due to', 2, 0.45),
('may warrant', 2, 0.65),
('an interesting avenue', 2, 0.70),
('we leave this', 2, 0.75),
('not explored here', 2, 0.75),
('assumed throughout', 2, 0.60);
