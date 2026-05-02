"""
gitgap — Ingestion pipeline
Coordinates: search → fetch BioC → parse → gateway filter → store to DB + JSON

CLI:
  python -m app.ingest.pipeline --query "Hubble tension" --max 20
"""

import json
import uuid as _uuid_mod
import argparse
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from .pmc import search_pmc, fetch_bioc, fetch_jats
from .parser import parse_bioc, parse_jats, ParsedPaper
from .filter import run_gateway, gateway_verdict
from .classify import classify_gap
from .embeddings import embed_text, vector_to_json
from ..services.disciplines import enrich_discipline
from ..services.cap_score import compute_cap
from ..services.ai_detection import run_on_paper as _ai_interrogate

CACHE_DIR   = Path("data/cache")
RESULTS_DIR = Path("data/results")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(query: str, max_results: int = 50, phase: int = 1,
        cache: bool = True, db=None):
    """
    Full pipeline for a query term.
    db: optional SQLAlchemy session — if provided, writes results to DB.
    Returns list of gap candidate dicts.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n=== gitgap pipeline ===")
    print(f"Query:   {query}")
    print(f"Max:     {max_results} | Phase: {phase}")
    print(f"Started: {_now()}")

    run_id = None
    if db:
        result = db.execute(text(
            "INSERT INTO ingest_runs (query_term, started_at, status) "
            "VALUES (:q, :t, 'running')"
        ), {"q": query, "t": _now()})
        db.commit()
        run_id = result.lastrowid

    # ── 1. Search ─────────────────────────────────────────────────────────────
    print(f"\n[1/4] Searching PMC...")
    pmcids = search_pmc(query, max_results=max_results)
    print(f"      {len(pmcids)} PMC IDs found")

    # ── 2. Fetch BioC JSON (primary) → JATS XML (fallback) ───────────────────
    print(f"\n[2/4] Fetching BioC JSON (JATS fallback for non-corpus articles)...")
    bioc_docs: dict = {}   # pmcid → BioC dict
    jats_docs: dict = {}   # pmcid → JATS XML string (fallback only)

    for i, pmcid in enumerate(pmcids):
        bioc_cache = CACHE_DIR / f"PMC{pmcid}.json"
        jats_cache = CACHE_DIR / f"PMC{pmcid}.xml"

        if cache and bioc_cache.exists():
            with open(bioc_cache) as f:
                bioc_docs[pmcid] = json.load(f)
            print(f"  [{i+1}/{len(pmcids)}] PMC{pmcid} (BioC cached)")
        elif cache and jats_cache.exists():
            with open(jats_cache) as f:
                jats_docs[pmcid] = f.read()
            print(f"  [{i+1}/{len(pmcids)}] PMC{pmcid} (JATS cached)")
        else:
            print(f"  [{i+1}/{len(pmcids)}] PMC{pmcid} fetching BioC...")
            doc = fetch_bioc(pmcid)
            if doc:
                bioc_docs[pmcid] = doc
                if cache:
                    with open(bioc_cache, "w") as f:
                        json.dump(doc, f)
            else:
                print(f"  [{i+1}/{len(pmcids)}] PMC{pmcid} BioC unavailable — trying JATS...")
                xml = fetch_jats(pmcid)
                jats_docs[pmcid] = xml
                if xml and cache:
                    with open(jats_cache, "w") as f:
                        f.write(xml)

    n_bioc = sum(1 for v in bioc_docs.values() if v is not None)
    n_jats = sum(1 for v in jats_docs.values() if v is not None)
    fetched = n_bioc + n_jats
    print(f"      BioC: {n_bioc}  JATS: {n_jats}  Total: {fetched}/{len(pmcids)}")

    # ── 3. Parse ──────────────────────────────────────────────────────────────
    print(f"\n[3/4] Parsing sections...")
    papers = []

    for pmcid, doc in bioc_docs.items():
        if doc is None:
            continue
        paper = parse_bioc(pmcid, doc)
        if paper and paper.conclusions_text:
            papers.append(paper)

    for pmcid, xml in jats_docs.items():
        if xml is None:
            continue
        paper = parse_jats(pmcid, xml)
        if paper and paper.conclusions_text:
            papers.append(paper)

    print(f"      Papers with conclusions: {len(papers)}/{fetched}")

    # ── 4. Gateway filter + store ─────────────────────────────────────────────
    print(f"\n[4/4] Gateway filter (Phase {phase})...")
    all_candidates = []
    go_count = 0

    for paper in papers:
        verdict = gateway_verdict(paper)
        candidates = run_gateway(paper, phase=phase)

        # Store paper to DB
        paper_db_id = None
        if db:
            existing = db.execute(text(
                "SELECT id FROM papers WHERE pmcid = :pmcid"
            ), {"pmcid": paper.pmcid}).fetchone()

            if existing:
                continue  # paper already processed — gaps were extracted on first ingest
            else:
                res = db.execute(text(
                    "INSERT INTO papers "
                    "(pmcid, doi, title, journal, pub_year, abstract_text, "
                    "methods_text, conclusions_text, ingested_at) "
                    "VALUES (:pmcid,:doi,:title,:journal,:year,:abstract,"
                    ":methods,:conclusions,:now)"
                ), {
                    "pmcid": paper.pmcid,
                    "doi": paper.doi,
                    "title": paper.title,
                    "journal": paper.journal,
                    "year": paper.pub_year,
                    "abstract": paper.abstract_text,
                    "methods": paper.methods_text,
                    "conclusions": paper.conclusions_text,
                    "now": _now(),
                })
                db.commit()
                paper_db_id = res.lastrowid
                # AI interrogation — non-declared = treated as AI-free
                _ai_interrogate(
                    paper_db_id,
                    paper.abstract_text or "",
                    paper.conclusions_text or "",
                    db,
                )

        if verdict["verdict"] == "GO":
            go_count += 1
            print(f"  GO   PMC{paper.pmcid} — {len(candidates)} declaration(s)")
            if paper.title:
                print(f"       {paper.title[:75]}")

            for c in candidates:
                all_candidates.append(c)
                if db and paper_db_id:
                    gap_class  = classify_gap(c.declaration_text, c.gateway_term)
                    gap_vector = vector_to_json(embed_text(c.declaration_text or ""))
                    enrichment = enrich_discipline(
                        c.declaration_text, c.gateway_term, paper.title or ""
                    )
                    db.execute(text(
                        "INSERT OR IGNORE INTO gap_endpoints "
                        "(paper_id, declaration_text, section_source, phase, "
                        "confidence, gateway_term, keeper_reviewed, "
                        "keeper_verdict, gap_class, content_vector, "
                        "source_discipline, target_disciplines, bridge_potential, bridge_rationale, "
                        "created_at) "
                        "VALUES (:pid,:decl,:sec,:phase,:conf,:term,0,'pending',"
                        ":gap_class,:vector,"
                        ":src_disc,:tgt_disc,:bridge,:rationale,"
                        ":now)"
                    ), {
                        "pid":       paper_db_id,
                        "decl":      c.declaration_text,
                        "sec":       c.section_source,
                        "phase":     c.phase,
                        "conf":      c.confidence,
                        "term":      c.gateway_term,
                        "gap_class": gap_class,
                        "vector":    gap_vector,
                        "src_disc":  enrichment["source_discipline"],
                        "tgt_disc":  json.dumps(enrichment["target_disciplines"]),
                        "bridge":    enrichment["bridge_potential"],
                        "rationale": enrichment["bridge_rationale"],
                        "now":       _now(),
                    })
                    # F3-B: Compute CAP immediately — rowid valid before commit in SQLite
                    new_gap_id = db.execute(text("SELECT last_insert_rowid()")).fetchone()
                    if new_gap_id and new_gap_id[0]:
                        try:
                            compute_cap(new_gap_id[0], db)
                        except Exception:
                            pass  # Non-fatal — CAP rescored via /gaps/cap/recompute-all
            if db:
                db.commit()
        else:
            print(f"  ---  PMC{paper.pmcid}")

    # ── Finalise run record ───────────────────────────────────────────────────
    if db and run_id:
        db.execute(text(
            "UPDATE ingest_runs SET pmcids_fetched=:f, pmcids_parsed=:p, "
            "gaps_found=:g, completed_at=:t, status='complete' WHERE id=:id"
        ), {
            "f": fetched, "p": len(papers),
            "g": len(all_candidates), "t": _now(), "id": run_id,
        })
        db.commit()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n=== Results ===")
    print(f"Searched:     {len(pmcids)}")
    print(f"Fetched:      {fetched}")
    print(f"Conclusions:  {len(papers)}")
    print(f"GO:           {go_count} ({round(go_count/len(papers)*100) if papers else 0}%)")
    print(f"Candidates:   {len(all_candidates)}")

    # ── Save JSON ─────────────────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"{ts}_{query.replace(' ','_')[:40]}_p{phase}.json"
    with open(out, "w") as f:
        json.dump({
            "query": query, "phase": phase, "ran_at": _now(),
            "stats": {
                "searched": len(pmcids), "fetched": fetched,
                "had_conclusions": len(papers), "go_count": go_count,
                "total_candidates": len(all_candidates),
            },
            "candidates": [
                {
                    "pmcid": c.paper_pmcid,
                    "declaration": c.declaration_text,
                    "gateway_term": c.gateway_term,
                    "phase": c.phase,
                    "confidence": c.confidence,
                    "section": c.section_source,
                    "keeper_required": True,
                }
                for c in all_candidates
            ]
        }, f, indent=2)
    print(f"Saved:        {out}")
    return all_candidates


def run_from_text(
    title: str,
    abstract_text: str,
    full_text: str = "",
    doi: str = None,
    year: int = None,
    journal: str = None,
    source: str = "external",
    catching_cosmoid: str = None,
    db=None,
) -> dict:
    """
    F3-C: Ingest a paper from raw text — no PMC fetch required.

    Accepts Zenodo papers, preprints, personal uploads, or any text that
    shouldn't or can't be retrieved from PMC.

    - pmcid is stored as `EXT:{doi}` (if doi provided) or `EXT:{uuid4}`.
    - full_text is scanned as the conclusions/discussion section.
    - If catching_cosmoid is set, each extracted gap is immediately marked CAUGHT.

    Returns:
      {
        "synthetic_pmcid": str,
        "paper_id": int | None,
        "gaps_found": int,
        "gap_ids": list[int],
      }
    """
    # Synthetic unique pmcid — preserves UNIQUE constraint without schema change
    synthetic_pmcid = f"EXT:{doi}" if doi else f"EXT:{_uuid_mod.uuid4()}"

    # Build a ParsedPaper directly from supplied text
    paper = ParsedPaper(
        pmcid=synthetic_pmcid,
        doi=doi,
        title=title,
        journal=journal or source,
        pub_year=year,
        abstract_text=abstract_text,
        # conclusions_text is what the gateway filter scans most heavily;
        # if full_text is provided use it, otherwise fall back to abstract
        conclusions_text=full_text.strip() if full_text and full_text.strip() else abstract_text,
    )

    candidates = run_gateway(paper, phase=1)

    paper_db_id = None
    gap_ids: list = []

    if not db:
        return {
            "synthetic_pmcid": synthetic_pmcid,
            "paper_id": None,
            "gaps_found": len(candidates),
            "gap_ids": [],
        }

    # ── Store paper record ────────────────────────────────────────────────────
    existing = db.execute(text(
        "SELECT id FROM papers WHERE pmcid = :pmcid"
    ), {"pmcid": synthetic_pmcid}).fetchone()

    if existing:
        paper_db_id = existing[0]
    else:
        res = db.execute(text(
            "INSERT INTO papers "
            "(pmcid, doi, title, journal, pub_year, abstract_text, conclusions_text, ingested_at) "
            "VALUES (:pmcid, :doi, :title, :journal, :year, :abstract, :conclusions, :now)"
        ), {
            "pmcid":      synthetic_pmcid,
            "doi":        doi,
            "title":      title,
            "journal":    journal or source,
            "year":       year,
            "abstract":   abstract_text,
            "conclusions": full_text.strip() if full_text and full_text.strip() else abstract_text,
            "now":        _now(),
        })
        db.commit()
        paper_db_id = res.lastrowid
        # AI interrogation — non-declared = treated as AI-free
        _ai_interrogate(
            paper_db_id,
            abstract_text or "",
            full_text or abstract_text or "",
            db,
        )

    # ── Store gap candidates ──────────────────────────────────────────────────
    for c in candidates:
        gap_class  = classify_gap(c.declaration_text, c.gateway_term)
        gap_vector = vector_to_json(embed_text(c.declaration_text or ""))
        enrichment = enrich_discipline(c.declaration_text, c.gateway_term, title or "")

        db.execute(text(
            "INSERT INTO gap_endpoints "
            "(paper_id, declaration_text, section_source, phase, "
            "confidence, gateway_term, keeper_reviewed, keeper_verdict, "
            "gap_class, content_vector, "
            "source_discipline, target_disciplines, bridge_potential, bridge_rationale, "
            "created_at) "
            "VALUES (:pid, :decl, :sec, :phase, :conf, :term, 0, 'pending', "
            ":gap_class, :vector, "
            ":src_disc, :tgt_disc, :bridge, :rationale, "
            ":now)"
        ), {
            "pid":       paper_db_id,
            "decl":      c.declaration_text,
            "sec":       c.section_source,
            "phase":     c.phase,
            "conf":      c.confidence,
            "term":      c.gateway_term,
            "gap_class": gap_class,
            "vector":    gap_vector,
            "src_disc":  enrichment["source_discipline"],
            "tgt_disc":  json.dumps(enrichment["target_disciplines"]),
            "bridge":    enrichment["bridge_potential"],
            "rationale": enrichment["bridge_rationale"],
            "now":       _now(),
        })
        db.commit()

        new_gap_id = db.execute(text("SELECT last_insert_rowid()")).fetchone()[0]
        gap_ids.append(new_gap_id)

        # CAP score — best-effort initial estimate
        try:
            compute_cap(new_gap_id, db)
        except Exception:
            pass

        # Mark CAUGHT immediately if catching_cosmoid is provided
        if catching_cosmoid:
            db.execute(text(
                "UPDATE gap_endpoints "
                "SET caught_paper_cosmoid = :cosmoid, caught_at = :now, catch_confidence = 0.75 "
                "WHERE id = :id"
            ), {"cosmoid": catching_cosmoid, "now": _now(), "id": new_gap_id})
            db.commit()

    return {
        "synthetic_pmcid": synthetic_pmcid,
        "paper_id":        paper_db_id,
        "gaps_found":      len(candidates),
        "gap_ids":         gap_ids,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--max", type=int, default=50)
    parser.add_argument("--phase", type=int, default=1)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip DB writes (JSON output only)")
    args = parser.parse_args()

    if args.no_db:
        run(query=args.query, max_results=args.max,
            phase=args.phase, cache=not args.no_cache)
    else:
        from ..database import SessionLocal, init_db
        init_db()
        db = SessionLocal()
        try:
            run(query=args.query, max_results=args.max,
                phase=args.phase, cache=not args.no_cache, db=db)
        finally:
            db.close()
