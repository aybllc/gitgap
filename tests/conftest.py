"""
gitgap — Test configuration and shared fixtures.

Patches app.database before any app import so init_db() uses
an in-memory SQLite engine. get_db is overridden via FastAPI
dependency_overrides so every route sees the test DB.
"""

import itertools
import json
import os

# ── Working directory ─────────────────────────────────────────────────────────
# Must be the project root so Jinja2 can find app/templates/
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Patch database BEFORE importing app ──────────────────────────────────────
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# StaticPool ensures all connections share the SAME in-memory SQLite DB.
# Without it, each new connection creates a separate blank database and
# tables created in init_db() are invisible to route sessions.
_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

import app.database as _db_mod   # noqa: E402
_db_mod.engine = _TEST_ENGINE
_db_mod.SessionLocal = _TestSessionLocal

# ── Now import app (lifespan init_db uses test engine) ───────────────────────
from app.main import app           # noqa: E402
from app.database import get_db, init_db  # noqa: E402

init_db()  # Create all tables on in-memory engine

# ── Override get_db dependency ────────────────────────────────────────────────
def _override_get_db():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = _override_get_db

# ── Fixtures ──────────────────────────────────────────────────────────────────
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    """Function-scoped DB session. Rolls back after each test."""
    session = _TestSessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


# ── Unique ID counter (prevents pmcid collisions across tests) ────────────────
_id_counter = itertools.count(start=10000)


def _next_id() -> int:
    return next(_id_counter)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def seed_paper(db, **overrides) -> int:
    """Insert a test paper row. Returns paper_id."""
    data = {
        "pmcid":             overrides.get("pmcid", f"TEST{_next_id()}"),
        "doi":               overrides.get("doi", None),
        "title":             overrides.get("title", "Test Paper"),
        "journal":           overrides.get("journal", "Test Journal"),
        "pub_year":          overrides.get("pub_year", 2024),
        "abstract_text":     overrides.get("abstract_text", "Further research is needed."),
        "methods_text":      overrides.get("methods_text", None),
        "conclusions_text":  overrides.get("conclusions_text", "This gap remains unresolved."),
        "ingested_at":       overrides.get("ingested_at", "2024-01-01T00:00:00"),
        "ai_flag":           overrides.get("ai_flag", 0),
        "ai_declared":       overrides.get("ai_declared", None),
        "ai_detection_score": overrides.get("ai_detection_score", None),
        "ai_detection_signals": overrides.get("ai_detection_signals", None),
        "ai_interrogated_at": overrides.get("ai_interrogated_at", None),
    }
    db.execute(text("""
        INSERT INTO papers
        (pmcid, doi, title, journal, pub_year, abstract_text, methods_text,
         conclusions_text, ingested_at, ai_flag, ai_declared, ai_detection_score,
         ai_detection_signals, ai_interrogated_at)
        VALUES
        (:pmcid, :doi, :title, :journal, :pub_year, :abstract_text, :methods_text,
         :conclusions_text, :ingested_at, :ai_flag, :ai_declared, :ai_detection_score,
         :ai_detection_signals, :ai_interrogated_at)
    """), data)
    db.commit()
    return db.execute(text("SELECT last_insert_rowid()")).scalar()


def seed_gap(db, paper_id: int, **overrides) -> int:
    """Insert a test gap row. Returns gap_id."""
    _vec = json.dumps([0.0] * 512)
    data = {
        "paper_id":           paper_id,
        "declaration_text":   overrides.get("declaration_text", f"Further research is needed in area {_next_id()}."),
        "section_source":     overrides.get("section_source", "conclusions"),
        "phase":              overrides.get("phase", 1),
        "confidence":         overrides.get("confidence", 0.85),
        "gateway_term":       overrides.get("gateway_term", "further research is needed"),
        "keeper_verdict":     overrides.get("keeper_verdict", "pending"),
        "gap_class":          overrides.get("gap_class", "general"),
        "content_vector":     overrides.get("content_vector", _vec),
        "caught_paper_cosmoid": overrides.get("caught_paper_cosmoid", None),
        "caught_at":          overrides.get("caught_at", None),
        "catch_confidence":   overrides.get("catch_confidence", None),
        "source_discipline":  overrides.get("source_discipline", None),
        "target_disciplines": overrides.get("target_disciplines", None),
        "bridge_potential":   overrides.get("bridge_potential", None),
        "bridge_rationale":   overrides.get("bridge_rationale", None),
        "created_at":         overrides.get("created_at", "2024-01-01T00:00:00"),
        "found_at":           overrides.get("found_at", None),
        "found_paper_cosmoid": overrides.get("found_paper_cosmoid", None),
        "rejected_at":        overrides.get("rejected_at", None),
        "rejection_mode":     overrides.get("rejection_mode", None),
        "rejection_notes":    overrides.get("rejection_notes", None),
        "pickup_instructions": overrides.get("pickup_instructions", None),
        "cap_score":          overrides.get("cap_score", None),
    }
    db.execute(text("""
        INSERT INTO gap_endpoints
        (paper_id, declaration_text, section_source, phase, confidence,
         gateway_term, keeper_verdict, gap_class, content_vector,
         caught_paper_cosmoid, caught_at, catch_confidence,
         source_discipline, target_disciplines, bridge_potential, bridge_rationale,
         created_at, found_at, found_paper_cosmoid, rejected_at, rejection_mode,
         rejection_notes, pickup_instructions, cap_score)
        VALUES
        (:paper_id, :declaration_text, :section_source, :phase, :confidence,
         :gateway_term, :keeper_verdict, :gap_class, :content_vector,
         :caught_paper_cosmoid, :caught_at, :catch_confidence,
         :source_discipline, :target_disciplines, :bridge_potential, :bridge_rationale,
         :created_at, :found_at, :found_paper_cosmoid, :rejected_at, :rejection_mode,
         :rejection_notes, :pickup_instructions, :cap_score)
    """), data)
    db.commit()
    return db.execute(text("SELECT last_insert_rowid()")).scalar()


def seed_source(db, **overrides) -> int:
    """Insert a test api_sources row. Returns source_id."""
    data = {
        "name":              overrides.get("name", f"Test Source {_next_id()}"),
        "slug":              overrides.get("slug", f"test_source_{_next_id()}"),
        "base_url":          overrides.get("base_url", "https://example.com/api"),
        "auth_type":         overrides.get("auth_type", "none"),
        "api_key_env":       overrides.get("api_key_env", None),
        "email_env":         overrides.get("email_env", None),
        "response_format":   overrides.get("response_format", "json"),
        "rate_limit_per_sec": overrides.get("rate_limit_per_sec", 3.0),
        "status":            overrides.get("status", "active"),
        "notes":             overrides.get("notes", None),
        "now":               "2024-01-01T00:00:00",
    }
    db.execute(text("""
        INSERT INTO api_sources
        (name, slug, base_url, auth_type, api_key_env, email_env,
         response_format, rate_limit_per_sec, status, notes, created_at, updated_at)
        VALUES
        (:name, :slug, :base_url, :auth_type, :api_key_env, :email_env,
         :response_format, :rate_limit_per_sec, :status, :notes, :now, :now)
    """), data)
    db.commit()
    return db.execute(text("SELECT last_insert_rowid()")).scalar()


def seed_journal(db, source_id: int, **overrides) -> int:
    """Insert a test journal_registry row. Returns journal_id."""
    data = {
        "source_id":     source_id,
        "journal_name":  overrides.get("journal_name", f"Test Journal {_next_id()}"),
        "issn":          overrides.get("issn", None),
        "nlm_id":        overrides.get("nlm_id", None),
        "search_query":  overrides.get("search_query", "test query"),
        "oai_endpoint":  overrides.get("oai_endpoint", None),
        "article_count": overrides.get("article_count", 0),
        "status":        overrides.get("status", "active"),
        "notes":         overrides.get("notes", None),
        "now":           "2024-01-01T00:00:00",
    }
    db.execute(text("""
        INSERT INTO journal_registry
        (source_id, journal_name, issn, nlm_id, search_query, oai_endpoint,
         article_count, status, notes, created_at)
        VALUES
        (:source_id, :journal_name, :issn, :nlm_id, :search_query, :oai_endpoint,
         :article_count, :status, :notes, :now)
    """), data)
    db.commit()
    return db.execute(text("SELECT last_insert_rowid()")).scalar()


def seed_ingest_run(db, **overrides) -> int:
    """Insert a test ingest_runs row. Returns run_id."""
    data = {
        "query_term":    overrides.get("query_term", "test query"),
        "pmcids_fetched": overrides.get("pmcids_fetched", 5),
        "pmcids_parsed": overrides.get("pmcids_parsed", 3),
        "gaps_found":    overrides.get("gaps_found", 2),
        "started_at":    overrides.get("started_at", "2024-01-01T00:00:00"),
        "completed_at":  overrides.get("completed_at", "2024-01-01T00:01:00"),
        "status":        overrides.get("status", "complete"),
        "notes":         overrides.get("notes", None),
    }
    db.execute(text("""
        INSERT INTO ingest_runs
        (query_term, pmcids_fetched, pmcids_parsed, gaps_found,
         started_at, completed_at, status, notes)
        VALUES
        (:query_term, :pmcids_fetched, :pmcids_parsed, :gaps_found,
         :started_at, :completed_at, :status, :notes)
    """), data)
    db.commit()
    return db.execute(text("SELECT last_insert_rowid()")).scalar()


# Minimal BioC fixture (reused across ingest and pipeline tests)
BIOC_FIXTURE = {
    "documents": [{
        "passages": [
            {"infons": {"type": "title"}, "text": "Test Paper Title"},
            {
                "infons": {
                    "type": "abstract",
                    "article-id_doi": "10.1234/test.2024",
                },
                "text": (
                    "Further research is needed in this area. "
                    "No study has examined the relationship between X and Y. "
                    "Future work should address this gap."
                ),
            },
            {
                "infons": {"type": "conclusions"},
                "text": (
                    "This gap remains unresolved. "
                    "Future experiments are needed to determine the underlying mechanism."
                ),
            },
        ],
        "id": "12345",
        "infons": {
            "journal": "Test Journal",
            "year": "2024",
        },
    }],
    "date": "2024",
    "key": "open_access_subset",
}
