"""
gitgap — CAP Score Service (F3-B)

CAP = Corpus-Appreciated Phenomenon score.
Measures how "ripe" a gap is for resolution — not whether it has been resolved,
but how ready the corpus is to support a serious attempt.

Formula: CAP_raw = (EC + MS) + EE − (MF + TCR)
         CAP     = (CAP_raw + 2) / 5    → normalised to [0, 1]

Components (each normalised to [0, 1]):
  EC  – Existence Consensus:     how many independent papers named this gap
  MS  – Measurement Stability:   how consistent confidence is across those papers
  EE  – Explanatory Entropy:     how many disciplines point at this gap
  MF  – Methodological Formal.:  how defined the method to fill it already is (SUBTRACT)
  TCR – Temporal Convergence:    how recently an attempt was made (SUBTRACT)

High CAP = widely agreed upon, multi-disciplinary, no good method yet, not being
           actively attempted → the most valuable open gap.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text


# ── Component computation ─────────────────────────────────────────────────────

def _ec(cluster_paper_count: int) -> float:
    """Existence Consensus — normalised paper count (5+ = full score)."""
    return min(cluster_paper_count, 5) / 5.0


def _ms(confidences: list[float]) -> float:
    """
    Measurement Stability — inverse of standard deviation.
    Single observation → perfect stability (1.0).
    """
    if len(confidences) <= 1:
        return 1.0
    mean = sum(confidences) / len(confidences)
    variance = sum((c - mean) ** 2 for c in confidences) / len(confidences)
    std_dev = math.sqrt(variance)
    return max(0.0, 1.0 - std_dev)


def _ee(target_disciplines: list[str]) -> float:
    """Explanatory Entropy — discipline diversity (4+ = full score)."""
    return min(len(target_disciplines), 4) / 4.0


def _mf(gap_class: str | None, keeper_verdict: str | None) -> float:
    """
    Methodological Formalization — how solved/defined the gap already is.
    Higher = more formalized = subtract more from CAP.
    """
    is_methodology = (gap_class or "").lower() == "methodology"
    is_keeper_pass = (keeper_verdict or "") == "pass"
    if is_methodology and is_keeper_pass:
        return 0.8
    if is_methodology:
        return 0.5
    if is_keeper_pass:
        return 0.4
    return 0.1


def _tcr(caught_at_iso: str | None) -> float:
    """
    Temporal Convergence Rate — recency of an attempt to fill this gap.
    Recent catch → higher subtraction (gap is being addressed).
    """
    if not caught_at_iso:
        return 0.0
    try:
        caught_dt = datetime.fromisoformat(caught_at_iso.replace("Z", "+00:00"))
        caught_dt = caught_dt.replace(tzinfo=timezone.utc) if caught_dt.tzinfo is None else caught_dt
        age_days = (datetime.now(timezone.utc) - caught_dt).days
        if age_days <= 90:
            return 1.0
        if age_days <= 180:
            return 0.6
        if age_days <= 365:
            return 0.3
        return 0.05
    except Exception:
        return 0.0


def _cap_from_components(ec: float, ms: float, ee: float, mf: float, tcr: float) -> float:
    """Apply the CAP formula and normalise to [0, 1]."""
    raw = (ec + ms) + ee - (mf + tcr)
    # raw ∈ [-2, 3]; normalise to [0, 1]
    return round(max(0.0, min(1.0, (raw + 2.0) / 5.0)), 4)


# ── Per-gap computation ────────────────────────────────────────────────────────

def compute_cap(gap_id: int, db: Session) -> dict:
    """
    Compute and persist the CAP score for a single gap.

    Looks up:
    - convergence_members / convergence_groups for EC and MS
    - gap_endpoints fields for EE, MF, TCR

    Returns the score breakdown dict and persists cap_score to gap_endpoints.
    """
    row = db.execute(text(
        "SELECT id, paper_id, confidence, gap_class, keeper_verdict, "
        "       target_disciplines, caught_at "
        "FROM gap_endpoints WHERE id = :id"
    ), {"id": gap_id}).mappings().first()

    if row is None:
        raise ValueError(f"Gap {gap_id} not found")

    # Target disciplines
    try:
        tgt_discs = json.loads(row["target_disciplines"] or "[]")
    except Exception:
        tgt_discs = []

    # Convergence cluster lookup
    cluster_row = db.execute(text("""
        SELECT cg.paper_count, cg.member_count, cg.id AS group_id
        FROM convergence_members cm
        JOIN convergence_groups cg ON cg.id = cm.group_id
        WHERE cm.gap_id = :gid
        LIMIT 1
    """), {"gid": gap_id}).mappings().first()

    if cluster_row and cluster_row["member_count"] > 1:
        paper_count = cluster_row["paper_count"]

        # Confidences of all gaps in the cluster
        conf_rows = db.execute(text("""
            SELECT ge.confidence
            FROM convergence_members cm
            JOIN gap_endpoints ge ON ge.id = cm.gap_id
            WHERE cm.group_id = :gid AND ge.confidence IS NOT NULL
        """), {"gid": cluster_row["group_id"]}).fetchall()

        confidences = [r[0] for r in conf_rows]
    else:
        paper_count  = 1
        confidences  = [row["confidence"]] if row["confidence"] is not None else [0.5]

    # Compute components
    ec  = _ec(paper_count)
    ms  = _ms(confidences)
    ee  = _ee(tgt_discs)
    mf  = _mf(row["gap_class"], row["keeper_verdict"])
    tcr = _tcr(row["caught_at"])

    cap = _cap_from_components(ec, ms, ee, mf, tcr)

    # Persist
    db.execute(text(
        "UPDATE gap_endpoints SET cap_score = :cap WHERE id = :id"
    ), {"cap": cap, "id": gap_id})
    db.commit()

    return {
        "gap_id":     gap_id,
        "cap_score":  cap,
        "components": {
            "EC":  round(ec,  4),
            "MS":  round(ms,  4),
            "EE":  round(ee,  4),
            "MF":  round(mf,  4),
            "TCR": round(tcr, 4),
        },
    }


def recompute_all_cap(db: Session) -> dict:
    """
    Recompute CAP for every gap in the index.
    Returns summary stats.
    """
    ids = db.execute(text("SELECT id FROM gap_endpoints")).fetchall()
    gap_ids = [r[0] for r in ids]

    updated = 0
    errors  = 0
    for gid in gap_ids:
        try:
            compute_cap(gid, db)
            updated += 1
        except Exception:
            errors += 1

    return {
        "total":   len(gap_ids),
        "updated": updated,
        "errors":  errors,
    }
