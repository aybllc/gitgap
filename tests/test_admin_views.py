"""A1–A20: All /admin/* routes."""
import pytest
from sqlalchemy import text
from tests.conftest import seed_source, seed_journal, _TestSessionLocal


# ── GET /admin/ ───────────────────────────────────────────────────────────────

def test_a1_dashboard(client, db):
    seed_source(db)
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /admin/sources ────────────────────────────────────────────────────────

def test_a2_sources_index(client, db):
    seed_source(db)
    r = client.get("/admin/sources")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /admin/sources/{id} ───────────────────────────────────────────────────

def test_a3_source_detail(client, db):
    sid = seed_source(db, name="Detail Source", slug="detail_source_uniq")
    r = client.get(f"/admin/sources/{sid}")
    assert r.status_code == 200
    assert b"Detail Source" in r.content


def test_a18_source_detail_not_found(client):
    r = client.get("/admin/sources/999999")
    assert r.status_code == 404


# ── POST /admin/sources ───────────────────────────────────────────────────────

def test_a4_create_source(client):
    r = client.post("/admin/sources", data={
        "name": "New Test Source",
        "slug": "new_test_source_unique",
        "base_url": "https://newapi.example.com",
        "auth_type": "none",
        "response_format": "json",
        "rate_limit": "5.0",
        "notes": "Created in test.",
    }, follow_redirects=False)
    assert r.status_code == 303
    # Verify in DB
    s = _TestSessionLocal()
    row = s.execute(text("SELECT id FROM api_sources WHERE slug = 'new_test_source_unique'")).scalar()
    s.close()
    assert row is not None


# ── POST /admin/sources/{id}/edit ─────────────────────────────────────────────

def test_a5_edit_source(client, db):
    sid = seed_source(db, name="Editable Source", slug="editable_source_x")
    r = client.post(f"/admin/sources/{sid}/edit", data={
        "name": "Edited Source Name",
        "base_url": "https://edited.example.com",
        "auth_type": "none",
        "response_format": "json",
        "rate_limit": "3.0",
        "status": "active",
        "notes": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    name = s.execute(text("SELECT name FROM api_sources WHERE id = :id"), {"id": sid}).scalar()
    s.close()
    assert name == "Edited Source Name"


# ── POST /admin/sources/{id}/delete ──────────────────────────────────────────

def test_a6_delete_source(client, db):
    sid = seed_source(db, name="Delete Me", slug="delete_me_source")
    r = client.post(f"/admin/sources/{sid}/delete", follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    row = s.execute(text("SELECT id FROM api_sources WHERE id = :id"), {"id": sid}).scalar()
    s.close()
    assert row is None


# ── POST /admin/sources/{id}/mappings ─────────────────────────────────────────

def test_a7_add_mapping(client, db):
    sid = seed_source(db, name="Mapping Source", slug="mapping_src_uniq")
    r = client.post(f"/admin/sources/{sid}/mappings", data={
        "source_field": "documents[0].infons.title",
        "target_field": "title",
        "transform": "none",
        "required": "1",
        "default_value": "",
        "notes": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    mid = s.execute(text(
        "SELECT id FROM api_field_mappings WHERE source_id = :sid AND target_field = 'title'",
    ), {"sid": sid}).scalar()
    s.close()
    assert mid is not None


# ── POST /admin/sources/{id}/mappings/{mid}/delete ────────────────────────────

def test_a8_delete_mapping(client, db):
    sid = seed_source(db, name="Delete Mapping Src", slug="del_map_src_uniq")
    # Seed a mapping directly
    db.execute(text("""
        INSERT INTO api_field_mappings
        (source_id, source_field, target_table, target_field, required)
        VALUES (:sid, 'test_field', 'papers', 'title', 0)
    """), {"sid": sid})
    db.commit()
    mid = db.execute(text("SELECT last_insert_rowid()")).scalar()

    r = client.post(f"/admin/sources/{sid}/mappings/{mid}/delete",
                    follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    row = s.execute(text("SELECT id FROM api_field_mappings WHERE id = :id"), {"id": mid}).scalar()
    s.close()
    assert row is None


# ── GET /admin/journals ───────────────────────────────────────────────────────

def test_a9_journals_index(client, db):
    sid = seed_source(db, slug="journals_idx_src")
    seed_journal(db, sid)
    r = client.get("/admin/journals")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /admin/journals/{id} ──────────────────────────────────────────────────

def test_a10_journal_detail(client, db):
    sid = seed_source(db, slug="jrnl_detail_src")
    jid = seed_journal(db, sid, journal_name="Detailed Journal")
    r = client.get(f"/admin/journals/{jid}")
    assert r.status_code == 200
    assert b"Detailed Journal" in r.content


def test_a19_journal_detail_not_found(client):
    r = client.get("/admin/journals/999999")
    assert r.status_code == 404


# ── POST /admin/journals ──────────────────────────────────────────────────────

def test_a11_create_journal(client, db):
    sid = seed_source(db, slug="create_jrnl_src")
    r = client.post("/admin/journals", data={
        "source_id": str(sid),
        "journal_name": "Created Test Journal",
        "issn": "9999-9999",
        "nlm_id": "",
        "search_query": "created journal test",
        "oai_endpoint": "",
        "notes": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    row = s.execute(text(
        "SELECT id FROM journal_registry WHERE journal_name = 'Created Test Journal'"
    )).scalar()
    s.close()
    assert row is not None


# ── POST /admin/journals/discover ─────────────────────────────────────────────

def test_a12_discover_journal(client):
    from unittest.mock import patch
    with patch("app.routers.admin.search_pmc", return_value=["PMC11111", "PMC22222"]), \
         patch("app.routers.admin.fetch_bioc", return_value=None):
        r = client.post("/admin/journals/discover",
                        data={"journal_name": "Nature"},
                        follow_redirects=False)
    assert r.status_code == 303


# ── POST /admin/journals/{id}/edit ────────────────────────────────────────────

def test_a13_edit_journal(client, db):
    sid = seed_source(db, slug="edit_jrnl_src")
    jid = seed_journal(db, sid, journal_name="Before Edit")
    r = client.post(f"/admin/journals/{jid}/edit", data={
        "journal_name": "After Edit",
        "issn": "",
        "nlm_id": "",
        "search_query": "updated query",
        "oai_endpoint": "https://new-oai.example.org",
        "status": "active",
        "notes": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    name = s.execute(text("SELECT journal_name FROM journal_registry WHERE id = :id"),
                     {"id": jid}).scalar()
    s.close()
    assert name == "After Edit"


# ── POST /admin/journals/{id}/delete ─────────────────────────────────────────

def test_a14_delete_journal(client, db):
    sid = seed_source(db, slug="del_jrnl_src")
    jid = seed_journal(db, sid, journal_name="Journal To Delete")
    r = client.post(f"/admin/journals/{jid}/delete", follow_redirects=False)
    assert r.status_code == 303
    s = _TestSessionLocal()
    row = s.execute(text("SELECT id FROM journal_registry WHERE id = :id"), {"id": jid}).scalar()
    s.close()
    assert row is None


# ── POST /admin/journals/{id}/reconcile ──────────────────────────────────────

def test_a15_reconcile_journal(client, db):
    from unittest.mock import patch
    sid = seed_source(db, slug="reconcile_src_x")
    jid = seed_journal(db, sid, journal_name="Reconcile Journal")
    with patch("app.routers.admin.run_pipeline", return_value=[]):
        r = client.post(f"/admin/journals/{jid}/reconcile",
                        follow_redirects=False)
    assert r.status_code == 303


# ── POST /admin/reconcile-all ─────────────────────────────────────────────────

def test_a16_reconcile_all(client, db):
    from unittest.mock import patch
    sid = seed_source(db, slug="rec_all_src_y")
    seed_journal(db, sid, status="active")
    seed_journal(db, sid, status="active")
    with patch("app.routers.admin.run_pipeline", return_value=[]):
        r = client.post("/admin/reconcile-all", follow_redirects=False)
    assert r.status_code == 303


# ── GET /admin/env ────────────────────────────────────────────────────────────

def test_a17_env_check(client):
    r = client.get("/admin/env")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


# ── POST /admin/sources — missing required field → 422 ───────────────────────

def test_a20_create_source_missing_name(client):
    r = client.post("/admin/sources", data={
        "slug": "no_name_source",
        "base_url": "https://noname.example.com",
    }, follow_redirects=False)
    assert r.status_code == 422


# ── GET /admin/journal-requests ───────────────────────────────────────────────

def test_a21_journal_requests_index(client, db):
    db.execute(text("""
        INSERT INTO journal_requests (journal_name, url, contact_email, status)
        VALUES ('Test Request Journal', 'https://req.example.com', 'req@example.com', 'pending')
    """))
    db.commit()
    r = client.get("/admin/journal-requests")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"Test Request Journal" in r.content


# ── POST /admin/journal-requests/{id}/approve ─────────────────────────────────

def test_a22_approve_journal_request(client, db):
    sid = seed_source(db, slug="approve_req_src")
    db.execute(text("""
        INSERT INTO journal_requests (journal_name, issn, status)
        VALUES ('Approve Me Journal', '0000-0001', 'pending')
    """))
    db.commit()
    req_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

    r = client.post(f"/admin/journal-requests/{req_id}/approve", data={
        "source_id": str(sid),
        "search_query": "approve me test query",
    }, follow_redirects=False)
    assert r.status_code == 303

    s = _TestSessionLocal()
    row = s.execute(text(
        "SELECT id FROM journal_registry WHERE journal_name = 'Approve Me Journal'"
    )).scalar()
    req_status = s.execute(text(
        "SELECT status FROM journal_requests WHERE id = :id"
    ), {"id": req_id}).scalar()
    s.close()
    assert row is not None
    assert req_status == "approved"


# ── POST /admin/journal-requests/{id}/dismiss ─────────────────────────────────

def test_a23_dismiss_journal_request(client, db):
    db.execute(text("""
        INSERT INTO journal_requests (journal_name, status)
        VALUES ('Dismiss Me Journal', 'pending')
    """))
    db.commit()
    req_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

    r = client.post(f"/admin/journal-requests/{req_id}/dismiss",
                    follow_redirects=False)
    assert r.status_code == 303

    s = _TestSessionLocal()
    status = s.execute(text(
        "SELECT status FROM journal_requests WHERE id = :id"
    ), {"id": req_id}).scalar()
    s.close()
    assert status == "dismissed"
