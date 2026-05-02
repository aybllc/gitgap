"""
gitgap — Document parsers
BioC JSON (primary) and JATS XML (fallback) → ParsedPaper for gateway filter.
Section types: TITLE, ABSTRACT, INTRO, METHODS, RESULTS, DISCUSS, CONCL
Author: Eric D. Martin | ORCID 0009-0006-5944-1742
"""

from dataclasses import dataclass
from typing import Optional
from lxml import etree
from .pmc import extract_sections, get_doi_from_bioc


@dataclass
class ParsedPaper:
    pmcid: str
    doi: Optional[str] = None
    title: Optional[str] = None
    journal: Optional[str] = None
    pub_year: Optional[int] = None
    abstract_text: Optional[str] = None
    methods_text: Optional[str] = None
    conclusions_text: Optional[str] = None


def parse_bioc(pmcid: str, bioc_doc: dict) -> Optional[ParsedPaper]:
    """
    Convert a BioC JSON document into a ParsedPaper.
    Returns None if the document is empty or malformed.
    """
    if not bioc_doc:
        return None

    sections = extract_sections(bioc_doc)

    if not sections:
        return None

    paper = ParsedPaper(pmcid=pmcid)

    # ── DOI ──────────────────────────────────────────────────────────────────
    paper.doi = get_doi_from_bioc(bioc_doc)

    # ── Title ─────────────────────────────────────────────────────────────────
    paper.title = sections.get("TITLE") or sections.get("FRONT")

    # ── Abstract ─────────────────────────────────────────────────────────────
    paper.abstract_text = sections.get("ABSTRACT")

    # ── Methods ──────────────────────────────────────────────────────────────
    paper.methods_text = sections.get("METHODS") or sections.get("METHOD")

    # ── Conclusions ──────────────────────────────────────────────────────────
    # CONCL is the primary target.
    # DISCUSS is the fallback — future work declarations often live here.
    paper.conclusions_text = (
        sections.get("CONCL") or
        sections.get("CONCLUSIONS") or
        sections.get("DISCUSS") or
        sections.get("DISCUSSION")
    )

    # ── Journal + year from BioC infons ──────────────────────────────────────
    documents = bioc_doc if isinstance(bioc_doc, list) else [bioc_doc]
    for doc in documents:
        infons = doc.get("infons", {})
        paper.journal = infons.get("journal") or infons.get("source")
        year_str = infons.get("year") or infons.get("pub_year")
        if year_str:
            try:
                paper.pub_year = int(str(year_str)[:4])
            except ValueError:
                pass
        break  # only need first document's metadata

    return paper


# ── JATS XML parser (E-utilities fallback) ────────────────────────────────────

def _jats_first(root, local_name: str):
    """Return first element with matching local name, namespace-agnostic."""
    for el in root.iter():
        if el.tag == local_name or el.tag.endswith(f"}}{local_name}"):
            return el
    return None


def _jats_all(root, local_name: str):
    """Return all elements with matching local name, namespace-agnostic."""
    return [el for el in root.iter()
            if el.tag == local_name or el.tag.endswith(f"}}{local_name}")]


def _text(el) -> str:
    """Collapse all text content of an element, strip whitespace."""
    return " ".join(el.itertext()).strip() if el is not None else ""


def parse_jats(pmcid: str, xml_text: str) -> Optional[ParsedPaper]:
    """
    Parse JATS XML (E-utilities efetch) into a ParsedPaper.
    Fallback path when BioC JSON is unavailable for a PMCID.
    Handles namespaced and plain JATS equally via local-name matching.
    """
    try:
        parser = etree.XMLParser(
            load_dtd=False, no_network=True, recover=True
        )
        root = etree.fromstring(xml_text.encode("utf-8"), parser=parser)
    except Exception as e:
        print(f"  parse_jats error for PMC{pmcid}: {e}")
        return None

    paper = ParsedPaper(pmcid=pmcid)

    # DOI
    for el in _jats_all(root, "article-id"):
        if el.get("pub-id-type") == "doi" and el.text:
            paper.doi = el.text.strip()
            break

    # Title
    paper.title = _text(_jats_first(root, "article-title")) or None

    # Journal
    paper.journal = _text(_jats_first(root, "journal-title")) or None

    # Year — first pub-date that has a year child
    for pub_date in _jats_all(root, "pub-date"):
        year_el = _jats_first(pub_date, "year")
        if year_el is not None and year_el.text:
            try:
                paper.pub_year = int(year_el.text.strip()[:4])
                break
            except ValueError:
                pass

    # Abstract — first abstract block
    abstract_el = _jats_first(root, "abstract")
    if abstract_el is not None:
        paper.abstract_text = _text(abstract_el) or None

    # Body sections — match by sec-type attribute or section title text
    methods_parts: list[str] = []
    conclusions_parts: list[str] = []
    discussion_parts: list[str] = []

    for sec in _jats_all(root, "sec"):
        sec_type = (sec.get("sec-type") or "").lower()
        title_el = _jats_first(sec, "title")
        sec_title = _text(title_el).lower() if title_el is not None else ""
        sec_text = _text(sec)

        is_methods = any(w in sec_type or w in sec_title
                         for w in ("method", "material"))
        is_concl   = any(w in sec_type or w in sec_title
                         for w in ("conclusion", "conclud", "summary"))
        is_discuss = any(w in sec_type or w in sec_title
                         for w in ("discussion", "discuss"))

        if is_methods and sec_text:
            methods_parts.append(sec_text)
        elif is_concl and sec_text:
            conclusions_parts.append(sec_text)
        elif is_discuss and sec_text:
            discussion_parts.append(sec_text)

    paper.methods_text = " ".join(methods_parts) or None
    # Prefer explicit conclusions; fall back to discussion
    paper.conclusions_text = (
        " ".join(conclusions_parts) or
        " ".join(discussion_parts) or
        None
    )

    return paper
