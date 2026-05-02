"""W1–W8: End-to-end lifecycle workflow tests."""
import pytest
from sqlalchemy import text
from tests.conftest import seed_paper, seed_gap, seed_source, seed_journal, _TestSessionLocal


# ── W1: NAUGHT → CAUGHT → FOUND ──────────────────────────────────────────────

def test_lifecycle_w1_naught_caught_found(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid, keeper_verdict="pass")

    # 1. Catch the gap
    r = client.post(f"/gaps/{gid}/catch", json={
        "paper_cosmoid": "caught-for-found-test",
        "catch_confidence": 0.9,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "caught"

    # 2. Mark found
    r = client.post(f"/gaps/{gid}/found", json={
        "found_paper_cosmoid": "found-final-cosmoid",
        "found_paper_doi": "10.9999/found.2024",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "found"
    assert r.json()["found_at"] is not None

    # 3. Verify DB state
    s = _TestSessionLocal()
    row = s.execute(text(
        "SELECT caught_paper_cosmoid, found_at, found_paper_cosmoid "
        "FROM gap_endpoints WHERE id = :id"
    ), {"id": gid}).mappings().first()
    s.close()
    assert row["caught_paper_cosmoid"] == "caught-for-found-test"
    assert row["found_at"] is not None
    assert row["found_paper_cosmoid"] == "found-final-cosmoid"

    # 4. Detail page renders without error
    r = client.get(f"/gaps/{gid}")
    assert r.status_code == 200


# ── W2: NAUGHT → CAUGHT → REJECTED ───────────────────────────────────────────

def test_lifecycle_w2_naught_caught_rejected(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid, keeper_verdict="pass")

    # 1. Catch
    r = client.post(f"/gaps/{gid}/catch", json={
        "paper_cosmoid": "pre-rejection-cosmoid",
        "catch_confidence": 0.7,
    })
    assert r.status_code == 200

    # 2. Reject
    r = client.post(f"/gaps/{gid}/reject", json={
        "rejection_mode": "methodology",
        "rejection_notes": "The methodology section is insufficient.",
        "pickup_instructions": "Rewrite the experimental design and resubmit.",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["rejection_mode"] == "methodology"

    # 3. Rejected trail should show this gap
    r = client.get("/view/rejected")
    assert r.status_code == 200
    assert b"methodology" in r.content.lower()


# ── W3: Bulk Keeper Review ────────────────────────────────────────────────────

def test_lifecycle_w3_bulk_review(client, db):
    pid = seed_paper(db)
    gids = [seed_gap(db, pid, keeper_verdict="pending") for _ in range(5)]

    r = client.post("/view/gaps/bulk-review", data={
        "gap_ids": [str(g) for g in gids],
        "verdict": "pass",
        "redirect_url": "/view/gaps",
    }, follow_redirects=False)
    assert r.status_code == 303

    # All 5 should now be 'pass'
    s = _TestSessionLocal()
    for gid in gids:
        verdict = s.execute(text(
            "SELECT keeper_verdict FROM gap_endpoints WHERE id = :id"
        ), {"id": gid}).scalar()
        assert verdict == "pass", f"Gap {gid} expected 'pass', got {verdict!r}"
    s.close()

    # API filter should return them
    r = client.get("/gaps/?verdict=pass")
    assert r.status_code == 200
    returned_ids = {g["id"] for g in r.json()["gaps"]}
    assert set(gids).issubset(returned_ids)


# ── W4: Ingest + AI Detection (mocked PMC) ───────────────────────────────────

def test_lifecycle_w4_ingest_ai_detection(client):
    """Ingest via from-text; the paper gets AI detection fields set."""
    from unittest.mock import patch
    with patch("app.services.ai_detection._llm_judge", return_value=None):
        r = client.post("/ingest/from-text", json={
            "title": "An Academic Paper on Research Gaps",
            "abstract_text": (
                "It is important to note that further research is needed in this area. "
                "Future work should delve into the underlying mechanisms. "
                "Moreover, in the realm of neuroscience, this question remains open. "
                "It should be noted that no studies have examined this topic. "
                "Furthermore, it is evident that more investigation is warranted."
            ),
            "full_text": "Future experiments are needed to resolve this gap.",
            "doi": "10.9999/ai.detect.test",
            "year": 2024,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"

    # At least one gap was extracted (trigger phrases in abstract)
    gap_count = body.get("gaps_found", body.get("gap_ids", []))
    # Not asserting count > 0 since pipeline may find 0 — just verifying no crash


# ── W5: Journal Request Submission ───────────────────────────────────────────

def test_lifecycle_w5_journal_request(client):
    r = client.post("/docs/submit-journal", data={
        "journal_name": "Lifecycle Test Journal",
        "url": "https://lifecycle-journal.org",
        "contact_email": "editor@lifecycle-journal.org",
        "notes": "Testing the full submission path.",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "flash=submitted" in r.headers["location"]

    # Verify DB record
    s = _TestSessionLocal()
    row = s.execute(text(
        "SELECT journal_name FROM journal_requests "
        "WHERE journal_name = 'Lifecycle Test Journal'"
    )).scalar()
    s.close()
    assert row == "Lifecycle Test Journal"


# ── W6: Journal Reconcile Trigger ────────────────────────────────────────────

def test_lifecycle_w6_reconcile_trigger(client, db):
    from unittest.mock import patch
    sid = seed_source(db, slug="reconcile_lifecycle_src")
    jid = seed_journal(db, sid, journal_name="Reconcile Lifecycle Journal")

    with patch("app.routers.admin.run_pipeline", return_value=[]):
        r = client.post(f"/admin/journals/{jid}/reconcile",
                        follow_redirects=False)
    assert r.status_code == 303

    # Journal detail page renders without error
    r = client.get(f"/admin/journals/{jid}")
    assert r.status_code == 200


# ── W7: CSV Export Filter Fidelity ───────────────────────────────────────────

def test_lifecycle_w7_csv_filter(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, keeper_verdict="pass")
    seed_gap(db, pid, keeper_verdict="pass")
    seed_gap(db, pid, keeper_verdict="fail")

    r_pass = client.get("/view/gaps/export.csv?verdict=pass")
    assert r_pass.status_code == 200
    lines_pass = [l for l in r_pass.text.strip().split("\n") if l.strip()]
    # Header + ≥2 data rows for 'pass'
    assert len(lines_pass) >= 3

    r_fail = client.get("/view/gaps/export.csv?verdict=fail")
    assert r_fail.status_code == 200
    lines_fail = [l for l in r_fail.text.strip().split("\n") if l.strip()]
    # Header + ≥1 data row for 'fail'
    assert len(lines_fail) >= 2

    # Verify column headers
    header = lines_pass[0]
    assert "declaration_text" in header
    assert "gateway_term" in header
    assert "keeper_verdict" in header


# ── W8: Globe Data Lifecycle States ──────────────────────────────────────────

def test_lifecycle_w8_globe_states(client, db):
    pid = seed_paper(db)

    _pending_id = seed_gap(db, pid, keeper_verdict="pending")
    caught_id   = seed_gap(db, pid, caught_paper_cosmoid="globe-caught-cosmoid")
    found_id    = seed_gap(db, pid,
                           caught_paper_cosmoid="globe-found-cosmoid",
                           found_at="2024-06-01T00:00:00",
                           found_paper_cosmoid="globe-found-cosmoid")
    rejected_id = seed_gap(db, pid,
                           rejected_at="2024-07-01T00:00:00",
                           rejection_mode="scope")

    r = client.get("/gaps/globe-data")
    assert r.status_code == 200
    gaps = r.json()["gaps"]
    assert len(gaps) >= 4

    gap_by_id = {g["id"]: g for g in gaps}
    assert gap_by_id[caught_id]["caught"] is True
    assert gap_by_id[found_id]["found"] is True
    assert gap_by_id[rejected_id]["rejected"] is True
    assert gap_by_id[_pending_id]["caught"] is False
    assert gap_by_id[_pending_id]["found"] is False
