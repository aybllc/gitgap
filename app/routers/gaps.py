"""
gitgap — Gaps API router
Browse, search, review, and context-search gap endpoints.
"""

import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone
import httpx, json

from ..database import get_db
from ..ingest.embeddings import embed_text, cosine_distance, json_to_vector, vector_to_json
from ..ingest.classify import classify_gap
from ..services.disciplines import enrich_discipline
from ..services.convergence import cluster_gaps, get_agreed_gap_ids, get_convergence_summary
from ..services.cap_score import compute_cap, recompute_all_cap

router = APIRouter(prefix="/gaps", tags=["gaps"])

EAIOU_API = os.getenv("EAIOU_API_URL", "http://127.0.0.1:8000")
EAIOU_MASTER_API_KEY = os.getenv("EAIOU_MASTER_API_KEY", "")


def _notify_eaiou_rescore():
    """Best-effort notification to eaiou after a new gap is pinned (F1-D)."""
    if not EAIOU_MASTER_API_KEY:
        return
    try:
        with httpx.Client(timeout=2) as client:
            client.post(
                f"{EAIOU_API}/author/wheelhouse/rescore-all",
                headers={"X-API-Key": EAIOU_MASTER_API_KEY},
            )
    except Exception:
        pass


@router.get("/")
def list_gaps(
    phase: int = Query(None, description="Filter by phase (1 or 2)"),
    verdict: str = Query(None, description="Filter by keeper_verdict"),
    term: str = Query(None, description="Filter by gateway_term"),
    gap_class: str = Query(None, description="Filter by gap_class"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """
    List all gap endpoints.
    Ordered by confidence desc — highest signal first.
    """
    where = ["1=1"]
    params = {"limit": limit, "offset": offset}

    if phase is not None:
        where.append("g.phase = :phase")
        params["phase"] = phase
    if verdict:
        where.append("g.keeper_verdict = :verdict")
        params["verdict"] = verdict
    if term:
        where.append("g.gateway_term LIKE :term")
        params["term"] = f"%{term}%"
    if gap_class:
        where.append("g.gap_class = :gap_class")
        params["gap_class"] = gap_class

    where_clause = " AND ".join(where)

    rows = db.execute(text(f"""
        SELECT
            g.id, g.paper_id, g.declaration_text, g.section_source,
            g.phase, g.confidence, g.gateway_term, g.gap_class,
            g.keeper_reviewed, g.keeper_verdict, g.created_at,
            p.pmcid, p.doi, p.title, p.journal, p.pub_year
        FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        WHERE {where_clause}
        ORDER BY g.confidence DESC, g.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        WHERE {where_clause}
    """), {k: v for k, v in params.items()
           if k not in ("limit", "offset")}).scalar()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "gaps": [dict(r) for r in rows],
    }


@router.get("/search")
def context_search(
    q: str = Query(..., description="Free-text query — finds semantically related gaps"),
    gap_class: str = Query(None, description="Filter by gap class (methodology, scope, empirical, theoretical, replication, general)"),
    limit: int = Query(10, le=50),
    min_score: float = Query(0.0, description="Minimum similarity score (0.0–1.0)"),
    db: Session = Depends(get_db),
):
    """
    Semantic context search across all gap declarations.
    Embeds the query using the same character n-gram model used at ingest,
    then ranks gaps by cosine similarity. Returns top N matches.

    Similarity score: 1.0 = identical, 0.0 = unrelated.
    """
    query_vec = embed_text(q)

    # Fetch all gaps with stored content_vector (skip any null — legacy pre-migration)
    where = ["content_vector IS NOT NULL"]
    params = {}
    if gap_class:
        where.append("gap_class = :gap_class")
        params["gap_class"] = gap_class

    where_clause = " AND ".join(where)

    rows = db.execute(text(f"""
        SELECT
            g.id, g.declaration_text, g.section_source, g.phase,
            g.confidence, g.gateway_term, g.gap_class,
            g.keeper_verdict, g.content_vector, g.created_at,
            p.pmcid, p.title, p.journal, p.pub_year
        FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        WHERE {where_clause}
    """), params).mappings().all()

    scored = []
    for row in rows:
        stored_vec = json_to_vector(row["content_vector"])
        dist = cosine_distance(query_vec, stored_vec)
        similarity = round(1.0 - dist, 4)
        if similarity >= min_score:
            scored.append({**dict(row), "similarity": similarity, "content_vector": None})

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return {
        "query": q,
        "total_searched": len(rows),
        "returned": len(scored[:limit]),
        "gaps": scored[:limit],
    }


@router.get("/globe-data")
def globe_data(db: Session = Depends(get_db)):
    """
    Lightweight gap dataset for the 3D globe visualization.
    Returns all gaps with discipline metadata — positions computed client-side.
    Includes F3-A convergence flags: is_agreed marks gaps in agreed-upon clusters.
    """
    rows = db.execute(text("""
        SELECT g.id, g.declaration_text, g.gateway_term, g.gap_class,
               g.confidence, g.keeper_verdict, g.cap_score,
               g.source_discipline, g.target_disciplines,
               g.bridge_potential, g.caught_paper_cosmoid,
               g.found_at, g.found_paper_cosmoid,
               g.rejected_at, g.rejection_mode,
               p.title, p.pmcid, p.pub_year
        FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        ORDER BY g.bridge_potential DESC NULLS LAST, g.confidence DESC
    """)).mappings().all()

    # F3-A: agreed gap IDs for fused spike rendering
    agreed_ids = get_agreed_gap_ids(db)

    gaps = []
    for row in rows:
        g = dict(row)
        try:
            g["target_disciplines"] = json.loads(g.get("target_disciplines") or "[]")
        except Exception:
            g["target_disciplines"] = []
        g["declaration_short"] = (g["declaration_text"] or "")[:200]
        del g["declaration_text"]
        g["caught"]    = bool(g.get("caught_paper_cosmoid"))
        g["found"]     = bool(g.get("found_at"))
        g["rejected"]  = bool(g.get("rejected_at"))
        g["is_agreed"] = g["id"] in agreed_ids
        del g["caught_paper_cosmoid"]
        # Keep found_paper_cosmoid for hover panel; strip the raw timestamps
        g.pop("found_at", None)
        g.pop("rejected_at", None)
        gaps.append(g)

    return {"gaps": gaps, "total": len(gaps), "agreed_count": len(agreed_ids)}


@router.get("/structural-holes")
def structural_holes(
    source: str = Query(None, description="Source discipline (e.g. computer_science)"),
    target: str = Query(None, description="Target discipline that lacks this technique"),
    min_bridge: float = Query(0.5, description="Minimum bridge_potential score"),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """
    Structural hole query: find gaps where a technique from one discipline
    has not crossed into another.

    Example: source=computer_science&target=psychology
    Returns methodology gaps originating in CS whose target_disciplines
    include psychology — the structural hole between those two clusters.
    """
    where = ["bridge_potential >= :min_bridge", "source_discipline IS NOT NULL"]
    params: dict = {"min_bridge": min_bridge, "limit": limit}

    if source:
        where.append("source_discipline = :source")
        params["source"] = source

    where_clause = " AND ".join(where)

    rows = db.execute(text(f"""
        SELECT
            g.id, g.declaration_text, g.section_source, g.gateway_term,
            g.gap_class, g.confidence, g.keeper_verdict,
            g.source_discipline, g.target_disciplines,
            g.bridge_potential, g.bridge_rationale,
            g.caught_paper_cosmoid, g.created_at,
            p.pmcid, p.title, p.journal, p.pub_year
        FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        WHERE {where_clause}
        ORDER BY g.bridge_potential DESC, g.confidence DESC
        LIMIT :limit
    """), params).mappings().all()

    results = []
    for row in rows:
        r = dict(row)
        # Parse target_disciplines JSON
        try:
            r["target_disciplines"] = json.loads(r["target_disciplines"] or "[]")
        except Exception:
            r["target_disciplines"] = []

        # Filter by target discipline if specified
        if target and target not in r["target_disciplines"]:
            continue

        results.append(r)

    return {
        "source_filter": source,
        "target_filter": target,
        "min_bridge": min_bridge,
        "total": len(results),
        "holes": results,
    }


@router.get("/stats")
def gap_stats(db: Session = Depends(get_db)):
    """Overview stats for the gap index."""
    papers = db.execute(text("SELECT COUNT(*) FROM papers")).scalar()
    total  = db.execute(text("SELECT COUNT(*) FROM gap_endpoints")).scalar()
    p1     = db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE phase=1")).scalar()
    p2     = db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE phase=2")).scalar()
    go     = db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE keeper_verdict='pass'")).scalar()
    pending= db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE keeper_verdict='pending'")).scalar()

    terms  = db.execute(text("""
        SELECT gateway_term, COUNT(*) as cnt
        FROM gap_endpoints GROUP BY gateway_term
        ORDER BY cnt DESC LIMIT 10
    """)).mappings().all()

    classes = db.execute(text("""
        SELECT gap_class, COUNT(*) as cnt
        FROM gap_endpoints GROUP BY gap_class
        ORDER BY cnt DESC
    """)).mappings().all()

    return {
        "papers_ingested": papers,
        "total_candidates": total,
        "phase_1": p1,
        "phase_2": p2,
        "keeper_passed": go,
        "keeper_pending": pending,
        "top_terms": [dict(t) for t in terms],
        "by_class": [dict(c) for c in classes],
    }


class PinRequest(BaseModel):
    declaration_text: str
    gateway_term: str
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    source_title: Optional[str] = None
    source_journal: Optional[str] = None
    pub_year: Optional[int] = None
    section_source: Optional[str] = "conclusions"
    confidence: Optional[float] = 0.90
    phase: Optional[int] = 1
    # Agent provides its own certainty score (0–1). Distinct from cosine confidence.
    agent_confidence: Optional[float] = None


@router.post("/pin")
def pin_gap(payload: PinRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    AI-facing targeted gap insertion.
    An agent reads a specific paper, identifies the gap, and registers it precisely.
    Produces higher-confidence gaps than the bulk ingest pipeline.

    If pmcid is provided, the endpoint fetches abstract + conclusions from PubMed BioC
    and stores them as context for the investigation phase.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Resolve or create the source paper record
    paper_id = None
    abstract_text = None
    conclusions_text = None

    if payload.pmcid:
        # Check if paper already ingested
        existing = db.execute(text(
            "SELECT id FROM papers WHERE pmcid = :pmcid"
        ), {"pmcid": payload.pmcid}).fetchone()

        if existing:
            paper_id = existing[0]
            ctx = db.execute(text(
                "SELECT abstract_text, conclusions_text FROM papers WHERE id = :id"
            ), {"id": paper_id}).mappings().first()
            if ctx:
                abstract_text = ctx["abstract_text"]
                conclusions_text = ctx["conclusions_text"]
        else:
            # Fetch from PubMed BioC
            try:
                r = httpx.get(
                    f"https://www.ncbi.nlm.nih.gov/research/bioxref/api/v2/paper/PMC{payload.pmcid}",
                    timeout=8,
                )
                if r.status_code != 200:
                    r = httpx.get(
                        f"https://www.ncbi.nlm.nih.gov/research/bioxref/api/v1/paper/PMC{payload.pmcid}",
                        timeout=8,
                    )
            except Exception:
                r = None

            # Minimal paper record — context fields may be null if fetch failed
            db.execute(text("""
                INSERT INTO papers
                    (pmcid, doi, title, journal, pub_year, abstract_text, conclusions_text, ingested_at)
                VALUES (:pmcid, :doi, :title, :journal, :pub_year, :abstract, :conclusions, :now)
            """), {
                "pmcid": payload.pmcid,
                "doi": payload.doi,
                "title": payload.source_title,
                "journal": payload.source_journal,
                "pub_year": payload.pub_year,
                "abstract": abstract_text,
                "conclusions": conclusions_text,
                "now": now,
            })
            db.commit()
            result = db.execute(text("SELECT last_insert_rowid()")).fetchone()
            paper_id = result[0]
    else:
        # No pmcid — create a minimal stub paper record
        db.execute(text("""
            INSERT INTO papers
                (pmcid, doi, title, journal, pub_year, ingested_at)
            VALUES (:pmcid, :doi, :title, :journal, :pub_year, :now)
        """), {
            "pmcid": payload.pmcid or "PINNED",
            "doi": payload.doi,
            "title": payload.source_title,
            "journal": payload.source_journal,
            "pub_year": payload.pub_year,
            "now": now,
        })
        db.commit()
        result = db.execute(text("SELECT last_insert_rowid()")).fetchone()
        paper_id = result[0]

    # Classify and embed the declaration
    gap_class = classify_gap(payload.declaration_text, payload.gateway_term)
    vec = embed_text(payload.declaration_text)
    vec_json = vector_to_json(vec)

    # Discipline enrichment — structural hole analysis
    enrichment = enrich_discipline(
        payload.declaration_text,
        payload.gateway_term,
        payload.source_title or "",
    )

    db.execute(text("""
        INSERT INTO gap_endpoints
            (paper_id, declaration_text, section_source, phase, confidence,
             gateway_term, gap_class, content_vector,
             source_discipline, target_disciplines, bridge_potential, bridge_rationale,
             created_at)
        VALUES
            (:paper_id, :decl, :section, :phase, :conf,
             :term, :gap_class, :vec,
             :src_disc, :tgt_disc, :bridge, :rationale,
             :now)
    """), {
        "paper_id": paper_id,
        "decl": payload.declaration_text,
        "section": payload.section_source,
        "phase": payload.phase,
        "conf": payload.agent_confidence or payload.confidence,
        "term": payload.gateway_term,
        "gap_class": gap_class,
        "vec": vec_json,
        "src_disc": enrichment["source_discipline"],
        "tgt_disc": json.dumps(enrichment["target_disciplines"]),
        "bridge": enrichment["bridge_potential"],
        "rationale": enrichment["bridge_rationale"],
        "now": now,
    })
    db.commit()

    gap_id = db.execute(text("SELECT last_insert_rowid()")).fetchone()[0]

    # F3-B: Compute initial CAP score for the newly pinned gap
    try:
        compute_cap(gap_id, db)
    except Exception:
        pass  # Non-fatal

    # F1-D: Trigger wheelhouse rescore in eaiou after pin completes
    background_tasks.add_task(_notify_eaiou_rescore)

    return {
        "id": gap_id,
        "paper_id": paper_id,
        "gap_class": gap_class,
        "gateway_term": payload.gateway_term,
        "declaration_text": payload.declaration_text,
        "pmcid": payload.pmcid,
        "source_discipline": enrichment["source_discipline"],
        "target_disciplines": enrichment["target_disciplines"],
        "bridge_potential": enrichment["bridge_potential"],
        "bridge_rationale": enrichment["bridge_rationale"],
        "enrich_method": enrichment.get("method"),
        "status": "pinned",
    }


@router.post("/cap/recompute-all")
def cap_recompute_all(db: Session = Depends(get_db)):
    """
    F3-B: Recompute CAP scores for every gap in the index.
    Run after convergence/run to capture cluster-level EC and MS.
    """
    result = recompute_all_cap(db)
    return {"status": "complete", **result}


@router.post("/cap/recompute/{gap_id}")
def cap_recompute_one(gap_id: int, db: Session = Depends(get_db)):
    """F3-B: Recompute CAP score for a single gap."""
    if not db.execute(text("SELECT id FROM gap_endpoints WHERE id = :id"),
                      {"id": gap_id}).scalar():
        raise HTTPException(status_code=404, detail="Gap not found")
    result = compute_cap(gap_id, db)
    return result


@router.post("/convergence/run")
def run_convergence(
    threshold: float = Query(0.25, description="Cosine distance threshold for clustering (lower = tighter clusters)"),
    db: Session = Depends(get_db),
):
    """
    F3-A: Recompute convergence clusters across all vectorised gaps.
    O(n²) — run on demand, not on every insert.

    threshold=0.25: gaps with cosine_distance < 0.25 are considered to address
    the same underlying problem.
    """
    result = cluster_gaps(db, threshold=threshold)
    return {
        "status": "complete",
        **result,
    }


@router.get("/convergence")
def list_convergence(
    agreed_only: bool = Query(False, description="Return only agreed-upon clusters (≥3 members from ≥2 papers)"),
    db: Session = Depends(get_db),
):
    """
    F3-A: Return convergence clusters with representative gap declarations.
    An 'agreed-upon gap' (is_agreed=True) has ≥3 members from ≥2 different papers —
    meaning at least two independent sources identified the same unresolved problem.
    """
    groups = get_convergence_summary(db)
    if agreed_only:
        groups = [g for g in groups if g["is_agreed"]]

    total          = len(groups)
    agreed_count   = sum(1 for g in groups if g["is_agreed"])
    total_members  = sum(g["member_count"] for g in groups)

    return {
        "total_clusters": total,
        "agreed_clusters": agreed_count,
        "total_members": total_members,
        "clusters": groups,
    }


@router.get("/dial")
async def gaps_dial(db: Session = Depends(get_db)):
    """
    Lightweight gap list for the left-sidebar dial widget.
    Returns up to 2000 gaps ordered by id DESC (newest first).
    Must be defined before /{gap_id} to avoid route conflict.
    """
    rows = db.execute(text("""
        SELECT
            g.id,
            COALESCE(g.gateway_term, SUBSTR(g.declaration_text, 1, 40)) AS gateway_term,
            g.confidence,
            g.cap_score,
            g.keeper_verdict,
            g.source_discipline,
            p.pub_year
        FROM gap_endpoints g
        LEFT JOIN papers p ON p.id = g.paper_id
        ORDER BY g.id DESC
        LIMIT 2000
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{gap_id}")
def get_gap(gap_id: int, db: Session = Depends(get_db)):
    """Single gap endpoint with full paper context."""
    row = db.execute(text("""
        SELECT
            g.id, g.declaration_text, g.section_source,
            g.phase, g.confidence, g.gateway_term, g.gap_class,
            g.keeper_reviewed, g.keeper_verdict, g.created_at,
            g.caught_paper_cosmoid, g.caught_at, g.catch_confidence,
            p.pmcid, p.doi, p.title, p.journal, p.pub_year,
            p.abstract_text, p.conclusions_text
        FROM gap_endpoints g
        JOIN papers p ON p.id = g.paper_id
        WHERE g.id = :id
    """), {"id": gap_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Gap not found")

    return dict(row)


@router.post("/{gap_id}/review")
def keeper_review(
    gap_id: int,
    verdict: str = Query(..., description="pass or fail"),
    db: Session = Depends(get_db),
):
    """
    Keeper review — human validates or rejects a gap candidate.
    The Appreciated Gateway must be evaluated by a human keeper.
    """
    if verdict not in ("pass", "fail"):
        raise HTTPException(status_code=422, detail="verdict must be 'pass' or 'fail'")

    result = db.execute(text(
        "UPDATE gap_endpoints SET keeper_reviewed=1, keeper_verdict=:v "
        "WHERE id=:id"
    ), {"v": verdict, "id": gap_id})
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Gap not found")

    return {"id": gap_id, "verdict": verdict, "status": "recorded"}


@router.post("/{gap_id}/catch")
def record_catch(
    gap_id: int,
    paper_cosmoid: str = Body(..., embed=True),
    catch_confidence: float = Body(None, embed=True, description="Cosine similarity between paper abstract and gap declaration (0–1)"),
    db: Session = Depends(get_db),
):
    """
    Record that a gap has been CAUGHT — an eaiou paper claims to resolve it.
    Called by eaiou on paper submission when gitgap_gap_id is set.
    catch_confidence is the cosine similarity between the paper abstract and gap
    declaration — a permanent, computed measure of alignment, not a gate.
    """
    row = db.execute(text(
        "SELECT id, caught_paper_cosmoid FROM gap_endpoints WHERE id = :id"
    ), {"id": gap_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Gap not found")

    now = datetime.now(timezone.utc).isoformat()
    db.execute(text(
        "UPDATE gap_endpoints "
        "SET caught_paper_cosmoid = :cosmoid, caught_at = :now, catch_confidence = :conf "
        "WHERE id = :id"
    ), {"cosmoid": paper_cosmoid, "now": now, "conf": catch_confidence, "id": gap_id})
    db.commit()

    return {
        "id": gap_id,
        "caught_paper_cosmoid": paper_cosmoid,
        "caught_at": now,
        "catch_confidence": catch_confidence,
        "status": "caught",
        "previously_caught": row["caught_paper_cosmoid"],
    }


@router.post("/{gap_id}/found")
def mark_found(
    gap_id: int,
    found_paper_cosmoid: str = Body(..., embed=True, description="eaiou CosmoID of the paper that resolved this gap"),
    found_paper_doi: str = Body("", embed=True, description="DOI of the resolving paper (optional)"),
    db: Session = Depends(get_db),
):
    """
    Mark a gap as FOUND — the lifecycle terminal state.
    Called by eaiou editor when a paper linked to this gap is accepted or published.
    NAUGHT → CAUGHT → FOUND.
    """
    row = db.execute(text(
        "SELECT id, found_at FROM gap_endpoints WHERE id = :id"
    ), {"id": gap_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Gap not found")

    now = datetime.now(timezone.utc).isoformat()
    db.execute(text(
        "UPDATE gap_endpoints "
        "SET found_at = :now, found_paper_cosmoid = :cosmoid, found_paper_doi = :doi "
        "WHERE id = :id"
    ), {"now": now, "cosmoid": found_paper_cosmoid, "doi": found_paper_doi or None, "id": gap_id})
    db.commit()

    return {
        "id":                  gap_id,
        "found_paper_cosmoid": found_paper_cosmoid,
        "found_paper_doi":     found_paper_doi or None,
        "found_at":            now,
        "status":              "found",
        "previously_found":    row["found_at"],
    }


_REJECTION_MODES = {
    "methodology", "scope", "insufficient_evidence",
    "theory_gap", "duplicate", "other",
}


@router.post("/{gap_id}/reject")
def mark_rejected(
    gap_id: int,
    rejection_mode: str = Body("other", embed=True, description="Why the attempt failed"),
    rejection_notes: str = Body("", embed=True, description="Editor/reviewer notes (private)"),
    pickup_instructions: str = Body("", embed=True, description="Guidance for next attempt (public)"),
    db: Session = Depends(get_db),
):
    """
    Record that an attempt to fill this gap was REJECTED at peer review.
    The gap returns to active (NAUGHT/CAUGHT state is preserved) but gains a rejection trail.
    Called by eaiou editor when a paper linked to this gap is rejected.
    """
    if rejection_mode not in _REJECTION_MODES:
        rejection_mode = "other"

    row = db.execute(text(
        "SELECT id FROM gap_endpoints WHERE id = :id"
    ), {"id": gap_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Gap not found")

    now = datetime.now(timezone.utc).isoformat()
    db.execute(text(
        "UPDATE gap_endpoints "
        "SET rejected_at = :now, rejection_mode = :mode, "
        "    rejection_notes = :notes, pickup_instructions = :pickup "
        "WHERE id = :id"
    ), {
        "now":    now,
        "mode":   rejection_mode,
        "notes":  rejection_notes or None,
        "pickup": pickup_instructions or None,
        "id":     gap_id,
    })
    db.commit()

    return {
        "id":              gap_id,
        "rejected_at":     now,
        "rejection_mode":  rejection_mode,
        "pickup_instructions": pickup_instructions or None,
        "status":          "rejected",
    }


