"""I1–I6: All /ingest/* routes (PMC calls mocked)."""
import pytest
from unittest.mock import patch
from tests.conftest import seed_ingest_run, BIOC_FIXTURE


# ── POST /ingest/ingest/run ───────────────────────────────────────────────────

def test_i1_trigger_run_queued(client):
    """Trigger run returns immediately with 'queued' status."""
    with patch("app.ingest.pmc.search_pmc", return_value=["PMC12345"]), \
         patch("app.ingest.pmc.fetch_bioc", return_value=BIOC_FIXTURE):
        r = client.post("/ingest/ingest/run?query=cancer&max_results=5&phase=1")
    assert r.status_code == 200
    body = r.json()
    # Background tasks mode → queued; or synchronous fallback → complete
    assert body["status"] in ("queued", "complete")
    assert body["query"] == "cancer"


# ── GET /ingest/runs ──────────────────────────────────────────────────────────

def test_i2_list_runs_empty(client):
    r = client.get("/ingest/runs")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body
    assert isinstance(body["runs"], list)


def test_i2b_list_runs_with_data(client, db):
    seed_ingest_run(db, query_term="cancer test")
    r = client.get("/ingest/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) >= 1


# ── GET /ingest/runs/{run_id} ─────────────────────────────────────────────────

def test_i3_get_run_valid(client, db):
    run_id = seed_ingest_run(db, query_term="specific run")
    r = client.get(f"/ingest/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id
    assert body["query_term"] == "specific run"


def test_i4_get_run_not_found(client):
    r = client.get("/ingest/runs/999999")
    assert r.status_code == 404


# ── POST /ingest/from-text ────────────────────────────────────────────────────

def test_i5_from_text_valid(client):
    r = client.post("/ingest/from-text", json={
        "title": "A Study on Research Gaps in Neuroscience",
        "abstract_text": (
            "Further research is needed to understand the mechanisms "
            "underlying X. No study has systematically examined Y."
        ),
        "full_text": "Future work should address this gap directly.",
        "doi": "10.9999/test.2024",
        "year": 2024,
        "journal": "Test Journal",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert "gap_ids" in body or "gaps_found" in body


def test_i6_from_text_missing_title(client):
    """title is required — missing it triggers 422."""
    r = client.post("/ingest/from-text", json={
        "abstract_text": "Some abstract text here.",
    })
    assert r.status_code == 422


def test_i6b_from_text_empty_abstract(client):
    """Empty abstract_text raises 422 at route level."""
    r = client.post("/ingest/from-text", json={
        "title": "Test Paper",
        "abstract_text": "   ",
    })
    assert r.status_code == 422
