"""
gitgap — Discipline enrichment service
Classifies gaps by source discipline and target crossing potential.

Primary: Gemini API (GOOGLE_API_KEY)
Fallback: rule-based keyword classifier (always available)
"""

import json
import os
import re
import httpx

_GEMINI_KEY = os.getenv("GOOGLE_API_KEY")
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

# ── Rule-based classifier ────────────────────────────────────────────────────

_SOURCE_RULES = {
    "computer_science": [
        "algorithm", "encoding", "indexing", "spatial index", "morton", "z-order",
        "z-curve", "hilbert", "bit-interleav", "hash", "database", "query",
        "data structure", "cache", "memory", "computational", "software",
        "machine learning", "neural network", "deep learning", "gpu", "parallel",
        "tree", "octree", "k-d tree", "rtree", "spatial data",
    ],
    "neuroscience": [
        "fmri", "mri", "voxel", "neuroimaging", "brain", "cortex", "neural",
        "eeg", "meg", "resting state", "activation", "connectivity", "tractography",
        "white matter", "grey matter", "bold", "hemodynamic",
    ],
    "psychology": [
        "cognitive", "behavior", "psychometric", "anxiety", "depression",
        "personality", "memory", "attention", "emotion", "mental health",
        "clinical", "therapeutic", "irt", "item response", "adaptive testing",
        "eye tracking", "scanpath", "reaction time", "stimulus",
    ],
    "criminal_justice": [
        "recidivism", "offender", "criminal", "crime", "sentencing", "parole",
        "compas", "static-99", "risk assessment", "justice", "incarceration",
        "police", "forensic", "prosecution", "evidence",
    ],
    "genomics_bioinformatics": [
        "genome", "dna", "rna", "sequence", "kmer", "k-mer", "protein",
        "phylogenetic", "snp", "variant", "gene", "transcriptome", "metagenome",
        "assembly", "alignment", "blast", "read mapping",
    ],
    "geospatial": [
        "gis", "geographic", "latitude", "longitude", "coordinate", "map",
        "terrain", "raster", "lidar", "elevation", "spatial analysis",
        "geospatial", "cartography", "remote sensing",
    ],
    "epidemiology": [
        "incidence", "prevalence", "cohort", "case-control", "odds ratio",
        "relative risk", "survival analysis", "mortality", "morbidity",
        "epidemiolog", "public health", "vaccination", "infection",
    ],
    "physics": [
        "quantum", "particle", "photon", "electron", "gravitational",
        "thermodynamic", "magnetic field", "electromagnetic", "optic",
        "spectroscop", "cosmolog",
    ],
}

# Which fields are most likely to benefit from each source discipline's techniques
_BRIDGE_TARGETS = {
    "computer_science": [
        "psychology", "criminal_justice", "epidemiology",
        "genomics_bioinformatics", "neuroscience",
    ],
    "neuroscience": ["psychology", "criminal_justice", "epidemiology"],
    "physics": ["neuroscience", "genomics_bioinformatics", "geospatial"],
    "genomics_bioinformatics": ["epidemiology", "psychology", "criminal_justice"],
    "geospatial": ["criminal_justice", "epidemiology", "psychology"],
    "epidemiology": ["criminal_justice", "psychology"],
    "psychology": ["criminal_justice", "epidemiology"],
    "criminal_justice": ["psychology", "epidemiology"],
}


# Technique-origin markers: when these appear, the TECHNIQUE comes from this discipline
# regardless of what domain the paper is about.
# These take priority over general domain keywords.
_TECHNIQUE_MARKERS = {
    "computer_science": [
        "morton encoding", "morton code", "z-order", "z-curve", "hilbert curve",
        "space-filling curve", "bit-interleav", "spatial index", "quadtree",
        "octree", "k-d tree", "r-tree", "hash function", "bloom filter",
        "locality-sensitive hash", "lsh", "topological data analysis", "tda",
        "persistent homology", "category theory", "graph neural", "transformer",
    ],
    "physics": [
        "quantum entanglement", "quantum circuit", "topological insulator",
        "renormalization group", "monte carlo simulation", "tensor network",
    ],
    "mathematics": [
        "fourier transform", "wavelet", "manifold learning", "differential geometry",
        "information geometry", "spectral theory",
    ],
}

# Application-domain keywords: where the technique is being APPLIED
_APPLICATION_DOMAINS = {
    "psychology":       ["cognitive", "psychometric", "anxiety", "depression", "personality",
                         "memory", "attention", "emotion", "irt", "adaptive testing",
                         "eye tracking", "reaction time", "behavioral"],
    "criminal_justice": ["recidivism", "offender", "criminal", "crime", "sentencing",
                         "compas", "static-99", "risk assessment", "parole", "forensic"],
    "neuroscience":     ["fmri", "mri", "voxel", "neuroimaging", "brain", "cortex",
                         "eeg", "bold", "connectivity"],
    "epidemiology":     ["incidence", "prevalence", "cohort", "odds ratio", "mortality",
                         "public health", "vaccination"],
    "genomics_bioinformatics": ["genome", "dna", "kmer", "sequence", "gene", "transcriptome"],
    "geospatial":       ["latitude", "longitude", "gis", "terrain", "raster", "lidar"],
}


def _classify_rule_based(declaration_text: str, gateway_term: str, source_title: str = "") -> dict:
    text = (declaration_text + " " + gateway_term + " " + (source_title or "")).lower()

    # Step 1: detect technique origin (overrides domain detection)
    technique_source = None
    for discipline, markers in _TECHNIQUE_MARKERS.items():
        if any(m in text for m in markers):
            technique_source = discipline
            break

    # Step 2: detect application domain
    app_scores = {}
    for domain, keywords in _APPLICATION_DOMAINS.items():
        hit = sum(1 for kw in keywords if kw in text)
        if hit:
            app_scores[domain] = hit

    # Step 3: determine source and targets
    if technique_source:
        # Structural hole case: CS/physics technique in an applied domain
        source = technique_source
        # Targets = detected application domains (these are the crossing destinations)
        targets = [d for d in sorted(app_scores, key=app_scores.get, reverse=True)
                   if d != technique_source]
        if not targets:
            targets = _BRIDGE_TARGETS.get(source, [])
        bridge = 0.80  # high: confirmed technique-in-domain structural hole
    else:
        # No technique marker — use general domain scoring
        general_scores = {}
        for discipline, keywords in _SOURCE_RULES.items():
            hit = sum(1 for kw in keywords if kw in text)
            if hit:
                general_scores[discipline] = hit
        source = max(general_scores, key=general_scores.get) if general_scores else "general"
        targets = _BRIDGE_TARGETS.get(source, [])
        technical = {"computer_science", "physics", "genomics_bioinformatics"}
        bridge = 0.65 if source in technical else 0.40

    return {
        "source_discipline": source,
        "target_disciplines": targets[:4],
        "bridge_potential": bridge,
        "bridge_rationale": (
            f"Technique originates in {source.replace('_', ' ')}; "
            f"functional analogues in {', '.join(t.replace('_', ' ') for t in targets[:2])} "
            f"literature are absent."
            if targets else
            f"Originates in {source.replace('_', ' ')}; cross-domain applicability unconfirmed."
        ),
        "method": "rule_based",
    }


# ── Gemini classifier ────────────────────────────────────────────────────────

_PROMPT = """\
You are a cross-disciplinary science analyst identifying structural holes in academic literature.

Given this research gap declaration:
Declaration: "{declaration}"
Gateway term: "{gateway}"
Source paper: "{title}"

Return ONLY valid JSON (no markdown, no explanation):
{{
  "source_discipline": "primary field of the source paper (snake_case, one of: computer_science, neuroscience, psychology, criminal_justice, genomics_bioinformatics, geospatial, epidemiology, physics, mathematics, general)",
  "target_disciplines": ["list", "of", "2-4", "fields", "where", "this", "technique", "could", "bridge"],
  "bridge_potential": 0.0,
  "bridge_rationale": "one sentence: why this is a structural hole"
}}

bridge_potential: 0.0 = no cross-domain value, 1.0 = highly transferable technique unknown in target fields."""


def _classify_gemini(declaration_text: str, gateway_term: str, source_title: str = "") -> dict | None:
    if not _GEMINI_KEY:
        return None
    prompt = _PROMPT.format(
        declaration=declaration_text[:500],
        gateway=gateway_term,
        title=(source_title or "")[:120],
    )
    try:
        resp = httpx.post(
            _GEMINI_URL,
            params={"key": _GEMINI_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=12,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json()
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        # Strip any accidental markdown fences
        text = re.sub(r"```(?:json)?|```", "", text).strip()
        result = json.loads(text)
        result["method"] = "gemini"
        return result
    except Exception:
        return None


# ── Public interface ─────────────────────────────────────────────────────────

def enrich_discipline(
    declaration_text: str,
    gateway_term: str,
    source_title: str = "",
) -> dict:
    """
    Returns discipline enrichment for a gap.
    Tries Gemini first; falls back to rule-based if unavailable or key expired.

    Returns dict with keys:
        source_discipline, target_disciplines (list), bridge_potential (float),
        bridge_rationale (str), method ('gemini' | 'rule_based')
    """
    result = _classify_gemini(declaration_text, gateway_term, source_title)
    if result is None:
        result = _classify_rule_based(declaration_text, gateway_term, source_title)
    return result
