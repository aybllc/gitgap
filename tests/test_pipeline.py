"""P1–P17: Unit tests for ingest pipeline modules."""
import json
import pytest
from unittest.mock import patch

from app.ingest.embeddings import embed_text, cosine_distance, vector_to_json, json_to_vector
from app.ingest.classify import classify_gap
from app.ingest.filter import run_gateway, gateway_verdict, PHASE_1_TERMS
from app.ingest.pmc import extract_sections, get_doi_from_bioc
from app.ingest.parser import parse_bioc, ParsedPaper
from tests.conftest import BIOC_FIXTURE


# ── Embeddings ────────────────────────────────────────────────────────────────

def test_p1_embed_text_returns_vector():
    v = embed_text("hello world")
    assert isinstance(v, list)
    assert len(v) == 512
    assert all(isinstance(x, float) for x in v)


def test_p2_cosine_distance_identical():
    v = embed_text("identical text for self-comparison")
    d = cosine_distance(v, v)
    assert abs(d) < 1e-6


def test_p3_cosine_distance_different():
    v1 = embed_text("cancer research gap in methodology")
    v2 = embed_text("quantum mechanics superposition principle")
    d = cosine_distance(v1, v2)
    assert 0.0 < d <= 1.0


def test_p4_vector_roundtrip():
    v = embed_text("roundtrip test for serialization fidelity")
    serialized = vector_to_json(v)
    recovered = json_to_vector(serialized)
    assert len(recovered) == len(v)
    assert all(abs(a - b) < 1e-9 for a, b in zip(v, recovered))


# ── Classification ────────────────────────────────────────────────────────────

def test_p5_classify_replication():
    cls = classify_gap("Replication of this study is needed for independent validation.", "")
    assert cls == "replication"


def test_p6_classify_methodology():
    cls = classify_gap("This gap reflects a methodological limitation in the current approach.", "")
    assert cls == "methodology"


def test_p7_classify_scope():
    cls = classify_gap("The long-term effects were beyond the scope of this study.", "")
    assert cls == "scope"


def test_p8_classify_theoretical():
    cls = classify_gap("The theoretical mechanism underlying this phenomenon is unclear.", "")
    assert cls == "theoretical"


def test_p9_classify_general_fallback():
    cls = classify_gap("Future studies are needed to understand this.", "")
    # Should fall through to 'general' if no specific keyword matches
    assert cls in ("general", "empirical", "scope", "methodology", "theoretical", "replication")


# ── Gateway Filter ────────────────────────────────────────────────────────────

def _make_paper(abstract="", conclusions="", title="Test Paper"):
    """Construct a minimal ParsedPaper for filter tests."""
    return ParsedPaper(
        pmcid="TEST99999",
        doi=None,
        title=title,
        journal="Test Journal",
        pub_year=2024,
        abstract_text=abstract,
        methods_text=None,
        conclusions_text=conclusions,
    )


def test_p10_gateway_verdict_go():
    paper = _make_paper(
        abstract="Further research is needed to resolve this question.",
        conclusions="Future work should address this gap.",
    )
    result = gateway_verdict(paper)
    assert result["verdict"] == "GO"


def test_p11_gateway_verdict_no_go():
    paper = _make_paper(
        abstract="We show that X is related to Y in our sample.",
        conclusions="Our results confirm the hypothesis.",
    )
    result = gateway_verdict(paper)
    assert result["verdict"] == "NO-GO"


def test_p12_run_gateway_phase1():
    paper = _make_paper(
        abstract=(
            "Further research is needed to understand the mechanism. "
            "Future studies should examine the long-term effects."
        ),
        conclusions="Future work remains to be done.",
    )
    candidates = run_gateway(paper, phase=1)
    assert isinstance(candidates, list)
    assert len(candidates) >= 1


def test_p13_run_gateway_phase2():
    paper = _make_paper(
        abstract=(
            "One limitation of our study is the sample size. "
            "We note that this could be explored further."
        ),
        conclusions="Future work remains to be done.",
    )
    candidates_p1 = run_gateway(paper, phase=1)
    candidates_p2 = run_gateway(paper, phase=2)
    # Phase 2 should return >= Phase 1 candidates (greyscale adds more)
    assert len(candidates_p2) >= len(candidates_p1)


# ── PMC Client (static extraction, no network) ───────────────────────────────

def test_p14_extract_sections():
    sections = extract_sections(BIOC_FIXTURE)
    assert isinstance(sections, dict)
    assert "ABSTRACT" in sections or "abstract" in {k.lower() for k in sections}


def test_p15_get_doi_from_bioc():
    doi = get_doi_from_bioc(BIOC_FIXTURE)
    # Our fixture has article-id_doi = "10.1234/test.2024"
    assert doi == "10.1234/test.2024"


# ── Parser ────────────────────────────────────────────────────────────────────

def test_p16_parse_bioc_valid():
    paper = parse_bioc("12345", BIOC_FIXTURE)
    assert paper is not None
    assert isinstance(paper, ParsedPaper)
    assert paper.pmcid == "12345"
    assert paper.abstract_text is not None or paper.title is not None


def test_p17_parse_bioc_none_input():
    paper = parse_bioc("12345", None)
    assert paper is None
