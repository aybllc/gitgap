"""
gitgap — Gap detection pipeline API
Surfaces explicitly declared research gaps from peer-reviewed literature.
Author: Eric D. Martin | ORCID 0009-0006-5944-1742
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

from .database import init_db
from .routers import gaps, ingest, web, admin, docs

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="gitgap",
    description=(
        "Gap detection pipeline — surfaces explicitly declared research gaps "
        "from peer-reviewed PMC literature. "
        "NAUGHT → CAUGHT → FOUND. "
        "Author: Eric D. Martin | ORCID 0009-0006-5944-1742"
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(gaps.router)
app.include_router(ingest.router)
app.include_router(web.router)
app.include_router(admin.router)
app.include_router(docs.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "system": "gitgap",
    }


@app.get("/")
def root():
    return RedirectResponse(url="/view/globe", status_code=302)
