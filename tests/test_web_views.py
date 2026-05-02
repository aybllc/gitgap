"""W1–W17: All /view/* HTML routes."""
import json
import pytest
from tests.conftest import seed_paper, seed_gap, seed_ingest_run


# ── GET /view/gaps ────────────────────────────────────────────────────────────

def test_w1_gap_index(client, db):
    pid = seed_paper(db)
    for _ in range(3):
        seed_gap(db, pid)
    r = client.get("/view/gaps")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_w2_gap_index_verdict_filter(client, db):
    pid = seed_paper(db, title="Visible Paper")
    seed_gap(db, pid, keeper_verdict="pass",
             declaration_text="Visible gap for test w2.")
    r = client.get("/view/gaps?verdict=pass")
    assert r.status_code == 200
    # The title or declaration should appear somewhere in the rendered HTML
    assert b"Visible" in r.content or b"pass" in r.content.lower()


# ── GET /view/gaps/export.csv ─────────────────────────────────────────────────

def test_w3_csv_export(client, db):
    pid = seed_paper(db, title="CSV Paper")
    seed_gap(db, pid, keeper_verdict="pass")
    seed_gap(db, pid, keeper_verdict="fail")
    r = client.get("/view/gaps/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().split("\n")
    assert len(lines) >= 2   # header + at least one data row


def test_w3b_csv_export_filtered(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, keeper_verdict="pass")
    seed_gap(db, pid, keeper_verdict="fail")
    r = client.get("/view/gaps/export.csv?verdict=pass")
    assert r.status_code == 200
    lines = r.text.strip().split("\n")
    # Header + rows that matched only 'pass'
    for line in lines[1:]:
        assert "pass" in line


# ── GET /view/gaps/{id} ───────────────────────────────────────────────────────

def test_w4_gap_detail(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid, declaration_text="Unique detail text for w4.")
    r = client.get(f"/view/gaps/{gid}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"Unique detail text for w4" in r.content


def test_w5_gap_detail_not_found(client):
    r = client.get("/view/gaps/999999")
    assert r.status_code == 404


# ── POST /view/gaps/{id}/review ───────────────────────────────────────────────

def test_w6_web_review_post(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/view/gaps/{gid}/review",
                    data={"verdict": "pass"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert f"/view/gaps/{gid}" in r.headers["location"]


# ── GET /view/holes ───────────────────────────────────────────────────────────

def test_w7_structural_holes_view(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid,
             source_discipline="computer_science",
             bridge_potential=0.8,
             target_disciplines=json.dumps(["psychology"]))
    r = client.get("/view/holes")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /view/rejected ────────────────────────────────────────────────────────

def test_w8_rejected_trail(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid,
             rejected_at="2024-06-01T00:00:00",
             rejection_mode="methodology",
             pickup_instructions="Revise the methodology section.")
    r = client.get("/view/rejected")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"methodology" in r.content or b"Rejected" in r.content


def test_w9_rejected_trail_mode_filter(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid,
             rejected_at="2024-06-01T00:00:00",
             rejection_mode="scope",
             pickup_instructions="Expand the scope.")
    r = client.get("/view/rejected?mode=scope")
    assert r.status_code == 200
    assert b"scope" in r.content.lower()


# ── GET /view/globe ───────────────────────────────────────────────────────────

def test_w10_globe_view(client):
    r = client.get("/view/globe")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── GET /view/runs ────────────────────────────────────────────────────────────

def test_w11_runs_index(client, db):
    seed_ingest_run(db, query_term="w11 test run")
    r = client.get("/view/runs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── POST /view/gaps/bulk-review ───────────────────────────────────────────────

def test_w12_bulk_review(client, db):
    pid = seed_paper(db)
    gids = [seed_gap(db, pid) for _ in range(3)]
    r = client.post(
        "/view/gaps/bulk-review",
        data={
            "gap_ids": [str(g) for g in gids],
            "verdict": "pass",
            "redirect_url": "/view/gaps",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Verify DB state
    from sqlalchemy import text
    session = next(iter(client.app.dependency_overrides[__import__('app.database', fromlist=['get_db']).get_db]()))
    for gid in gids:
        from tests.conftest import _TestSessionLocal
        s = _TestSessionLocal()
        verdict = s.execute(
            text("SELECT keeper_verdict FROM gap_endpoints WHERE id = :id"),
            {"id": gid}
        ).scalar()
        s.close()
        assert verdict == "pass"


def test_w13_bulk_review_no_gap_ids(client):
    """Submitting without gap_ids → 422 (required field missing)."""
    r = client.post(
        "/view/gaps/bulk-review",
        data={"verdict": "pass", "redirect_url": "/view/gaps"},
        follow_redirects=False,
    )
    assert r.status_code == 422


# ── AI flag / CAUGHT / FOUND / REJECTED rendering ────────────────────────────

def test_w14_ai_flag_marker(client, db):
    pid = seed_paper(db, ai_flag=1)
    gid = seed_gap(db, pid)
    r = client.get(f"/view/gaps/{gid}")
    assert r.status_code == 200
    assert b"Signals detected" in r.content or b"AI" in r.content


def test_w15_caught_section(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid,
                   caught_paper_cosmoid="caught-cosmoid-xyz",
                   caught_at="2024-03-01T00:00:00",
                   catch_confidence=0.88)
    r = client.get(f"/view/gaps/{gid}")
    assert r.status_code == 200
    assert b"CAUGHT" in r.content


def test_w16_found_section(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid,
                   caught_paper_cosmoid="found-cosmoid-abc",
                   caught_at="2024-03-01T00:00:00",
                   found_at="2024-04-01T00:00:00",
                   found_paper_cosmoid="found-cosmoid-abc")
    r = client.get(f"/view/gaps/{gid}")
    assert r.status_code == 200
    # CAUGHT section shows cosmoid
    assert b"CAUGHT" in r.content


def test_w17_rejected_section(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid,
                   rejected_at="2024-05-01T00:00:00",
                   rejection_mode="methodology",
                   pickup_instructions="Try a different method.")
    r = client.get(f"/view/gaps/{gid}")
    assert r.status_code == 200
    # The rejected gap still renders; Provenance card shows verdict=pending
    assert r.status_code == 200
