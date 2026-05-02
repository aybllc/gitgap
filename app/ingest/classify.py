"""
gitgap — Gap classification

Classifies each gap into a research gap type at ingest.
Rule-based — no model needed. Classification is a soft label, not a gate.

Gap classes:
  methodology   — limitations of the method/approach used
  scope         — paper deliberately excluded something; future scope
  empirical     — more data, experiments, or observations required
  theoretical   — mechanism or theoretical framework is missing/incomplete
  replication   — needs independent validation or reproduction
  general       — fallback; generic future-work declaration

The class improves Wheelhouse matching in eaiou: a computational author
should receive methodology gaps, not replication gaps.
"""

import re
from typing import Optional

# ── Keyword rule sets ─────────────────────────────────────────────────────────

_RULES = {
    "replication": [
        "replicate", "reproduce", "independent validation",
        "independent confirmation", "verify", "verification",
        "corroborate", "further confirmation", "cross-validation",
    ],
    "methodology": [
        "method", "approach", "technique", "algorithm", "framework",
        "model", "procedure", "implementation", "tool", "analysis",
        "computational", "numerical", "simulation", "software",
    ],
    "scope": [
        "beyond the scope", "not explored here", "not addressed",
        "did not address", "did not account", "we leave this",
        "left for future", "restricted to", "limited to",
        "we focused on", "out of scope", "assumed throughout",
    ],
    "empirical": [
        "experiment", "experimental", "observational", "measurement",
        "sample size", "clinical trial", "cohort", "dataset", "survey",
        "longitudinal", "case study", "field study",
    ],
    "theoretical": [
        "theoretical", "mechanism", "fundamental", "first principles",
        "mathematical", "formalism", "theory", "analytical", "derivation",
        "explanation", "underlying", "physical interpretation",
        "conceptual framework",
    ],
}


def classify_gap(declaration_text: str, gateway_term: str) -> str:
    """
    Classify a gap declaration into a research gap type.
    Checks rules in priority order — first match wins.
    Falls back to 'general'.
    """
    text = (declaration_text or "").lower()
    term = (gateway_term or "").lower()

    # Priority order: most specific first
    for cls in ("replication", "scope", "methodology", "empirical", "theoretical"):
        keywords = _RULES[cls]
        if any(kw in text or kw in term for kw in keywords):
            return cls

    return "general"
