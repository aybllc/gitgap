"""
gitgap — Database connection
Local: SQLite (zero config, single file)
Production: swap DATABASE_URL in .env for PostgreSQL/MariaDB
"""

import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/gitgap.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS papers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pmcid           TEXT UNIQUE NOT NULL,
                doi             TEXT,
                title           TEXT,
                journal         TEXT,
                pub_year        INTEGER,
                abstract_text   TEXT,
                methods_text    TEXT,
                conclusions_text TEXT,
                ingested_at     TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS gap_endpoints (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id        INTEGER NOT NULL,
                declaration_text TEXT NOT NULL,
                section_source  TEXT,
                phase           INTEGER DEFAULT 1,
                confidence      REAL,
                gateway_term    TEXT,
                keeper_reviewed INTEGER DEFAULT 0,
                keeper_verdict  TEXT DEFAULT 'pending',
                gap_class            TEXT DEFAULT 'general',
                content_vector       TEXT,
                caught_paper_cosmoid TEXT DEFAULT NULL,
                caught_at            DATETIME DEFAULT NULL,
                catch_confidence     REAL DEFAULT NULL,
                source_discipline    TEXT DEFAULT NULL,
                target_disciplines   TEXT DEFAULT NULL,
                bridge_potential     REAL DEFAULT NULL,
                bridge_rationale     TEXT DEFAULT NULL,
                created_at           TEXT NOT NULL,
                FOREIGN KEY (paper_id) REFERENCES papers(id)
            )
        """))
        # F1-A / F1-B: Lifecycle closure columns — idempotent via try/except (SQLite)
        _lifecycle_cols = [
            "ALTER TABLE gap_endpoints ADD COLUMN found_at           DATETIME DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN found_paper_cosmoid TEXT     DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN found_paper_doi     TEXT     DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN rejected_at         DATETIME DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN rejection_mode      TEXT     DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN rejection_notes     TEXT     DEFAULT NULL",
            "ALTER TABLE gap_endpoints ADD COLUMN pickup_instructions TEXT     DEFAULT NULL",
        ]
        for _ddl in _lifecycle_cols:
            try:
                conn.execute(text(_ddl))
            except Exception:
                pass  # Column already exists

        # Dedup index — prevents same declaration from same paper being stored twice
        try:
            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_gap_paper_declaration
                ON gap_endpoints(paper_id, declaration_text)
            """))
        except Exception:
            pass  # Index already exists

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ingest_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                query_term      TEXT,
                pmcids_fetched  INTEGER DEFAULT 0,
                pmcids_parsed   INTEGER DEFAULT 0,
                gaps_found      INTEGER DEFAULT 0,
                started_at      TEXT NOT NULL,
                completed_at    TEXT,
                status          TEXT DEFAULT 'running',
                notes           TEXT
            )
        """))
        # F3-B: CAP score column
        try:
            conn.execute(text(
                "ALTER TABLE gap_endpoints ADD COLUMN cap_score REAL DEFAULT NULL"
            ))
        except Exception:
            pass  # Column already exists

        # Admin: tombstone columns on papers (idempotent)
        for _ddl in [
            "ALTER TABLE papers ADD COLUMN tombstone_state TEXT DEFAULT NULL",
            "ALTER TABLE papers ADD COLUMN tombstoned_at   TEXT DEFAULT NULL",
        ]:
            try:
                conn.execute(text(_ddl))
            except Exception:
                pass

        # Admin: API source registry
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_sources (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL,
                slug             TEXT NOT NULL UNIQUE,
                base_url         TEXT NOT NULL,
                auth_type        TEXT DEFAULT 'none',
                api_key_env      TEXT DEFAULT NULL,
                email_env        TEXT DEFAULT NULL,
                response_format  TEXT DEFAULT 'json',
                rate_limit_per_sec REAL DEFAULT 3.0,
                status           TEXT DEFAULT 'active',
                notes            TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )
        """))

        # Admin: field mapping rules per source
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_field_mappings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id       INTEGER NOT NULL,
                source_field    TEXT NOT NULL,
                target_table    TEXT NOT NULL DEFAULT 'papers',
                target_field    TEXT NOT NULL,
                transform       TEXT DEFAULT NULL,
                required        INTEGER DEFAULT 0,
                default_value   TEXT DEFAULT NULL,
                notes           TEXT,
                FOREIGN KEY (source_id) REFERENCES api_sources(id)
            )
        """))

        # Admin: journal registry (corpus to maintain per source)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS journal_registry (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id       INTEGER NOT NULL,
                journal_name    TEXT NOT NULL,
                issn            TEXT DEFAULT NULL,
                nlm_id          TEXT DEFAULT NULL,
                search_query    TEXT NOT NULL,
                oai_endpoint    TEXT DEFAULT NULL,
                article_count   INTEGER DEFAULT 0,
                last_reconciled TEXT DEFAULT NULL,
                status          TEXT DEFAULT 'active',
                notes           TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES api_sources(id)
            )
        """))
        # Idempotent: add oai_endpoint if migrating from older schema
        try:
            conn.execute(text(
                "ALTER TABLE journal_registry ADD COLUMN oai_endpoint TEXT DEFAULT NULL"
            ))
        except Exception:
            pass

        # Admin: reconcile run log per journal
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reconcile_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_id          INTEGER NOT NULL,
                triggered_at        TEXT NOT NULL,
                completed_at        TEXT DEFAULT NULL,
                articles_checked    INTEGER DEFAULT 0,
                articles_added      INTEGER DEFAULT 0,
                articles_tombstoned INTEGER DEFAULT 0,
                status              TEXT DEFAULT 'running',
                error_message       TEXT DEFAULT NULL,
                FOREIGN KEY (journal_id) REFERENCES journal_registry(id)
            )
        """))

        # Admin: journal discovery probe log
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS discovery_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_name    TEXT NOT NULL,
                queried_at      TEXT NOT NULL,
                result_status   TEXT NOT NULL,
                article_count   INTEGER DEFAULT 0,
                bioc_available  INTEGER DEFAULT 0,
                sample_pmcids   TEXT DEFAULT NULL,
                notes           TEXT
            )
        """))

        # Seed built-in sources (INSERT OR IGNORE on unique slug)
        # Ordered by recommended build priority (Phase 1 = core → Phase 5 = optional)
        _now_ts = __import__('datetime').datetime.utcnow().isoformat()
        _sources_seed = [
            # ── Phase 0: existing NCBI / PMC pipeline ────────────────────────
            ("PMC BioC JSON", "pmc_bioc",
             "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi",
             "email_key", "NCBI_API_KEY", "NCBI_EMAIL", "json", 3.0,
             "ACTIVE. Primary full-text source. Pre-labeled sections (TITLE, ABSTRACT, METHODS, CONCL). "
             "Phase 0 — already wired into ingest pipeline."),
            ("NCBI E-Utilities Search", "ncbi_esearch",
             "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
             "email_key", "NCBI_API_KEY", "NCBI_EMAIL", "json", 3.0,
             "ACTIVE. PMC/PubMed article search. Returns PMC IDs for a query term. "
             "Without key: 3 req/sec. With NCBI_API_KEY: 10 req/sec."),
            ("NCBI E-Utilities Fetch (JATS)", "ncbi_efetch",
             "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
             "email_key", "NCBI_API_KEY", "NCBI_EMAIL", "xml", 3.0,
             "ACTIVE. JATS XML fallback for articles not in BioC OA corpus. "
             "Same rate limits as esearch."),
            # ── Phase 1: core infrastructure ─────────────────────────────────
            ("OpenAlex", "openalex",
             "https://api.openalex.org",
             "none", None, "OPENALEX_EMAIL", "json", 10.0,
             "PENDING. Free scholarly graph: 200M+ works, authors, institutions, concepts. "
             "No key required. Add mailto= polite header via OPENALEX_EMAIL env var. "
             "Primary data layer — covers ~90% of scholarly literature. "
             "Endpoints: /works /sources /authors /institutions. "
             "Docs: https://docs.openalex.org"),
            ("Crossref REST API", "crossref",
             "https://api.crossref.org",
             "none", None, "CROSSREF_EMAIL", "json", 50.0,
             "PENDING. DOI registration + metadata normalization. No key required. "
             "Add User-Agent mailto: header via CROSSREF_EMAIL. "
             "Essential for DOI resolution and deduplication. "
             "Endpoint: /works?query=...  Docs: https://api.crossref.org"),
            # ── Phase 2: quality + filtering ─────────────────────────────────
            ("DOAJ API", "doaj",
             "https://doaj.org/api/v4",
             "none", None, None, "json", 10.0,
             "PENDING. Directory of Open Access Journals — vetted OA quality filter. "
             "No key required for read endpoints. "
             "Endpoints: /search/journals /search/articles. "
             "Use for journal whitelist and license credibility check. "
             "Docs: https://doaj.org/api/v4/docs"),
            # ── Phase 3: direct journal enrichment ───────────────────────────
            ("PLOS API", "plos",
             "https://api.plos.org",
             "none", None, None, "json", 10.0,
             "PENDING. PLOS publisher API — immediate access, no signup. "
             "Endpoint: /search?q=...  Returns JSON. "
             "Fields: title, abstract, author, journal, DOI. "
             "~5 min to usable. Docs: https://api.plos.org"),
            ("Springer Nature Open Access API", "springer_nature",
             "https://api.springernature.com",
             "api_key_param", "SPRINGER_API_KEY", None, "json", 5.0,
             "PENDING. Major publisher OA API — requires account + API key. "
             "Key goes in api_key= query param. "
             "Register at: https://dev.springernature.com  ~15-30 min setup. "
             "Strongest structured OA dataset from a major publisher."),
            # ── Phase 4: domain expansion ─────────────────────────────────────
            ("Europe PMC", "europe_pmc",
             "https://www.ebi.ac.uk/europepmc/webservices/rest",
             "none", None, "EPMC_EMAIL", "json", 10.0,
             "PENDING. Biomedical full-text + MeSH annotations. No key required. "
             "Endpoint: /search?query=...  Includes full-text XML where available. "
             "Covers life sciences. Docs: https://europepmc.org/RestfulWebService"),
            # ── Phase 5: optional / experimental ─────────────────────────────
            ("Frontiers Search API", "frontiers",
             "https://search-api.frontiersin.org",
             "none", None, None, "json", 5.0,
             "PENDING. Frontiers publisher API — auth requirements unknown; verify stability. "
             "Check Swagger UI at base_url. Fallback: use OpenAlex filter if unstable. "
             "Confidence: medium."),
            # ── Platform-level: OAI-PMH (universal journal feed) ─────────────
            ("OAI-PMH Harvester", "oai_pmh",
             "https://www.openarchives.org/OAI/openarchivesprotocol.html",
             "none", None, "OAI_EMAIL", "xml", 5.0,
             "PENDING. Universal OAI-PMH harvesting protocol. Used by OJS journals, "
             "institutional repositories, and many independent publishers. "
             "Endpoint per journal is stored in journal_registry.oai_endpoint. "
             "Typical pattern: /oai or /oai/request or /index.php/journal/oai. "
             "verb=ListRecords&metadataPrefix=oai_dc  for DC metadata; "
             "Use &from=DATE for incremental sync. No key required. "
             "This is the industry-standard ingestion protocol for independent journals."),
            # ── Additional publishers ─────────────────────────────────────────
            ("Cambridge University Press API", "cambridge_up",
             "https://api.cambridge.org",
             "api_key_header", "CAMBRIDGE_API_KEY", None, "json", 5.0,
             "PENDING. Cambridge journals API — register at https://api.cambridge.org/register. "
             "Publisher-level journal metadata and content access. "
             "Use case: Cambridge-published peer-reviewed journals. "
             "Confidence: high (existence confirmed)."),
            ("Unpaywall", "unpaywall",
             "https://api.unpaywall.org/v2",
             "none", None, "UNPAYWALL_EMAIL", "json", 10.0,
             "PENDING. DOI → OA full-text resolver. No key required; add email param. "
             "Given a DOI, returns best available OA full-text link (PMC, publisher, preprint). "
             "Endpoint: /v2/{doi}?email=YOUR_EMAIL. "
             "Critical for DOI-to-full-text pipeline after Crossref discovery. "
             "Docs: https://unpaywall.org/products/api"),
            # ── Conditional / verify access first ────────────────────────────
            ("Elsevier ScienceDirect API", "elsevier",
             "https://api.elsevier.com",
             "api_key_header", "ELSEVIER_API_KEY", "ELSEVIER_INST_TOKEN", "json", 2.0,
             "CONDITIONAL. Major publisher — API exists but more controlled than OA options. "
             "Requires institutional token for full text. "
             "Register at: https://dev.elsevier.com  "
             "Good for metadata + abstracts; full-text gated by institution. "
             "Confidence: medium (access depends on subscription)."),
            ("Taylor & Francis API", "taylor_francis",
             "https://api.taylorfrancis.com",
             "api_key_param", "TF_API_TOKEN", None, "json", 2.0,
             "CONDITIONAL. API token available via admin portal. "
             "Primarily institutional/admin integrations rather than open scholarly API. "
             "Verify access at: https://help.tandfonline.com — search API Token. "
             "Confidence: low as fully open scholarly API."),
        ]
        for (name, slug, url, auth, key_env, email_env, fmt, rate, notes) in _sources_seed:
            conn.execute(text("""
                INSERT OR IGNORE INTO api_sources
                (name, slug, base_url, auth_type, api_key_env, email_env,
                 response_format, rate_limit_per_sec, status, notes, created_at, updated_at)
                VALUES (:name, :slug, :url, :auth, :key_env, :email_env,
                        :fmt, :rate, 'active', :notes, :now, :now)
            """), {
                "name": name, "slug": slug, "url": url, "auth": auth,
                "key_env": key_env, "email_env": email_env,
                "fmt": fmt, "rate": rate, "notes": notes, "now": _now_ts,
            })

        # Seed field mappings for pmc_bioc (only if that source exists and has no mappings yet)
        _pmc_id = conn.execute(text(
            "SELECT id FROM api_sources WHERE slug = 'pmc_bioc'"
        )).scalar()
        if _pmc_id:
            _existing = conn.execute(text(
                "SELECT COUNT(*) FROM api_field_mappings WHERE source_id = :id"
            ), {"id": _pmc_id}).scalar()
            if not _existing:
                _bioc_mappings = [
                    ("documents[0].passages[type=TITLE].text",           "papers", "title",            None,     1, None, "First TITLE passage"),
                    ("documents[0].infons.article-id_doi",               "papers", "doi",              None,     0, None, "DOI from document infons"),
                    ("documents[0].infons.journal",                      "papers", "journal",          None,     0, None, "Journal name"),
                    ("documents[0].infons.year",                         "papers", "pub_year",         "int",    0, None, "Publication year (cast to int)"),
                    ("documents[0].passages[type=ABSTRACT].text",        "papers", "abstract_text",    "concat", 0, None, "All ABSTRACT passages joined"),
                    ("documents[0].passages[type=METHODS].text",         "papers", "methods_text",     "concat", 0, None, "METHODS / METHOD passages joined"),
                    ("documents[0].passages[type=CONCL].text",           "papers", "conclusions_text", "concat", 0, None, "CONCL / DISCUSS / CONCLUSIONS passages joined"),
                ]
                for (sf, tt, tf, tr, req, dv, notes) in _bioc_mappings:
                    conn.execute(text("""
                        INSERT INTO api_field_mappings
                        (source_id, source_field, target_table, target_field,
                         transform, required, default_value, notes)
                        VALUES (:sid, :sf, :tt, :tf, :tr, :req, :dv, :notes)
                    """), {
                        "sid": _pmc_id, "sf": sf, "tt": tt, "tf": tf,
                        "tr": tr, "req": req, "dv": dv, "notes": notes,
                    })

        # AI detection columns on papers (idempotent migration)
        _ai_cols = [
            "ALTER TABLE papers ADD COLUMN ai_declared          TEXT    DEFAULT NULL",
            "ALTER TABLE papers ADD COLUMN ai_detection_score   REAL    DEFAULT NULL",
            "ALTER TABLE papers ADD COLUMN ai_detection_signals TEXT    DEFAULT NULL",
            "ALTER TABLE papers ADD COLUMN ai_flag              INTEGER DEFAULT 0",
            "ALTER TABLE papers ADD COLUMN ai_interrogated_at   TEXT    DEFAULT NULL",
        ]
        for _stmt in _ai_cols:
            try:
                conn.execute(text(_stmt))
            except Exception:
                pass  # column already exists

        # Docs: journal submission queue (public request form)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS journal_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                journal_name    TEXT NOT NULL,
                url             TEXT,
                oai_endpoint    TEXT,
                issn            TEXT,
                contact_email   TEXT,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                status          TEXT DEFAULT 'pending'
            )
        """))

        # F3-A: Convergence clustering tables
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS convergence_groups (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                representative_gap_id INTEGER NOT NULL,
                member_count         INTEGER NOT NULL DEFAULT 0,
                paper_count          INTEGER NOT NULL DEFAULT 0,
                is_agreed            INTEGER NOT NULL DEFAULT 0,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                FOREIGN KEY (representative_gap_id) REFERENCES gap_endpoints(id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS convergence_members (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                gap_id   INTEGER NOT NULL,
                paper_id INTEGER NOT NULL,
                FOREIGN KEY (group_id) REFERENCES convergence_groups(id),
                FOREIGN KEY (gap_id)   REFERENCES gap_endpoints(id)
            )
        """))
