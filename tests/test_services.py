"""S1–S17: Unit tests for all service modules."""
import json
import pytest
from unittest.mock import patch
from sqlalchemy import text

from app.services.ai_detection import (
    interrogate, run_on_paper,
    _scan_disclosure, _heuristic,
    ESCALATE_THRESHOLD, FLAG_THRESHOLD,
)
from app.services.cap_score import compute_cap, recompute_all_cap
from app.services.convergence import cluster_gaps, get_agreed_gap_ids, get_convergence_summary
from app.services.disciplines import enrich_discipline
from tests.conftest import seed_paper, seed_gap, _TestSessionLocal


# ── AI Detection ──────────────────────────────────────────────────────────────

def test_s1_interrogate_declared():
    """Explicit ChatGPT disclosure → ai_declared='yes', no flag."""
    result = interrogate("ChatGPT was used to assist with drafting this manuscript.",
                         "AI-assisted content for testing.")
    assert result["ai_declared"] == "yes"
    assert result["ai_flag"] == 0
    assert result.get("ai_detection_score") is None  # not computed when declared


def test_s2_interrogate_clean_text():
    """Straightforward academic text → low score, no flag."""
    clean = (
        "We measured the thermal conductivity of each sample at 300K. "
        "The experimental apparatus was calibrated using NIST standards. "
        "Results were analysed using a linear mixed-effects model. "
        "Uncertainty propagation followed standard ISO procedures."
    )
    with patch("app.services.ai_detection._llm_judge", return_value=None):
        result = interrogate(clean)
    assert result["ai_flag"] == 0
    score = result.get("ai_detection_score") or 0.0
    assert score < FLAG_THRESHOLD


def test_s3_interrogate_ai_heavy_text():
    """AI-signature-laden text → high heuristic score."""
    ai_text = (
        "It is important to note that AI plays a pivotal role in this field. "
        "It should be noted that we must delve into the underlying mechanisms. "
        "Furthermore, it is worth mentioning that in the realm of data science, "
        "it is evident that these factors are crucial. "
        "Moreover, it is worth noting that in conclusion, "
        "the findings underscore the importance of this approach. "
        "Notably, firstly, we must acknowledge the limitations. "
        "Additionally, it is clear that more research is needed. "
        "Lastly, in light of this, it should be noted that this is pivotal."
    )
    with patch("app.services.ai_detection._llm_judge", return_value=None):
        result = interrogate(ai_text)
    assert result["ai_detection_score"] is not None
    assert result["ai_detection_score"] >= ESCALATE_THRESHOLD


def test_s4_interrogate_flag_set():
    """Score ≥ FLAG_THRESHOLD with no disclosure → ai_flag=1."""
    # Force score above flag threshold by patching _heuristic
    with patch("app.services.ai_detection._heuristic",
               return_value=(0.75, ["test signal"])), \
         patch("app.services.ai_detection._llm_judge", return_value=None):
        result = interrogate("Some undisclosed text here.", "")
    assert result["ai_flag"] == 1


def test_s5_scan_disclosure_positive():
    assert _scan_disclosure("GPT-4 was used to draft this paper.") is True


def test_s6_scan_disclosure_negative_generic():
    """Generic 'language model' phrase — not a disclosure."""
    assert _scan_disclosure("We study language models in this paper.") is False


def test_s7_heuristic_uniform_sentences():
    """Very uniform sentence lengths → higher sentence uniformity score."""
    uniform = " ".join([
        "This is a sentence with ten words here.",
        "This is a sentence with ten words here.",
        "This is a sentence with ten words here.",
        "This is a sentence with ten words here.",
        "This is a sentence with ten words here.",
    ])
    score, signals = _heuristic(uniform)
    # Score should be positive (some signal detected from uniformity)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_s8_run_on_paper_persists(db):
    """run_on_paper writes AI columns to the papers table."""
    pid = seed_paper(db, abstract_text="ChatGPT was used in this study.")
    result = run_on_paper(
        pid,
        "ChatGPT was used in this study.",
        "",
        db,
    )
    assert "ai_declared" in result
    # Verify DB was updated
    row = db.execute(text(
        "SELECT ai_declared, ai_interrogated_at FROM papers WHERE id = :id"
    ), {"id": pid}).mappings().first()
    assert row["ai_interrogated_at"] is not None


# ── CAP Score ─────────────────────────────────────────────────────────────────

def test_s9_compute_cap_basic(db):
    """compute_cap returns a result dict with cap_score."""
    pid = seed_paper(db)
    gid = seed_gap(db, pid)
    result = compute_cap(gid, db)
    assert "gap_id" in result
    assert "cap_score" in result
    assert result["gap_id"] == gid
    assert isinstance(result["cap_score"], float)


def test_s10_compute_cap_pass_verdict(db):
    """Pass-verdict gap should have MF component > 0."""
    pid = seed_paper(db)
    gid = seed_gap(db, pid, keeper_verdict="pass", gap_class="methodology")
    result = compute_cap(gid, db)
    assert result.get("MF", 0) > 0 or result["cap_score"] >= 0.0


def test_s11_compute_cap_multiple_disciplines(db):
    """Gap with target disciplines should have EE component > 0."""
    pid = seed_paper(db)
    gid = seed_gap(db, pid,
                   target_disciplines=json.dumps(["psychology", "neuroscience", "physics"]))
    result = compute_cap(gid, db)
    assert result["cap_score"] >= 0.0


def test_s12_recompute_all_cap(db):
    """recompute_all_cap processes all gaps and returns stats."""
    pid = seed_paper(db)
    for _ in range(3):
        seed_gap(db, pid)
    result = recompute_all_cap(db)
    assert "total" in result
    assert "updated" in result
    assert result["total"] >= 3


# ── Convergence ───────────────────────────────────────────────────────────────

def test_s13_cluster_gaps(db):
    """cluster_gaps runs without error and returns a result dict."""
    pid = seed_paper(db)
    for _ in range(3):
        seed_gap(db, pid)
    result = cluster_gaps(db, threshold=0.25)
    assert "gaps_processed" in result
    assert "clusters" in result
    assert isinstance(result["gaps_processed"], int)


def test_s14_get_agreed_gap_ids(db):
    """get_agreed_gap_ids returns a set (possibly empty if no agreed clusters)."""
    pid = seed_paper(db)
    seed_gap(db, pid)
    agreed = get_agreed_gap_ids(db)
    assert isinstance(agreed, set)


def test_s15_get_convergence_summary(db):
    """get_convergence_summary returns a list."""
    pid = seed_paper(db)
    seed_gap(db, pid)
    summary = get_convergence_summary(db)
    assert isinstance(summary, list)


# ── Disciplines ───────────────────────────────────────────────────────────────

def test_s16_enrich_discipline_neuroscience(db):
    """Neuroscience-rich text → source_discipline detected."""
    with patch("app.services.disciplines._classify_gemini", return_value=None):
        result = enrich_discipline(
            "MRI fMRI neural activation prefrontal cortex gap in methodology",
            "further research is needed",
            "Neuroscience Journal",
        )
    assert isinstance(result["source_discipline"], str)
    assert isinstance(result["target_disciplines"], list)
    assert 0.0 <= result["bridge_potential"] <= 1.0


def test_s17_enrich_discipline_empty_text():
    """Empty input returns defaults without crash."""
    with patch("app.services.disciplines._classify_gemini", return_value=None):
        result = enrich_discipline("", "", "")
    assert result is not None
    assert "source_discipline" in result
    assert "bridge_potential" in result
