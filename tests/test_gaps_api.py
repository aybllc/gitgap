"""G1–G26: All /gaps/* API routes."""
import pytest
from tests.conftest import seed_paper, seed_gap


# ── GET /gaps/ ────────────────────────────────────────────────────────────────

def test_g1_list_gaps_empty(client):
    r = client.get("/gaps/")
    assert r.status_code == 200
    body = r.json()
    assert "gaps" in body
    assert "total" in body


def test_g2_list_gaps_filter_verdict(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, keeper_verdict="pending")
    seed_gap(db, pid, keeper_verdict="pass")
    r = client.get("/gaps/?verdict=pending")
    assert r.status_code == 200
    gaps = r.json()["gaps"]
    assert all(g["keeper_verdict"] == "pending" for g in gaps)


def test_g3_list_gaps_filter_pass(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, keeper_verdict="pass")
    r = client.get("/gaps/?verdict=pass")
    assert r.status_code == 200
    gaps = r.json()["gaps"]
    assert all(g["keeper_verdict"] == "pass" for g in gaps)


def test_g4_list_gaps_filter_term(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, gateway_term="cancer_research_needed")
    r = client.get("/gaps/?term=cancer_research")
    assert r.status_code == 200
    gaps = r.json()["gaps"]
    assert len(gaps) >= 1
    assert any("cancer_research" in g["gateway_term"] for g in gaps)


def test_g5_list_gaps_limit(client, db):
    pid = seed_paper(db)
    for _ in range(5):
        seed_gap(db, pid)
    r = client.get("/gaps/?limit=2")
    assert r.status_code == 200
    assert len(r.json()["gaps"]) <= 2


def test_g6_list_gaps_pagination(client, db):
    pid = seed_paper(db)
    for _ in range(6):
        seed_gap(db, pid)
    r1 = client.get("/gaps/?limit=3&offset=0")
    r2 = client.get("/gaps/?limit=3&offset=3")
    assert r1.status_code == 200
    assert r2.status_code == 200
    ids1 = {g["id"] for g in r1.json()["gaps"]}
    ids2 = {g["id"] for g in r2.json()["gaps"]}
    assert ids1.isdisjoint(ids2)


# ── GET /gaps/search ──────────────────────────────────────────────────────────

def test_g7_search_returns_results(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid, declaration_text="Replication of this study is needed in psychology.")
    r = client.get("/gaps/search?q=replication+study")
    assert r.status_code == 200
    body = r.json()
    assert "gaps" in body
    assert "query" in body


def test_g8_search_empty_string(client):
    r = client.get("/gaps/search?q=zzzzz_nonexistent_xyzq")
    assert r.status_code == 200
    assert r.json()["gaps"] == [] or isinstance(r.json()["gaps"], list)


# ── GET /gaps/globe-data ──────────────────────────────────────────────────────

def test_g9_globe_data(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid)
    r = client.get("/gaps/globe-data")
    assert r.status_code == 200
    body = r.json()
    assert "gaps" in body
    assert "total" in body


# ── GET /gaps/structural-holes ────────────────────────────────────────────────

def test_g10_structural_holes_source_filter(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid,
             source_discipline="neuroscience",
             bridge_potential=0.8,
             target_disciplines='["psychology"]')
    r = client.get("/gaps/structural-holes?source=neuroscience&min_bridge=0.5")
    assert r.status_code == 200
    body = r.json()
    assert "holes" in body


def test_g11_structural_holes_min_bridge(client, db):
    pid = seed_paper(db)
    seed_gap(db, pid,
             source_discipline="physics",
             bridge_potential=0.95,
             target_disciplines='["chemistry"]')
    r = client.get("/gaps/structural-holes?min_bridge=0.9")
    assert r.status_code == 200
    holes = r.json()["holes"]
    assert all(h["bridge_potential"] >= 0.9 for h in holes)


# ── GET /gaps/stats ───────────────────────────────────────────────────────────

def test_g12_gap_stats(client):
    r = client.get("/gaps/stats")
    assert r.status_code == 200
    body = r.json()
    assert "total_candidates" in body
    assert "keeper_passed" in body
    assert "keeper_pending" in body
    assert "papers_ingested" in body


# ── GET /gaps/{gap_id} ────────────────────────────────────────────────────────

def test_g13_get_gap_valid(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid, declaration_text="A specific unresolved gap.")
    r = client.get(f"/gaps/{gid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == gid
    assert "declaration_text" in body
    assert "pmcid" in body


def test_g14_get_gap_not_found(client):
    r = client.get("/gaps/999999")
    assert r.status_code == 404


# ── GET /gaps/convergence ─────────────────────────────────────────────────────

def test_g15_convergence_list(client):
    r = client.get("/gaps/convergence?agreed_only=false")
    assert r.status_code == 200
    body = r.json()
    assert "clusters" in body
    assert "total_clusters" in body


# ── POST /gaps/pin ────────────────────────────────────────────────────────────

def test_g16_pin_gap_valid(client):
    r = client.post("/gaps/pin", json={
        "declaration_text": "No study has examined the role of X in Y disease progression.",
        "gateway_term": "further research is needed",
        "source_title": "Test Paper 2024",
        "pub_year": 2024,
    })
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["status"] == "pinned"
    assert "gap_class" in body


def test_g17_pin_gap_missing_declaration(client):
    r = client.post("/gaps/pin", json={
        "gateway_term": "further research is needed",
    })
    assert r.status_code == 422


# ── POST /gaps/cap/recompute-all ──────────────────────────────────────────────

def test_g18_cap_recompute_all(client):
    r = client.post("/gaps/cap/recompute-all")
    assert r.status_code == 200
    body = r.json()
    assert "total" in body
    assert body["status"] == "complete"


# ── POST /gaps/cap/recompute/{gap_id} ─────────────────────────────────────────

def test_g19_cap_recompute_one_valid(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/cap/recompute/{gid}")
    assert r.status_code == 200
    body = r.json()
    assert "gap_id" in body
    assert "cap_score" in body


def test_g20_cap_recompute_one_not_found(client):
    r = client.post("/gaps/cap/recompute/999999")
    assert r.status_code == 404


# ── POST /gaps/convergence/run ────────────────────────────────────────────────

def test_g21_convergence_run(client):
    r = client.post("/gaps/convergence/run?threshold=0.25")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert "clusters" in body


# ── POST /gaps/{gap_id}/review ────────────────────────────────────────────────

def test_g22_review_pass(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/{gid}/review?verdict=pass")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "pass"


def test_g23_review_fail(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/{gid}/review?verdict=fail")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "fail"


def test_g24_review_invalid_verdict(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/{gid}/review?verdict=maybe")
    assert r.status_code == 422


# ── POST /gaps/{gap_id}/catch ─────────────────────────────────────────────────

def test_g25_catch_gap(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/{gid}/catch", json={
        "paper_cosmoid": "abc-123-cosmoid",
        "catch_confidence": 0.85,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["caught_paper_cosmoid"] == "abc-123-cosmoid"
    assert body["status"] == "caught"


# ── POST /gaps/{gap_id}/found ─────────────────────────────────────────────────

def test_g26_found_gap(client, db):
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    r = client.post(f"/gaps/{gid}/found", json={
        "found_paper_cosmoid": "found-xyz-cosmoid",
        "found_paper_doi": "10.9999/test.found",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["found_paper_cosmoid"] == "found-xyz-cosmoid"
    assert body["status"] == "found"
    assert body["found_at"] is not None
