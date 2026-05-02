"""
gitgap — Web UI router
HTML views for gap browsing and keeper review.
"""

import csv
import io
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db

router = APIRouter(prefix="/view", tags=["web"])
templates = Jinja2Templates(directory="app/templates")


def _get_stats(db: Session) -> dict:
    return {
        "papers_ingested": db.execute(text("SELECT COUNT(*) FROM papers")).scalar() or 0,
        "total_candidates": db.execute(text("SELECT COUNT(*) FROM gap_endpoints")).scalar() or 0,
        "phase_1": db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE phase=1")).scalar() or 0,
        "phase_2": db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE phase=2")).scalar() or 0,
        "keeper_passed": db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE keeper_verdict='pass'")).scalar() or 0,
        "keeper_pending": db.execute(text("SELECT COUNT(*) FROM gap_endpoints WHERE keeper_verdict='pending'")).scalar() or 0,
    }


@router.get("/gaps", response_class=HTMLResponse)
def gap_index(
    request: Request,
    verdict: str = None,
    phase: str = None,
    term: str = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    where = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if verdict:
        where.append("g.keeper_verdict = :verdict")
        params["verdict"] = verdict
    if phase:
        where.append("g.phase = :phase")
        params["phase"] = int(phase)
    if term:
        where.append("g.gateway_term LIKE :term")
        params["term"] = f"%{term}%"

    clause = " AND ".join(where)

    rows = db.execute(text(f"""
        SELECT g.id, g.declaration_text, g.section_source, g.phase,
               g.confidence, g.gateway_term, g.keeper_verdict,
               g.gap_class, g.source_discipline, g.bridge_potential,
               p.pmcid, p.title, p.pub_year
        FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {clause}
        ORDER BY g.confidence DESC, g.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {clause}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar() or 0

    return templates.TemplateResponse(request, "gaps_index.html", {
        "gaps": [dict(r) for r in rows],
        "stats": _get_stats(db),
        "total": total,
        "limit": limit,
        "offset": offset,
        "verdict": verdict,
        "phase_filter": phase,
        "term": term,
    })


@router.get("/gaps/export.csv")
def export_gaps_csv(
    verdict: str = None,
    phase: str = None,
    term: str = None,
    db: Session = Depends(get_db),
):
    """CSV export of gap candidates with current filter applied."""
    where = ["1=1"]
    params: dict = {}
    if verdict:
        where.append("g.keeper_verdict = :verdict")
        params["verdict"] = verdict
    if phase:
        where.append("g.phase = :phase")
        params["phase"] = int(phase)
    if term:
        where.append("g.gateway_term LIKE :term")
        params["term"] = f"%{term}%"

    rows = db.execute(text(f"""
        SELECT g.id, g.declaration_text, g.section_source, g.phase,
               g.confidence, g.gateway_term, g.keeper_verdict,
               g.gap_class, g.source_discipline, g.bridge_potential,
               g.cap_score,
               p.pmcid, p.title, p.pub_year
        FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {' AND '.join(where)}
        ORDER BY g.confidence DESC, g.created_at DESC
    """), params).mappings().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "pmcid", "title", "pub_year", "declaration_text",
        "gateway_term", "gap_class", "phase", "confidence",
        "keeper_verdict", "source_discipline", "bridge_potential", "cap_score",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["pmcid"], r["title"], r["pub_year"],
            r["declaration_text"], r["gateway_term"], r["gap_class"],
            r["phase"], r["confidence"], r["keeper_verdict"],
            r["source_discipline"], r["bridge_potential"], r["cap_score"],
        ])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=gaps_export.csv"},
    )


@router.post("/gaps/bulk-review")
def bulk_keeper_review(
    gap_ids: List[int] = Form(...),
    verdict: str = Form(...),
    redirect_url: str = Form(default="/view/gaps"),
    db: Session = Depends(get_db),
):
    """Bulk keeper verdict — pass or fail a list of gap IDs in one action."""
    from fastapi import HTTPException
    if verdict not in ("pass", "fail"):
        raise HTTPException(status_code=422, detail="verdict must be pass or fail")
    if not gap_ids:
        raise HTTPException(status_code=422, detail="No gap IDs provided")

    for gid in gap_ids:
        db.execute(text(
            "UPDATE gap_endpoints SET keeper_reviewed=1, keeper_verdict=:v WHERE id=:id"
        ), {"v": verdict, "id": gid})
    db.commit()

    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/gaps/{gap_id}", response_class=HTMLResponse)
def gap_detail(gap_id: int, request: Request, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    row = db.execute(text("""
        SELECT g.id, g.paper_id, g.declaration_text, g.section_source,
               g.phase, g.confidence, g.gateway_term, g.gap_class,
               g.keeper_reviewed, g.keeper_verdict, g.created_at,
               g.caught_paper_cosmoid, g.caught_at, g.catch_confidence,
               g.source_discipline, g.target_disciplines,
               g.bridge_potential, g.bridge_rationale,
               p.pmcid, p.doi, p.title, p.journal, p.pub_year,
               p.abstract_text, p.conclusions_text,
               p.ai_flag, p.ai_declared, p.ai_detection_score, p.ai_detection_signals
        FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE g.id = :id
    """), {"id": gap_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Gap not found")

    gap = dict(row)
    # Parse stored JSON list
    try:
        gap["target_disciplines"] = json.loads(gap.get("target_disciplines") or "[]")
    except Exception:
        gap["target_disciplines"] = []

    return templates.TemplateResponse(request, "gap_detail.html", {
        "gap": gap,
    })


_DISCIPLINES = [
    "computer_science", "neuroscience", "psychology", "criminal_justice",
    "genomics_bioinformatics", "geospatial", "epidemiology", "physics",
    "mathematics", "general",
]


@router.get("/holes", response_class=HTMLResponse)
def structural_holes_view(
    request: Request,
    source: str = None,
    target: str = None,
    min_bridge: float = 0.5,
    db: Session = Depends(get_db),
):
    where = ["g.bridge_potential >= :min_bridge", "g.source_discipline IS NOT NULL"]
    params: dict = {"min_bridge": min_bridge}
    if source:
        where.append("g.source_discipline = :source")
        params["source"] = source

    rows = db.execute(text(f"""
        SELECT g.id, g.declaration_text, g.gateway_term, g.gap_class,
               g.keeper_verdict, g.source_discipline, g.target_disciplines,
               g.bridge_potential, g.bridge_rationale,
               p.pmcid, p.title, p.pub_year
        FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {' AND '.join(where)}
        ORDER BY g.bridge_potential DESC, g.confidence DESC
        LIMIT 100
    """), params).mappings().all()

    holes = []
    for row in rows:
        h = dict(row)
        try:
            h["target_disciplines"] = json.loads(h.get("target_disciplines") or "[]")
        except Exception:
            h["target_disciplines"] = []
        if target and target not in h["target_disciplines"]:
            continue
        holes.append(h)

    return templates.TemplateResponse(request, "structural_holes.html", {
        "holes": holes,
        "disciplines": _DISCIPLINES,
        "source": source,
        "target": target,
        "min_bridge": min_bridge,
    })


@router.post("/gaps/{gap_id}/review")
def keeper_review_web(
    gap_id: int,
    verdict: str = Form(...),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    if verdict not in ("pass", "fail"):
        raise HTTPException(status_code=422, detail="verdict must be pass or fail")

    result = db.execute(text(
        "UPDATE gap_endpoints SET keeper_reviewed=1, keeper_verdict=:v WHERE id=:id"
    ), {"v": verdict, "id": gap_id})
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Gap not found")

    return RedirectResponse(url=f"/view/gaps/{gap_id}", status_code=303)


_REJECTION_MODES = [
    "methodology", "scope", "insufficient_evidence",
    "theory_gap", "duplicate", "other",
]


@router.get("/rejected", response_class=HTMLResponse)
def rejected_trail(
    request: Request,
    mode: str = None,
    term: str = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    where = ["g.rejected_at IS NOT NULL"]
    params: dict = {"limit": limit, "offset": offset}

    if mode and mode in _REJECTION_MODES:
        where.append("g.rejection_mode = :mode")
        params["mode"] = mode
    if term:
        where.append("g.gateway_term LIKE :term")
        params["term"] = f"%{term}%"

    clause = " AND ".join(where)

    rows = db.execute(text(f"""
        SELECT g.id, g.declaration_text, g.gateway_term, g.gap_class,
               g.confidence, g.rejection_mode, g.rejection_notes,
               g.pickup_instructions, g.rejected_at, g.caught_paper_cosmoid,
               p.title, p.pmcid, p.pub_year
        FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {clause}
        ORDER BY g.rejected_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM gap_endpoints g JOIN papers p ON p.id = g.paper_id
        WHERE {clause}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar() or 0

    rejected_stats = {
        "total_rejected": db.execute(text(
            "SELECT COUNT(*) FROM gap_endpoints WHERE rejected_at IS NOT NULL"
        )).scalar() or 0,
        "with_pickup": db.execute(text(
            "SELECT COUNT(*) FROM gap_endpoints WHERE rejected_at IS NOT NULL "
            "AND pickup_instructions IS NOT NULL"
        )).scalar() or 0,
    }
    mode_counts = db.execute(text("""
        SELECT rejection_mode, COUNT(*) AS cnt
        FROM gap_endpoints
        WHERE rejected_at IS NOT NULL AND rejection_mode IS NOT NULL
        GROUP BY rejection_mode
        ORDER BY cnt DESC
        LIMIT 3
    """)).mappings().all()

    return templates.TemplateResponse(request, "rejected_trail.html", {
        "gaps":           [dict(r) for r in rows],
        "stats":          _get_stats(db),
        "rejected_stats": rejected_stats,
        "mode_counts":    [dict(r) for r in mode_counts],
        "total":          total,
        "limit":          limit,
        "offset":         offset,
        "mode_filter":    mode,
        "term":           term,
    })


@router.get("/globe", response_class=HTMLResponse)
def globe_view(request: Request, db: Session = Depends(get_db)):
    stats = _get_stats(db)
    return templates.TemplateResponse(request, "globe.html", {"stats": stats})


@router.get("/runs", response_class=HTMLResponse)
def runs_index(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT id, query_term, pmcids_fetched, pmcids_parsed,
               gaps_found, started_at, completed_at, status
        FROM ingest_runs ORDER BY id DESC LIMIT 50
    """)).mappings().all()

    return templates.TemplateResponse(request, "runs_index.html", {
        "runs": [dict(r) for r in rows],
    })
