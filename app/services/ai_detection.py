"""
gitgap — AI content detection service

Doctrine:
  Non-declared = treated as AI-free.
  All papers ingested into gitgap are interrogated.
  ai_flag = 1 when: ai_score >= FLAG_THRESHOLD AND ai_declared is None.

Two-pass architecture:
  Pass 1 — Heuristic (always runs, ~10ms, no API):
    · Disclosure scan: explicit AI tool names → ai_declared='yes', skip flagging
    · Sentence burstiness: low variance in length = AI signal
    · Hedge/filler phrase density: over-hedging = AI signal
    · AI signature vocabulary: statistically AI-heavy phrases
    · Generic transition density: structured-list over-use = AI signal

  Pass 2 — LLM escalation (only when heuristic score ≥ ESCALATE_THRESHOLD):
    · Tries ANTHROPIC_API_KEY first, then OPENAI_API_KEY
    · LLM-as-judge: structured JSON response with score + signals
    · Falls back gracefully — heuristic result stands if no key configured
    · method field records which passes ran
"""

import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

# ── Thresholds ────────────────────────────────────────────────────────────────

ESCALATE_THRESHOLD = 0.40   # heuristic score above this → run LLM if available
FLAG_THRESHOLD     = 0.62   # final score above this + no declaration → ai_flag=1

# ── Disclosure keyword scan (low false-positive) ──────────────────────────────
# Specific AI tool names and explicit disclosure phrases.
# Generic terms like "language model" omitted — too common in research content.

_DISCLOSURE_KEYWORDS = [
    "chatgpt", "gpt-4", "gpt-3.5", "gpt4", "gpt-3", "gpt 4",
    "openai's model", "claude ai", "claude 3", "anthropic's",
    "llama 2", "llama 3", "gemini 1.5", "gemini pro", "gemini ultra",
    "ai-assisted writing", "ai-assisted manuscript", "ai-assisted drafting",
    "written with ai", "written using ai", "with the help of ai",
    "generated with ai", "ai was used to write", "ai tools were used to write",
    "language model was used to", "large language model was used to",
    "we used ai to", "copilot was used",
]

# ── Hedge phrase list ─────────────────────────────────────────────────────────

_HEDGE_PHRASES = [
    "it is important to note", "it should be noted", "it is worth noting",
    "it is worth mentioning", "it is crucial to", "it is essential to",
    "one must consider", "we must acknowledge", "we must note",
    "in this regard", "in light of this", "with that in mind",
    "taking this into account", "it is evident that", "it is clear that",
    "undoubtedly", "without a doubt", "needless to say",
    "it is no secret that", "it goes without saying",
]

# ── AI signature vocabulary ───────────────────────────────────────────────────
# Phrases statistically over-represented in AI-generated academic text.

_AI_SIGNATURE_PHRASES = [
    "delve into", "delve deeper", "delve further",
    "underscore the importance", "underscore the need",
    "in the realm of", "in the landscape of",
    "as we move forward", "going forward, it",
    "as mentioned above", "as noted above", "as discussed above",
    "as previously mentioned", "as outlined above",
    "plays a crucial role", "plays a pivotal role", "plays a vital role",
    "shed light on", "shed new light on",
    "a wide range of", "a broad range of", "a diverse range of",
    "paradigm shift", "holistic approach", "holistic understanding",
    "significant implications", "crucial aspect", "pivotal aspect",
    "nuanced understanding", "nuanced approach",
    "tapestry", "multifaceted", "multifaceted nature",
]

# ── Generic transition phrases ────────────────────────────────────────────────

_GENERIC_TRANSITIONS = [
    "furthermore,", "moreover,", "additionally,",
    "in conclusion,", "in summary,", "to summarize,",
    "firstly,", "secondly,", "thirdly,",
    "in addition,", "on the other hand,",
    "it is noteworthy that", "notably,",
    "importantly,",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_sentences(text: str) -> list[str]:
    """Rough sentence splitter — good enough for burstiness analysis."""
    parts = re.split(r'(?<=[.?!])\s+(?=[A-Z])', text)
    return [p.strip() for p in parts if len(p.strip()) > 10]


def _scan_disclosure(text: str) -> bool:
    """
    Returns True if the text explicitly discloses AI usage.
    Uses specific tool names / phrases — low false-positive rate.
    """
    lower = text.lower()
    return any(kw in lower for kw in _DISCLOSURE_KEYWORDS)


def _heuristic(text: str) -> tuple[float, list[str]]:
    """
    Compute heuristic AI score and signal list from text.
    Returns (score 0.0–1.0, signals []).
    """
    if not text or len(text.strip()) < 100:
        return 0.0, []

    lower = text.lower()
    words = lower.split()
    word_count = max(len(words), 1)
    signals: list[str] = []
    sub_scores: list[float] = []

    # ── Signal 1: Sentence length burstiness ─────────────────────────────────
    sentences = _split_sentences(text)
    if len(sentences) >= 6:
        lengths = [len(s.split()) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        if mean_len > 0:
            variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
            cv = math.sqrt(variance) / mean_len
            # Academic human writing: CV ≈ 0.45–0.85
            # AI writing: CV ≈ 0.15–0.35 (unnaturally uniform)
            burstiness_score = max(0.0, 1.0 - (cv / 0.40))
            burstiness_score = round(min(1.0, burstiness_score), 3)
            if burstiness_score > 0.30:
                signals.append("uniform_sentence_length")
                sub_scores.append(burstiness_score)

    # ── Signal 2: Hedge phrase density ───────────────────────────────────────
    hedge_count = sum(lower.count(p) for p in _HEDGE_PHRASES)
    hedge_per_100 = hedge_count / (word_count / 100)
    hedge_score = round(min(1.0, hedge_per_100 / 3.0), 3)
    if hedge_score > 0.25:
        signals.append("high_hedge_density")
        sub_scores.append(hedge_score)

    # ── Signal 3: AI signature vocabulary ────────────────────────────────────
    sig_count = sum(lower.count(p) for p in _AI_SIGNATURE_PHRASES)
    sig_score = round(min(1.0, sig_count / 6.0), 3)
    if sig_score > 0.15:
        signals.append("ai_signature_phrases")
        sub_scores.append(sig_score)

    # ── Signal 4: Generic transition density ─────────────────────────────────
    trans_count = sum(lower.count(p) for p in _GENERIC_TRANSITIONS)
    trans_per_100 = trans_count / (word_count / 100)
    trans_score = round(min(1.0, trans_per_100 / 2.5), 3)
    if trans_score > 0.25:
        signals.append("generic_transitions")
        sub_scores.append(trans_score)

    if not sub_scores:
        return 0.0, []

    # Weighted composite: burstiness is most reliable, weight it higher
    weights = [1.5 if s == "uniform_sentence_length" else 1.0 for s in signals]
    composite = sum(sc * w for sc, w in zip(sub_scores, weights)) / sum(weights)
    return round(min(1.0, composite), 3), signals


def _llm_judge(text: str) -> Optional[tuple[float, list[str]]]:
    """
    Optional LLM escalation.
    Tries Anthropic first, then OpenAI.
    Returns (score, signals) or None if no key is configured.
    Non-fatal — any exception returns None and heuristic result is used.
    """
    prompt_text = text[:4000]  # cap to avoid excessive token spend

    system = (
        "You are an AI content detection expert analyzing academic paper text. "
        "Assess whether the text was likely AI-generated (not AI-assisted research, but AI-written prose). "
        "Consider: uniform sentence length, over-hedged language, AI signature vocabulary "
        "(delve, underscore, pivotal, nuanced, realm of, as mentioned above), "
        "generic transitions, lack of specific empirical detail, unnaturally structured flow. "
        "Academic human writing is varied, specific, occasionally awkward. "
        "AI academic writing is smooth, hedged, structurally predictable. "
        "Reply ONLY with JSON: {\"score\": 0.0-1.0, \"signals\": [\"signal1\", ...]}"
    )

    # Try Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=anthropic_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                system=system,
                messages=[{"role": "user", "content": prompt_text}],
            )
            raw = msg.content[0].text.strip()
            data = json.loads(raw)
            return float(data["score"]), list(data.get("signals", []))
        except Exception:
            pass

    # Try OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai as _openai
            client = _openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=200,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt_text},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            data = json.loads(raw)
            return float(data["score"]), list(data.get("signals", []))
        except Exception:
            pass

    return None


# ── Public interface ──────────────────────────────────────────────────────────

def interrogate(abstract_text: str, full_text: str = "") -> dict:
    """
    Full two-pass AI detection for a paper.

    Returns:
    {
      "ai_declared":          "yes" | None,
      "ai_detection_score":   0.0–1.0,
      "ai_detection_signals": [...],
      "ai_flag":              0 | 1,
      "method":               "heuristic" | "heuristic+llm",
    }
    """
    combined = " ".join(filter(None, [abstract_text, full_text]))

    # Pass 0: Disclosure scan — if declared, record and exit (no flagging)
    if _scan_disclosure(combined):
        return {
            "ai_declared":          "yes",
            "ai_detection_score":   None,
            "ai_detection_signals": [],
            "ai_flag":              0,
            "method":               "disclosure_scan",
        }

    # Pass 1: Heuristic
    score, signals = _heuristic(combined)
    method = "heuristic"

    # Pass 2: LLM escalation if above threshold
    if score >= ESCALATE_THRESHOLD:
        llm_result = _llm_judge(combined)
        if llm_result is not None:
            llm_score, llm_signals = llm_result
            # Take the higher of the two scores — LLM has broader signal coverage
            if llm_score > score:
                score = round(llm_score, 3)
                signals = list(set(signals + llm_signals))
            method = "heuristic+llm"

    ai_flag = 1 if score >= FLAG_THRESHOLD else 0

    return {
        "ai_declared":          None,
        "ai_detection_score":   score,
        "ai_detection_signals": signals,
        "ai_flag":              ai_flag,
        "method":               method,
    }


def run_on_paper(paper_id: int, abstract_text: str, conclusions_text: str, db) -> dict:
    """
    Run detection and persist results to the papers table.
    Called from the ingest pipeline after each new paper INSERT.
    Non-fatal — exceptions are caught and logged; paper record is not rolled back.
    """
    try:
        result = interrogate(abstract_text or "", conclusions_text or "")
        db.execute(text("""
            UPDATE papers SET
                ai_declared          = :declared,
                ai_detection_score   = :score,
                ai_detection_signals = :signals,
                ai_flag              = :flag,
                ai_interrogated_at   = :ts
            WHERE id = :id
        """), {
            "declared": result["ai_declared"],
            "score":    result["ai_detection_score"],
            "signals":  json.dumps(result["ai_detection_signals"]),
            "flag":     result["ai_flag"],
            "ts":       _now(),
            "id":       paper_id,
        })
        db.commit()
        return result
    except Exception as e:
        # Non-fatal — log to stderr, do not raise
        print(f"[ai_detection] paper_id={paper_id} failed: {e}", flush=True)
        return {}
