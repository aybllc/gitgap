"""
gitgap — Ingest API router
Trigger pipeline runs and inspect run history.
"""

from typing import Optional

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db, SessionLocal
from ..ingest.pipeline import run as run_pipeline, run_from_text

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _run_with_own_session(query: str, max_results: int, phase: int):
    """
    Background task wrapper.
    Creates its own DB session — avoids closed-session issue
    with FastAPI dependency injection cleanup.
    """
    db = SessionLocal()
    try:
        run_pipeline(query=query, max_results=max_results, phase=phase, db=db)
    finally:
        db.close()


@router.post("/ingest/run")
def trigger_run(
    query: str,
    max_results: int = 50,
    phase: int = 1,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """
    Trigger a pipeline run for a search query.
    Runs in background — returns immediately with run status.
    Poll /ingest/runs for results.
    """
    if background_tasks:
        background_tasks.add_task(
            _run_with_own_session,
            query=query,
            max_results=max_results,
            phase=phase,
        )
        return {
            "status": "queued",
            "query": query,
            "max_results": max_results,
            "phase": phase,
            "message": "Pipeline running. Poll /ingest/runs for status.",
        }

    # Synchronous fallback
    candidates = run_pipeline(query=query, max_results=max_results,
                              phase=phase, db=db)
    return {
        "status": "complete",
        "query": query,
        "candidates_found": len(candidates),
    }


class FromTextRequest(BaseModel):
    title: str
    abstract_text: str
    full_text: Optional[str] = None          # conclusions / discussion text to scan
    doi: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    source: Optional[str] = "external"       # "zenodo", "arxiv", "personal", etc.
    catching_cosmoid: Optional[str] = None   # eaiou CosmoID — marks gaps CAUGHT immediately


@router.post("/from-text")
def ingest_from_text(
    payload: FromTextRequest,
    db: Session = Depends(get_db),
):
    """
    F3-C: Ingest a paper from raw text — no PMC ID required.

    Accepts Zenodo papers, arXiv preprints, personal uploads, or any paper
    not in PubMed Central. Runs the same gateway filter and gap extraction
    as the PMC pipeline.

    If `catching_cosmoid` is provided (an eaiou CosmoID), every extracted gap
    is immediately marked CAUGHT with that cosmoid — wiring the submission
    directly into the NAUGHT→CAUGHT lifecycle.

    Returns the extracted gap IDs so the caller can track them.
    """
    if not payload.abstract_text.strip():
        raise HTTPException(status_code=422, detail="abstract_text is required and must not be blank")

    result = run_from_text(
        title=payload.title,
        abstract_text=payload.abstract_text,
        full_text=payload.full_text or "",
        doi=payload.doi,
        year=payload.year,
        journal=payload.journal,
        source=payload.source or "external",
        catching_cosmoid=payload.catching_cosmoid,
        db=db,
    )

    return {
        "status": "complete",
        **result,
    }


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    """List all ingest runs, most recent first."""
    rows = db.execute(text("""
        SELECT id, query_term, pmcids_fetched, pmcids_parsed,
               gaps_found, started_at, completed_at, status
        FROM ingest_runs
        ORDER BY id DESC LIMIT :limit
    """), {"limit": limit}).mappings().all()
    return {"runs": [dict(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    """Single run detail."""
    from fastapi import HTTPException
    row = db.execute(text(
        "SELECT * FROM ingest_runs WHERE id = :id"
    ), {"id": run_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return dict(row)
