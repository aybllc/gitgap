"""
gitgap — PMC ingestion client
Primary: BioC JSON API — returns pre-labeled section passages (CONCL, METHODS, etc.)
Fallback: E-utilities JATS XML
Author: Eric D. Martin | ORCID 0009-0006-5944-1742
"""

import os
import time
import httpx
from typing import Optional

# ── BioC API (preferred) ──────────────────────────────────────────────────────
# Returns JSON with section_type-labeled passages. No auth required.
BIOC_BASE = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi"

# ── E-utilities (search + fallback fetch) ────────────────────────────────────
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EMAIL   = os.getenv("NCBI_EMAIL", "editor@eaiou.org")
API_KEY = os.getenv("NCBI_API_KEY", "")
RATE_LIMIT = 0.34 if not API_KEY else 0.11  # seconds between requests


def _eutils_params(extra: dict) -> dict:
    p = {"email": EMAIL, "tool": "gitgap"}
    if API_KEY:
        p["api_key"] = API_KEY
    p.update(extra)
    return p


# ── BioC fetch ────────────────────────────────────────────────────────────────

def fetch_bioc(pmcid: str) -> Optional[dict]:
    """
    Fetch BioC JSON for a single PMC article.
    Sections pre-labeled: TITLE, ABSTRACT, INTRO, METHODS,
    RESULTS, DISCUSS, CONCL, REF, FIG, TABLE.
    Returns the parsed JSON dict or None on failure.
    """
    # BioC accepts PMC IDs with or without 'PMC' prefix
    clean_id = pmcid.replace("PMC", "").strip()
    url = f"{BIOC_BASE}/BioC_json/PMC{clean_id}/unicode"

    with httpx.Client(timeout=60) as client:
        try:
            resp = client.get(url)
            resp.raise_for_status()
            time.sleep(0.2)  # polite — no stated rate limit, be conservative
            return resp.json()
        except Exception as e:
            print(f"  fetch_bioc error for PMC{clean_id}: {e}")
            return None


def extract_sections(bioc_doc: dict) -> dict:
    """
    Extract named sections from a BioC collection response.
    Structure: collection (list) → documents → passages

    Returns dict: {section_type: combined_text}
    Key section_type values: TITLE, ABSTRACT, INTRO, METHODS, CONCL, DISCUSS, REF
    """
    sections = {}

    # BioC response is a list containing one collection object
    collections = bioc_doc if isinstance(bioc_doc, list) else [bioc_doc]

    for collection in collections:
        for doc in collection.get("documents", []):
            for passage in doc.get("passages", []):
                infons = passage.get("infons", {})
                sec_type = (
                    infons.get("section_type") or
                    infons.get("type") or
                    "UNKNOWN"
                ).upper()
                text = passage.get("text", "").strip()
                if text:
                    if sec_type in sections:
                        sections[sec_type] += " " + text
                    else:
                        sections[sec_type] = text

    return sections


def get_doi_from_bioc(bioc_doc: dict) -> str | None:
    """Extract DOI from first passage infons (BioC stores metadata there)."""
    collections = bioc_doc if isinstance(bioc_doc, list) else [bioc_doc]
    for collection in collections:
        for doc in collection.get("documents", []):
            for passage in doc.get("passages", []):
                infons = passage.get("infons", {})
                doi = infons.get("article-id_doi")
                if doi:
                    return doi
    return None




# ── E-utilities search ────────────────────────────────────────────────────────

def search_pmc(query: str, max_results: int = 100) -> list[str]:
    """
    Search PMC open access articles for a query term.
    Returns list of PMC IDs (without 'PMC' prefix).
    """
    url = f"{EUTILS_BASE}/esearch.fcgi"
    params = _eutils_params({
        "db": "pmc",
        "term": f"{query} AND open access[filter]",
        "retmax": max_results,
        "retmode": "json",
    })
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        time.sleep(RATE_LIMIT)
        return data.get("esearchresult", {}).get("idlist", [])


# ── Combined fetch ────────────────────────────────────────────────────────────

# ── E-utilities JATS fallback ─────────────────────────────────────────────────

def fetch_jats(pmcid: str) -> Optional[str]:
    """
    E-utilities fallback: fetch JATS XML for articles not in BioC corpus.
    Used when BioC returns non-JSON (article not in PMC OA full-text subset).
    Returns raw XML string, or None on failure.
    """
    clean_id = pmcid.replace("PMC", "").strip()
    url = f"{EUTILS_BASE}/efetch.fcgi"
    params = _eutils_params({
        "db": "pmc",
        "id": clean_id,
        "rettype": "xml",
        "retmode": "xml",
    })
    with httpx.Client(timeout=60) as client:
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            time.sleep(RATE_LIMIT)
            text = resp.text.strip()
            return text if text.startswith("<") else None
        except Exception as e:
            print(f"  fetch_jats error for PMC{clean_id}: {e}")
            return None


def fetch_batch(pmcids: list[str]) -> dict[str, Optional[dict]]:
    """
    Fetch BioC JSON for a list of PMC IDs.
    Returns dict: {pmcid: bioc_dict or None}
    """
    results = {}
    for i, pmcid in enumerate(pmcids):
        print(f"  [{i+1}/{len(pmcids)}] fetching PMC{pmcid}")
        results[pmcid] = fetch_bioc(pmcid)
    return results
