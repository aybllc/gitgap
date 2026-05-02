"""
gitgap — Convergence Clustering Service (F3-A)

Finds gaps that cluster together semantically — independent papers that
identified the same unresolved problem.

"Agreed-upon gap": cluster with ≥3 members from ≥2 different papers.
These are the most validated gaps in the index.

Algorithm: Union-Find on pairwise cosine distances < threshold.
Complexity: O(n²) on gap count — fine at research-index scale (<10K gaps).
Run on demand via POST /gaps/convergence/run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text

from ..ingest.embeddings import cosine_distance, json_to_vector


# ── Union-Find ────────────────────────────────────────────────────────────────

class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank   = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1


# ── Main clustering function ──────────────────────────────────────────────────

def cluster_gaps(
    db: Session,
    threshold: float = 0.25,
) -> dict:
    """
    Cluster all vectorised gaps by semantic similarity.

    Two gaps are linked if cosine_distance(v1, v2) < threshold.
    Connected components become clusters.

    Clears existing convergence data and replaces with fresh results.
    Only clusters with ≥2 members are stored.

    Returns a stats dict.
    """
    rows = db.execute(text(
        "SELECT id, paper_id, content_vector, confidence "
        "FROM gap_endpoints WHERE content_vector IS NOT NULL"
    )).mappings().all()

    n = len(rows)
    if n < 2:
        return {"gaps_processed": n, "clusters": 0, "agreed": 0, "threshold": threshold}

    gaps    = [dict(r) for r in rows]
    vectors = [json_to_vector(g["content_vector"]) for g in gaps]

    # Build Union-Find over all gap pairs within threshold
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if cosine_distance(vectors[i], vectors[j]) < threshold:
                uf.union(i, j)

    # Group indices by component root
    components: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        components[uf.find(i)].append(i)

    # Keep only multi-member clusters
    clusters = {root: idxs for root, idxs in components.items() if len(idxs) >= 2}

    # Wipe old results (full recompute)
    db.execute(text("DELETE FROM convergence_members"))
    db.execute(text("DELETE FROM convergence_groups"))
    db.commit()

    now = datetime.now(timezone.utc).isoformat()
    agreed_count = 0

    for root, idxs in clusters.items():
        paper_ids    = {gaps[i]["paper_id"] for i in idxs}
        member_count = len(idxs)
        paper_count  = len(paper_ids)
        is_agreed    = member_count >= 3 and paper_count >= 2

        if is_agreed:
            agreed_count += 1

        # Representative = highest-confidence gap in the cluster
        rep_idx    = max(idxs, key=lambda i: gaps[i]["confidence"] or 0.0)
        rep_gap_id = gaps[rep_idx]["id"]

        db.execute(text(
            "INSERT INTO convergence_groups "
            "(representative_gap_id, member_count, paper_count, is_agreed, created_at, updated_at) "
            "VALUES (:rep, :mc, :pc, :ia, :now, :now)"
        ), {
            "rep": rep_gap_id,
            "mc":  member_count,
            "pc":  paper_count,
            "ia":  1 if is_agreed else 0,
            "now": now,
        })
        db.commit()

        group_id = db.execute(text("SELECT last_insert_rowid()")).fetchone()[0]

        for i in idxs:
            db.execute(text(
                "INSERT INTO convergence_members (group_id, gap_id, paper_id) "
                "VALUES (:gid, :gap, :pid)"
            ), {"gid": group_id, "gap": gaps[i]["id"], "pid": gaps[i]["paper_id"]})
        db.commit()

    return {
        "gaps_processed": n,
        "clusters":       len(clusters),
        "agreed":         agreed_count,
        "threshold":      threshold,
    }


def get_agreed_gap_ids(db: Session) -> set[int]:
    """
    Return the set of gap IDs that belong to an agreed-upon cluster.
    Used by globe-data to mark convergence spikes.
    """
    rows = db.execute(text(
        "SELECT cm.gap_id "
        "FROM convergence_members cm "
        "JOIN convergence_groups cg ON cg.id = cm.group_id "
        "WHERE cg.is_agreed = 1"
    )).fetchall()
    return {r[0] for r in rows}


def get_convergence_summary(db: Session) -> list[dict]:
    """
    Return all convergence groups with their representative gap declaration.
    Used by GET /gaps/convergence.
    """
    rows = db.execute(text("""
        SELECT
            cg.id, cg.representative_gap_id, cg.member_count, cg.paper_count,
            cg.is_agreed, cg.created_at,
            ge.declaration_text, ge.gateway_term, ge.gap_class, ge.confidence
        FROM convergence_groups cg
        JOIN gap_endpoints ge ON ge.id = cg.representative_gap_id
        ORDER BY cg.is_agreed DESC, cg.member_count DESC, cg.paper_count DESC
    """)).mappings().all()
    return [dict(r) for r in rows]
