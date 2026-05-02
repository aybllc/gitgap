"""D1–D15: All /docs/* routes."""
import pytest
from tests.conftest import seed_source, seed_journal


# ── GET /docs/ ────────────────────────────────────────────────────────────────

def test_d1_docs_root_redirect(client):
    r = client.get("/docs/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/docs/quickstart")


# ── Static doc pages ──────────────────────────────────────────────────────────

def test_d2_quickstart(client):
    r = client.get("/docs/quickstart")
    assert r.status_code == 200
    assert b"Quickstart" in r.content or b"quickstart" in r.content.lower()


def test_d3_origin(client):
    r = client.get("/docs/origin")
    assert r.status_code == 200
    assert b"intelligence" in r.content.lower()


def test_d4_workflow(client):
    r = client.get("/docs/workflow")
    assert r.status_code == 200
    assert b"NAUGHT" in r.content


def test_d5_journals(client, db):
    src_id = seed_source(db)
    seed_journal(db, src_id)
    r = client.get("/docs/journals")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_d6_submit_journal_form(client):
    r = client.get("/docs/submit-journal")
    assert r.status_code == 200
    assert b"<form" in r.content


def test_d7_submit_journal_post(client):
    r = client.post("/docs/submit-journal", data={
        "journal_name": "Test Open Access Journal",
        "url": "https://example-journal.org",
        "oai_endpoint": "https://example-journal.org/oai",
        "issn": "1234-5678",
        "contact_email": "editor@example.org",
        "notes": "This is a test submission.",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "flash=submitted" in r.headers["location"]


def test_d8_submit_journal_flash_message(client):
    r = client.get("/docs/submit-journal?flash=submitted")
    assert r.status_code == 200
    # Flash message should appear in page
    assert b"submitted" in r.content.lower() or b"request" in r.content.lower()


def test_d9_api_ingest(client):
    r = client.get("/docs/api-ingest")
    assert r.status_code == 200
    assert b"X-API-Key" in r.content


def test_d10_roadmap(client):
    r = client.get("/docs/roadmap")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_d11_funding(client):
    r = client.get("/docs/funding")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_d12_open_source(client):
    r = client.get("/docs/open-source")
    assert r.status_code == 200
    assert b"MIT" in r.content


def test_d13_ai_policy(client):
    r = client.get("/docs/ai-policy")
    assert r.status_code == 200
    assert b"interrogated" in r.content.lower()


def test_d14_glossary(client):
    r = client.get("/docs/glossary")
    assert r.status_code == 200
    assert b"NAUGHT" in r.content


def test_d15_submit_journal_missing_name(client):
    """journal_name is required — omitting it should fail validation."""
    r = client.post("/docs/submit-journal", data={
        "contact_email": "test@test.com",
    }, follow_redirects=False)
    assert r.status_code == 422
