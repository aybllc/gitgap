"""
gitgap — Gateway filter
Phase 1: explicit future research declarations (binary GO/NO-GO)
Phase 2: greyscale implicit signals

The Appreciated Gateway must be evaluated by a human Keeper.
This filter flags candidates. It does not pass the gate.
"""

import re
from dataclasses import dataclass
from typing import Optional
from .parser import ParsedPaper


# ── Phase 1: Explicit declarations (black — fully confirmed signal) ───────────

PHASE_1_TERMS = [
    "future work",
    "further research",
    "future research",
    "future studies",
    "future investigation",
    "remains an open question",
    "open question",
    "left for future",
    "beyond the scope",
    "we did not address",
    "warrants further",
    "requires further",
    "needs further",
    "deserves further",
    "future directions",
    "further work",
    "future experiments",
    "to be investigated",
    "remains to be determined",
    "remains unclear",
    "not yet understood",
    "still unknown",
]

# ── Phase 2: Greyscale implicit signals ───────────────────────────────────────

PHASE_2_TERMS = [
    ("we note that",           0.50),
    ("interestingly",          0.40),
    ("this suggests",          0.55),
    ("one limitation",         0.65),
    ("we did not account",     0.70),
    ("it is possible that",    0.45),
    ("could be explored",      0.60),
    ("might be due to",        0.45),
    ("may warrant",            0.65),
    ("an interesting avenue",  0.70),
    ("we leave this",          0.75),
    ("not explored here",      0.75),
    ("assumed throughout",     0.60),
]


@dataclass
class GapCandidate:
    paper_pmcid: str
    declaration_text: str       # sentence(s) containing the trigger
    gateway_term: str           # which term triggered
    phase: int                  # 1 or 2
    confidence: float           # 1.0 → black, 0.25 → light grey
    section_source: str         # conclusions / discussion / abstract
    keeper_required: bool = True  # always True — human must validate


def _extract_sentences(text: str, term: str, window: int = 2) -> str:
    """
    Extract sentences containing the trigger term, plus surrounding context.
    window = number of sentences before and after to include.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    for i, sentence in enumerate(sentences):
        if term.lower() in sentence.lower():
            start = max(0, i - window)
            end = min(len(sentences), i + window + 1)
            result.append(" ".join(sentences[start:end]))
    return " [...] ".join(result) if result else ""


def run_gateway(paper: ParsedPaper, phase: int = 1) -> list[GapCandidate]:
    """
    Run the gateway filter on a parsed paper.
    Returns list of GapCandidates (empty = NO-GO for that phase).

    phase=1: explicit declarations only
    phase=2: explicit + greyscale
    """
    candidates = []

    # Which text sections to scan
    sections = {
        "conclusions": paper.conclusions_text or "",
        "abstract": paper.abstract_text or "",
    }

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    for section_name, text in sections.items():
        if not text:
            continue
        text_lower = text.lower()
        for term in PHASE_1_TERMS:
            if term in text_lower:
                declaration = _extract_sentences(text, term)
                if declaration:
                    candidates.append(GapCandidate(
                        paper_pmcid=paper.pmcid,
                        declaration_text=declaration,
                        gateway_term=term,
                        phase=1,
                        confidence=1.0,
                        section_source=section_name,
                    ))

    if phase < 2:
        return _deduplicate(candidates)

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    # Only run on conclusions — abstract greyscale is too noisy
    conclusions = paper.conclusions_text or ""
    if conclusions:
        conclusions_lower = conclusions.lower()
        for term, confidence in PHASE_2_TERMS:
            if term in conclusions_lower:
                declaration = _extract_sentences(conclusions, term)
                if declaration:
                    candidates.append(GapCandidate(
                        paper_pmcid=paper.pmcid,
                        declaration_text=declaration,
                        gateway_term=term,
                        phase=2,
                        confidence=confidence,
                        section_source="conclusions",
                    ))

    return _deduplicate(candidates)


def _deduplicate(candidates: list[GapCandidate]) -> list[GapCandidate]:
    """Remove duplicate declarations from the same section/term."""
    seen = set()
    unique = []
    for c in candidates:
        key = (c.paper_pmcid, c.gateway_term, c.section_source)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def gateway_verdict(paper: ParsedPaper) -> dict:
    """
    Quick GO/NO-GO verdict for a paper.
    Returns summary dict for logging.
    """
    phase1 = run_gateway(paper, phase=1)
    return {
        "pmcid": paper.pmcid,
        "verdict": "GO" if phase1 else "NO-GO",
        "phase1_count": len(phase1),
        "candidates": phase1,
    }
