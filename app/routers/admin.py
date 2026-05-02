"""
gitgap — Admin router
Manage API sources, field mappings, journal registry, and reconcile runs.
"""

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, BackgroundTasks, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db, SessionLocal
from ..ingest.pmc import search_pmc, fetch_bioc
from ..ingest.parser import parse_bioc
from ..ingest.pipeline import run as run_pipeline
from ..services.ai_detection import run_on_paper as _ai_interrogate

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

# Internal fields available as mapping targets
_PAPER_FIELDS = [
    "pmcid", "doi", "title", "journal", "pub_year",
    "abstract_text", "methods_text", "conclusions_text",
]
_TRANSFORM_OPTIONS = ["none", "int", "strip", "concat", "first_sentence", "year_extract"]
_AUTH_TYPES = ["none", "email_key", "api_key_header", "api_key_param", "bearer", "basic"]
_RESPONSE_FORMATS = ["json", "xml"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_status(env_var: str | None) -> bool:
    """Return True if the named env var is set and non-empty."""
    if not env_var:
        return False
    return bool(os.getenv(env_var, "").strip())


def _sources_with_counts(db: Session):
    return db.execute(text("""
        SELECT s.id, s.name, s.slug, s.base_url, s.auth_type, s.api_key_env,
               s.email_env, s.response_format, s.rate_limit_per_sec,
               s.status, s.notes, s.updated_at,
               COUNT(DISTINCT m.id) AS mapping_count,
               COUNT(DISTINCT j.id) AS journal_count
        FROM api_sources s
        LEFT JOIN api_field_mappings m ON m.source_id = s.id
        LEFT JOIN journal_registry   j ON j.source_id = s.id
        GROUP BY s.id
        ORDER BY s.id
    """)).mappings().all()


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    sources = _sources_with_counts(db)
    recent_reconciles = db.execute(text("""
        SELECT r.id, r.triggered_at, r.completed_at, r.articles_added,
               r.articles_tombstoned, r.status, j.journal_name
        FROM reconcile_log r
        JOIN journal_registry j ON j.id = r.journal_id
        ORDER BY r.triggered_at DESC LIMIT 10
    """)).mappings().all()
    recent_discoveries = db.execute(text("""
        SELECT journal_name, queried_at, result_status, article_count, bioc_available, notes
        FROM discovery_log ORDER BY queried_at DESC LIMIT 10
    """)).mappings().all()
    stats = {
        "source_count":       db.execute(text("SELECT COUNT(*) FROM api_sources")).scalar() or 0,
        "journal_count":      db.execute(text("SELECT COUNT(*) FROM journal_registry")).scalar() or 0,
        "mapping_count":      db.execute(text("SELECT COUNT(*) FROM api_field_mappings")).scalar() or 0,
        "reconcile_runs":     db.execute(text("SELECT COUNT(*) FROM reconcile_log")).scalar() or 0,
        "pending_ai":         db.execute(text(
            "SELECT COUNT(*) FROM papers WHERE ai_interrogated_at IS NULL"
        )).scalar() or 0,
        "journal_requests":   db.execute(text(
            "SELECT COUNT(*) FROM journal_requests WHERE status = 'pending'"
        )).scalar() or 0,
    }
    # For discovery probe datalist: pending requests + registered journals
    probe_suggestions = db.execute(text("""
        SELECT journal_name FROM journal_requests WHERE status = 'pending'
        UNION
        SELECT journal_name FROM journal_registry WHERE status = 'active'
        ORDER BY journal_name
    """)).scalars().all()

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "active":             "admin",
        "sources":            [dict(s) for s in sources],
        "recent_reconciles":  [dict(r) for r in recent_reconciles],
        "recent_discoveries": [dict(d) for d in recent_discoveries],
        "stats":              stats,
        "probe_suggestions":  probe_suggestions,
    })


# ── API Sources ────────────────────────────────────────────────────────────────

@router.get("/sources", response_class=HTMLResponse)
def sources_index(request: Request, db: Session = Depends(get_db)):
    sources = _sources_with_counts(db)
    enriched = []
    for s in sources:
        sd = dict(s)
        sd["api_key_ok"]   = _env_status(sd.get("api_key_env"))
        sd["email_ok"]     = _env_status(sd.get("email_env"))
        enriched.append(sd)
    return templates.TemplateResponse(request, "admin/sources.html", {
        "active":   "admin",
        "sources":  enriched,
        "auth_types":    _AUTH_TYPES,
        "format_options": _RESPONSE_FORMATS,
    })


@router.post("/sources")
def create_source(
    name:             str  = Form(...),
    slug:             str  = Form(...),
    base_url:         str  = Form(...),
    auth_type:        str  = Form("none"),
    api_key_env:      str  = Form(""),
    email_env:        str  = Form(""),
    response_format:  str  = Form("json"),
    rate_limit:       float = Form(3.0),
    notes:            str  = Form(""),
    db: Session = Depends(get_db),
):
    try:
        db.execute(text("""
            INSERT INTO api_sources
            (name, slug, base_url, auth_type, api_key_env, email_env,
             response_format, rate_limit_per_sec, status, notes, created_at, updated_at)
            VALUES (:name, :slug, :url, :auth, :key_env, :email_env,
                    :fmt, :rate, 'active', :notes, :now, :now)
        """), {
            "name": name.strip(), "slug": slug.strip().lower().replace(" ", "_"),
            "url": base_url.strip(), "auth": auth_type,
            "key_env": api_key_env.strip() or None, "email_env": email_env.strip() or None,
            "fmt": response_format, "rate": rate_limit,
            "notes": notes.strip() or None, "now": _now(),
        })
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not create source: {e}")
    return RedirectResponse(url="/admin/sources", status_code=303)


@router.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(
    source_id: int,
    request: Request,
    flash: str = "",
    db: Session = Depends(get_db),
):
    source = db.execute(text(
        "SELECT * FROM api_sources WHERE id = :id"
    ), {"id": source_id}).mappings().first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    mappings = db.execute(text(
        "SELECT * FROM api_field_mappings WHERE source_id = :id ORDER BY id"
    ), {"id": source_id}).mappings().all()

    journals = db.execute(text("""
        SELECT j.*, COUNT(r.id) AS reconcile_count
        FROM journal_registry j
        LEFT JOIN reconcile_log r ON r.journal_id = j.id
        WHERE j.source_id = :id
        GROUP BY j.id
        ORDER BY j.journal_name
    """), {"id": source_id}).mappings().all()

    source_d = dict(source)
    source_d["api_key_ok"] = _env_status(source_d.get("api_key_env"))
    source_d["email_ok"]   = _env_status(source_d.get("email_env"))

    return templates.TemplateResponse(request, "admin/source_detail.html", {
        "active":           "admin",
        "source":           source_d,
        "mappings":         [dict(m) for m in mappings],
        "journals":         [dict(j) for j in journals],
        "paper_fields":     _PAPER_FIELDS,
        "transform_options": _TRANSFORM_OPTIONS,
        "auth_types":       _AUTH_TYPES,
        "format_options":   _RESPONSE_FORMATS,
        "flash":            flash,
    })


@router.post("/sources/{source_id}/edit")
def edit_source(
    source_id: int,
    name:            str   = Form(...),
    base_url:        str   = Form(...),
    auth_type:       str   = Form("none"),
    api_key_env:     str   = Form(""),
    email_env:       str   = Form(""),
    response_format: str   = Form("json"),
    rate_limit:      float = Form(3.0),
    status:          str   = Form("active"),
    notes:           str   = Form(""),
    db: Session = Depends(get_db),
):
    db.execute(text("""
        UPDATE api_sources SET
            name = :name, base_url = :url, auth_type = :auth,
            api_key_env = :key_env, email_env = :email_env,
            response_format = :fmt, rate_limit_per_sec = :rate,
            status = :status, notes = :notes, updated_at = :now
        WHERE id = :id
    """), {
        "name": name.strip(), "url": base_url.strip(), "auth": auth_type,
        "key_env": api_key_env.strip() or None, "email_env": email_env.strip() or None,
        "fmt": response_format, "rate": rate_limit, "status": status,
        "notes": notes.strip() or None, "now": _now(), "id": source_id,
    })
    db.commit()
    return RedirectResponse(url=f"/admin/sources/{source_id}?flash=Source+updated", status_code=303)


@router.post("/sources/{source_id}/delete")
def delete_source(source_id: int, db: Session = Depends(get_db)):
    # Cascade: delete mappings and journals (and their reconcile logs) first
    journal_ids = [r[0] for r in db.execute(text(
        "SELECT id FROM journal_registry WHERE source_id = :id"
    ), {"id": source_id}).fetchall()]
    for jid in journal_ids:
        db.execute(text("DELETE FROM reconcile_log WHERE journal_id = :id"), {"id": jid})
    db.execute(text("DELETE FROM journal_registry WHERE source_id = :id"), {"id": source_id})
    db.execute(text("DELETE FROM api_field_mappings WHERE source_id = :id"), {"id": source_id})
    db.execute(text("DELETE FROM api_sources WHERE id = :id"), {"id": source_id})
    db.commit()
    return RedirectResponse(url="/admin/sources", status_code=303)


# ── Field Mappings ─────────────────────────────────────────────────────────────

@router.post("/sources/{source_id}/mappings")
def add_mapping(
    source_id:     int,
    source_field:  str  = Form(...),
    target_field:  str  = Form(...),
    transform:     str  = Form("none"),
    required:      str  = Form("0"),
    default_value: str  = Form(""),
    notes:         str  = Form(""),
    db: Session = Depends(get_db),
):
    if not db.execute(text("SELECT id FROM api_sources WHERE id = :id"), {"id": source_id}).scalar():
        raise HTTPException(status_code=404, detail="Source not found")
    db.execute(text("""
        INSERT INTO api_field_mappings
        (source_id, source_field, target_table, target_field, transform, required, default_value, notes)
        VALUES (:sid, :sf, 'papers', :tf, :tr, :req, :dv, :notes)
    """), {
        "sid": source_id, "sf": source_field.strip(), "tf": target_field,
        "tr": transform if transform != "none" else None,
        "req": 1 if required == "1" else 0,
        "dv": default_value.strip() or None,
        "notes": notes.strip() or None,
    })
    db.commit()
    return RedirectResponse(url=f"/admin/sources/{source_id}?flash=Mapping+added", status_code=303)


@router.post("/sources/{source_id}/mappings/{mapping_id}/delete")
def delete_mapping(source_id: int, mapping_id: int, db: Session = Depends(get_db)):
    db.execute(text(
        "DELETE FROM api_field_mappings WHERE id = :id AND source_id = :sid"
    ), {"id": mapping_id, "sid": source_id})
    db.commit()
    return RedirectResponse(url=f"/admin/sources/{source_id}?flash=Mapping+removed", status_code=303)


# ── Journal Registry ───────────────────────────────────────────────────────────

@router.get("/journals", response_class=HTMLResponse)
def journals_index(
    request: Request,
    discover_name: str = "",
    discover_status: str = "",
    discover_count: int = 0,
    db: Session = Depends(get_db),
):
    journals = db.execute(text("""
        SELECT j.id, j.journal_name, j.issn, j.nlm_id, j.search_query,
               j.article_count, j.last_reconciled, j.status, j.notes,
               s.name AS source_name, s.id AS source_id,
               COUNT(r.id) AS reconcile_count,
               SUM(CASE WHEN r.status = 'running' THEN 1 ELSE 0 END) AS running_count
        FROM journal_registry j
        JOIN api_sources s ON s.id = j.source_id
        LEFT JOIN reconcile_log r ON r.journal_id = j.id
        GROUP BY j.id
        ORDER BY j.journal_name
    """)).mappings().all()

    sources = db.execute(text(
        "SELECT id, name FROM api_sources WHERE status = 'active' ORDER BY name"
    )).mappings().all()

    recent_discoveries = db.execute(text("""
        SELECT journal_name, queried_at, result_status, article_count, bioc_available, notes
        FROM discovery_log ORDER BY queried_at DESC LIMIT 5
    """)).mappings().all()

    return templates.TemplateResponse(request, "admin/journals.html", {
        "active":             "admin",
        "journals":           [dict(j) for j in journals],
        "sources":            [dict(s) for s in sources],
        "recent_discoveries": [dict(d) for d in recent_discoveries],
        "discover_name":      discover_name,
        "discover_status":    discover_status,
        "discover_count":     discover_count,
    })


@router.post("/journals")
def add_journal(
    source_id:    int  = Form(...),
    journal_name: str  = Form(...),
    issn:         str  = Form(""),
    nlm_id:       str  = Form(""),
    search_query: str  = Form(""),
    oai_endpoint: str  = Form(""),
    notes:        str  = Form(""),
    db: Session = Depends(get_db),
):
    # Auto-build search_query if not supplied: journal name as PMC query term
    query = search_query.strip() or f'"{journal_name.strip()}"[journal]'
    db.execute(text("""
        INSERT INTO journal_registry
        (source_id, journal_name, issn, nlm_id, search_query, oai_endpoint, status, notes, created_at)
        VALUES (:sid, :name, :issn, :nlm_id, :query, :oai, 'active', :notes, :now)
    """), {
        "sid": source_id, "name": journal_name.strip(),
        "issn": issn.strip() or None, "nlm_id": nlm_id.strip() or None,
        "query": query, "oai": oai_endpoint.strip() or None,
        "notes": notes.strip() or None, "now": _now(),
    })
    db.commit()
    return RedirectResponse(url="/admin/journals", status_code=303)


@router.post("/journals/discover")
def discover_journal(
    journal_name: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """
    Probe PMC for a journal name: check article count and BioC availability.
    Logs the attempt to discovery_log. Redirects back with result flash.
    """
    def _probe(name: str):
        """Background probe — runs after response is sent."""
        probe_db = SessionLocal()
        try:
            query = f'"{name}"[journal]'
            pmcids = search_pmc(query, max_results=10)
            bioc_ok = False
            if pmcids:
                doc = fetch_bioc(pmcids[0])
                bioc_ok = doc is not None
            status = "found" if len(pmcids) >= 5 else ("partial" if pmcids else "not_found")
            notes = None if pmcids else "No articles found in PMC for this journal name."
            probe_db.execute(text("""
                INSERT INTO discovery_log
                (journal_name, queried_at, result_status, article_count,
                 bioc_available, sample_pmcids, notes)
                VALUES (:name, :now, :status, :count, :bioc, :pmcids, :notes)
            """), {
                "name":   name, "now":   _now(), "status": status,
                "count":  len(pmcids),   "bioc":  1 if bioc_ok else 0,
                "pmcids": json.dumps(pmcids[:5]), "notes": notes,
            })
            probe_db.commit()
        except Exception as e:
            try:
                probe_db.execute(text("""
                    INSERT INTO discovery_log
                    (journal_name, queried_at, result_status, article_count,
                     bioc_available, sample_pmcids, notes)
                    VALUES (:name, :now, 'error', 0, 0, '[]', :notes)
                """), {"name": name, "now": _now(), "notes": str(e)})
                probe_db.commit()
            except Exception:
                pass
        finally:
            probe_db.close()

    if background_tasks:
        background_tasks.add_task(_probe, journal_name.strip())
        return RedirectResponse(
            url=f"/admin/journals?discover_name={journal_name.strip()}&discover_status=probing",
            status_code=303,
        )
    # Synchronous fallback
    _probe(journal_name.strip())
    return RedirectResponse(url="/admin/journals", status_code=303)


@router.get("/journals/{journal_id}", response_class=HTMLResponse)
def journal_detail(
    journal_id: int,
    request: Request,
    flash: str = "",
    db: Session = Depends(get_db),
):
    journal = db.execute(text("""
        SELECT j.*, s.name AS source_name
        FROM journal_registry j JOIN api_sources s ON s.id = j.source_id
        WHERE j.id = :id
    """), {"id": journal_id}).mappings().first()
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")

    log = db.execute(text("""
        SELECT * FROM reconcile_log WHERE journal_id = :id
        ORDER BY triggered_at DESC LIMIT 20
    """), {"id": journal_id}).mappings().all()

    sources = db.execute(text(
        "SELECT id, name FROM api_sources WHERE status = 'active' ORDER BY name"
    )).mappings().all()

    return templates.TemplateResponse(request, "admin/journal_detail.html", {
        "active":   "admin",
        "journal":  dict(journal),
        "log":      [dict(r) for r in log],
        "sources":  [dict(s) for s in sources],
        "flash":    flash,
    })


@router.post("/journals/{journal_id}/edit")
def edit_journal(
    journal_id:   int,
    journal_name: str  = Form(...),
    issn:         str  = Form(""),
    nlm_id:       str  = Form(""),
    search_query: str  = Form(...),
    oai_endpoint: str  = Form(""),
    status:       str  = Form("active"),
    notes:        str  = Form(""),
    db: Session = Depends(get_db),
):
    db.execute(text("""
        UPDATE journal_registry SET
            journal_name = :name, issn = :issn, nlm_id = :nlm_id,
            search_query = :query, oai_endpoint = :oai,
            status = :status, notes = :notes
        WHERE id = :id
    """), {
        "name":   journal_name.strip(), "issn":  issn.strip() or None,
        "nlm_id": nlm_id.strip() or None, "query": search_query.strip(),
        "oai":    oai_endpoint.strip() or None,
        "status": status, "notes": notes.strip() or None, "id": journal_id,
    })
    db.commit()
    return RedirectResponse(url=f"/admin/journals/{journal_id}?flash=Journal+updated", status_code=303)


@router.post("/journals/{journal_id}/delete")
def delete_journal(journal_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM reconcile_log WHERE journal_id = :id"), {"id": journal_id})
    db.execute(text("DELETE FROM journal_registry WHERE id = :id"), {"id": journal_id})
    db.commit()
    return RedirectResponse(url="/admin/journals", status_code=303)


# ── Reconcile ─────────────────────────────────────────────────────────────────

def _run_reconcile(journal_id: int):
    """
    Background reconcile job.
    - Runs the full pipeline with the journal's search_query.
    - Pipeline handles deduplication (skips already-ingested PMCIDs).
    - Tombstones DB papers for this journal that PMC no longer returns.
    """
    db = SessionLocal()
    log_id = None
    try:
        journal = db.execute(text(
            "SELECT * FROM journal_registry WHERE id = :id"
        ), {"id": journal_id}).mappings().first()
        if not journal:
            return

        # Open a reconcile log entry
        res = db.execute(text("""
            INSERT INTO reconcile_log (journal_id, triggered_at, status)
            VALUES (:jid, :now, 'running')
        """), {"jid": journal_id, "now": _now()})
        db.commit()
        log_id = res.lastrowid

        query = journal["search_query"]

        # 1. Get current PMC IDs for this journal
        pmc_ids = search_pmc(query, max_results=500)
        articles_checked = len(pmc_ids)

        # 2. Run full pipeline — pipeline skips already-ingested PMCIDs automatically
        candidates_before = db.execute(text("SELECT COUNT(*) FROM papers")).scalar() or 0
        run_pipeline(query=query, max_results=500, phase=1, db=db)
        candidates_after = db.execute(text("SELECT COUNT(*) FROM papers")).scalar() or 0
        articles_added = max(0, candidates_after - candidates_before)

        # 3. Tombstone detection — papers from this journal not in current PMC results
        articles_tombstoned = 0
        if pmc_ids and journal["journal_name"]:
            pmc_id_set = set(pmc_ids)
            db_rows = db.execute(text(
                "SELECT pmcid FROM papers "
                "WHERE journal LIKE :j AND pmcid NOT LIKE 'EXT:%' "
                "AND (tombstone_state IS NULL OR tombstone_state = '')"
            ), {"j": f"%{journal['journal_name']}%"}).fetchall()
            for row in db_rows:
                if row[0] not in pmc_id_set:
                    db.execute(text(
                        "UPDATE papers SET tombstone_state = 'retracted', tombstoned_at = :now "
                        "WHERE pmcid = :pmcid"
                    ), {"now": _now(), "pmcid": row[0]})
                    articles_tombstoned += 1
            db.commit()

        # 4. Update journal stats and log
        db.execute(text("""
            UPDATE journal_registry
            SET last_reconciled = :now,
                article_count = article_count + :added
            WHERE id = :id
        """), {"now": _now(), "added": articles_added, "id": journal_id})
        db.execute(text("""
            UPDATE reconcile_log SET
                completed_at = :now, articles_checked = :checked,
                articles_added = :added, articles_tombstoned = :tombstoned,
                status = 'complete'
            WHERE id = :lid
        """), {
            "now": _now(), "checked": articles_checked,
            "added": articles_added, "tombstoned": articles_tombstoned,
            "lid": log_id,
        })
        db.commit()

    except Exception as e:
        if log_id:
            try:
                db.execute(text("""
                    UPDATE reconcile_log SET status = 'error',
                        error_message = :err, completed_at = :now
                    WHERE id = :id
                """), {"err": str(e)[:500], "now": _now(), "id": log_id})
                db.commit()
            except Exception:
                pass
    finally:
        db.close()


@router.post("/journals/{journal_id}/reconcile")
def trigger_reconcile(
    journal_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Trigger a reconcile job for a single journal (runs in background)."""
    if not db.execute(text(
        "SELECT id FROM journal_registry WHERE id = :id"
    ), {"id": journal_id}).scalar():
        raise HTTPException(status_code=404, detail="Journal not found")
    background_tasks.add_task(_run_reconcile, journal_id)
    return RedirectResponse(
        url=f"/admin/journals/{journal_id}?flash=Reconcile+job+queued",
        status_code=303,
    )


@router.post("/reconcile-all")
def reconcile_all(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Trigger reconcile for all active journals."""
    ids = [r[0] for r in db.execute(text(
        "SELECT id FROM journal_registry WHERE status = 'active'"
    )).fetchall()]
    for jid in ids:
        background_tasks.add_task(_run_reconcile, jid)
    return RedirectResponse(
        url=f"/admin/journals?discover_status=reconciling+{len(ids)}+journals",
        status_code=303,
    )


# ── Env Check ─────────────────────────────────────────────────────────────────

@router.get("/env")
def env_check(db: Session = Depends(get_db)):
    """
    Return presence (not value) of all env vars referenced by api_sources.
    Safe to expose — only shows True/False, never the secret.
    """
    rows = db.execute(text(
        "SELECT DISTINCT api_key_env, email_env FROM api_sources"
    )).fetchall()
    result = {}
    seen = set()
    for (key_env, email_env) in rows:
        for v in [key_env, email_env]:
            if v and v not in seen:
                seen.add(v)
                result[v] = _env_status(v)
    return JSONResponse(result)


# ── Retroactive AI Interrogation Batch ────────────────────────────────────────

def _run_ai_batch():
    """
    Background job: interrogate all papers that haven't been AI-checked yet.
    Papers ingested before ai_detection was implemented have ai_interrogated_at=NULL.
    Non-fatal per-paper — a failure on one paper does not stop the batch.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, abstract_text, conclusions_text FROM papers "
            "WHERE ai_interrogated_at IS NULL ORDER BY id"
        )).fetchall()
        for (paper_id, abstract, conclusions) in rows:
            try:
                _ai_interrogate(
                    paper_id,
                    abstract or "",
                    conclusions or "",
                    db,
                )
            except Exception:
                pass  # non-fatal per paper
    finally:
        db.close()


@router.post("/ai-batch")
def trigger_ai_batch(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Queue retroactive AI interrogation for all papers with ai_interrogated_at=NULL.
    Runs in background — returns immediately.
    """
    pending = db.execute(text(
        "SELECT COUNT(*) FROM papers WHERE ai_interrogated_at IS NULL"
    )).scalar() or 0
    background_tasks.add_task(_run_ai_batch)
    return RedirectResponse(
        url=f"/admin/?flash=AI+batch+queued+%E2%80%94+{pending}+papers",
        status_code=303,
    )


# ── Journal Requests ──────────────────────────────────────────────────────────

@router.get("/journal-requests", response_class=HTMLResponse)
def journal_requests_index(
    request: Request,
    status: str = "pending",
    db: Session = Depends(get_db),
):
    """Review public journal submission requests."""
    where = "1=1"
    params: dict = {}
    if status and status != "all":
        where = "status = :status"
        params["status"] = status

    rows = db.execute(text(f"""
        SELECT id, journal_name, url, oai_endpoint, issn,
               contact_email, notes, created_at, status
        FROM journal_requests
        WHERE {where}
        ORDER BY created_at DESC
    """), params).mappings().all()

    counts = {
        "pending":   db.execute(text("SELECT COUNT(*) FROM journal_requests WHERE status='pending'")).scalar() or 0,
        "approved":  db.execute(text("SELECT COUNT(*) FROM journal_requests WHERE status='approved'")).scalar() or 0,
        "dismissed": db.execute(text("SELECT COUNT(*) FROM journal_requests WHERE status='dismissed'")).scalar() or 0,
    }

    # Sources available for promotion target
    sources = db.execute(text(
        "SELECT id, name FROM api_sources WHERE status='active' ORDER BY name"
    )).mappings().all()

    return templates.TemplateResponse(request, "admin/journal_requests.html", {
        "active":        "admin",
        "requests":      [dict(r) for r in rows],
        "counts":        counts,
        "status_filter": status,
        "sources":       [dict(s) for s in sources],
    })


@router.post("/journal-requests/{req_id}/approve")
def approve_journal_request(
    req_id: int,
    source_id: int = Form(...),
    search_query: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Promote a journal request to the registry.
    Creates a journal_registry entry (status='active') and marks the request approved.
    Admin can edit the registry entry afterwards to refine search_query, OAI endpoint, etc.
    """
    req = db.execute(text(
        "SELECT * FROM journal_requests WHERE id = :id"
    ), {"id": req_id}).mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    # Derive search_query: use explicit input, fall back to journal name
    query = search_query.strip() or req["journal_name"]

    db.execute(text("""
        INSERT INTO journal_registry
        (source_id, journal_name, issn, search_query, oai_endpoint,
         article_count, status, notes, created_at)
        VALUES
        (:sid, :name, :issn, :query, :oai, 0, 'active', :notes, :now)
    """), {
        "sid":   source_id,
        "name":  req["journal_name"],
        "issn":  req["issn"],
        "query": query,
        "oai":   req["oai_endpoint"],
        "notes": f"Promoted from public request #{req_id}. Contact: {req['contact_email'] or 'n/a'}",
        "now":   _now(),
    })

    db.execute(text(
        "UPDATE journal_requests SET status = 'approved' WHERE id = :id"
    ), {"id": req_id})
    db.commit()

    return RedirectResponse(
        url="/admin/journal-requests?status=pending",
        status_code=303,
    )


@router.post("/journal-requests/{req_id}/dismiss")
def dismiss_journal_request(req_id: int, db: Session = Depends(get_db)):
    """Mark a journal request as dismissed (not suitable for registry)."""
    db.execute(text(
        "UPDATE journal_requests SET status = 'dismissed' WHERE id = :id"
    ), {"id": req_id})
    db.commit()
    return RedirectResponse(
        url="/admin/journal-requests?status=pending",
        status_code=303,
    )
