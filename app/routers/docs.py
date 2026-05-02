"""
gitgap — Documentation router
Public-facing docs: quickstart, origin, workflow, journals, API reference,
roadmap, funding, open source, glossary.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db

router = APIRouter(prefix="/docs", tags=["docs"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def docs_root():
    return RedirectResponse(url="/docs/quickstart", status_code=302)


@router.get("/quickstart", response_class=HTMLResponse)
def docs_quickstart(request: Request):
    return templates.TemplateResponse(request, "docs/quickstart.html", {
        "active": "docs", "doc_page": "quickstart",
    })


@router.get("/origin", response_class=HTMLResponse)
def docs_origin(request: Request):
    return templates.TemplateResponse(request, "docs/origin.html", {
        "active": "docs", "doc_page": "origin",
    })


@router.get("/workflow", response_class=HTMLResponse)
def docs_workflow(request: Request):
    return templates.TemplateResponse(request, "docs/workflow.html", {
        "active": "docs", "doc_page": "workflow",
    })


@router.get("/journals", response_class=HTMLResponse)
def docs_journals(request: Request, db: Session = Depends(get_db)):
    journals = db.execute(text("""
        SELECT j.id, j.journal_name, j.issn, j.article_count,
               j.last_reconciled, j.status, j.oai_endpoint,
               s.name AS source_name
        FROM journal_registry j
        JOIN api_sources s ON s.id = j.source_id
        WHERE j.status = 'active'
        ORDER BY j.journal_name
    """)).mappings().all()

    total_articles = db.execute(text(
        "SELECT COALESCE(SUM(article_count), 0) FROM journal_registry WHERE status='active'"
    )).scalar() or 0

    return templates.TemplateResponse(request, "docs/journals.html", {
        "active": "docs", "doc_page": "journals",
        "journals": journals,
        "total_articles": total_articles,
    })


@router.get("/submit-journal", response_class=HTMLResponse)
def docs_submit_journal_get(request: Request, flash: str = None):
    return templates.TemplateResponse(request, "docs/submit_journal.html", {
        "active": "docs", "doc_page": "submit-journal",
        "flash": flash,
    })


@router.post("/submit-journal", response_class=HTMLResponse)
def docs_submit_journal_post(
    request: Request,
    journal_name: str = Form(...),
    url: str = Form(""),
    oai_endpoint: str = Form(""),
    issn: str = Form(""),
    contact_email: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    db.execute(text("""
        INSERT INTO journal_requests (journal_name, url, oai_endpoint, issn, contact_email, notes)
        VALUES (:name, :url, :oai, :issn, :email, :notes)
    """), {
        "name": journal_name.strip(),
        "url": url.strip() or None,
        "oai": oai_endpoint.strip() or None,
        "issn": issn.strip() or None,
        "email": contact_email.strip() or None,
        "notes": notes.strip() or None,
    })
    db.commit()
    return RedirectResponse(
        url="/docs/submit-journal?flash=submitted",
        status_code=303,
    )


@router.get("/api-ingest", response_class=HTMLResponse)
def docs_api_ingest(request: Request):
    return templates.TemplateResponse(request, "docs/api_ingest.html", {
        "active": "docs", "doc_page": "api-ingest",
    })


@router.get("/roadmap", response_class=HTMLResponse)
def docs_roadmap(request: Request):
    return templates.TemplateResponse(request, "docs/roadmap.html", {
        "active": "docs", "doc_page": "roadmap",
    })


@router.get("/funding", response_class=HTMLResponse)
def docs_funding(request: Request):
    return templates.TemplateResponse(request, "docs/funding.html", {
        "active": "docs", "doc_page": "funding",
    })


@router.get("/open-source", response_class=HTMLResponse)
def docs_open_source(request: Request):
    return templates.TemplateResponse(request, "docs/open_source.html", {
        "active": "docs", "doc_page": "open-source",
    })


@router.get("/ai-policy", response_class=HTMLResponse)
def docs_ai_policy(request: Request):
    return templates.TemplateResponse(request, "docs/ai_policy.html", {
        "active": "docs", "doc_page": "ai-policy",
    })


@router.get("/glossary", response_class=HTMLResponse)
def docs_glossary(request: Request):
    return templates.TemplateResponse(request, "docs/glossary.html", {
        "active": "docs", "doc_page": "glossary",
    })
