# Corpus-Appreciated Phenomena (CAP)

**Definition:** A Corpus-Appreciated Phenomenon (CAP) is a reproducible empirical pattern for which the research corpus exhibits high consensus in observation and measurement, but low convergence in explanatory theory and formalized methodology.

CAP is the primary target class for gitgap gap detection.

> Not ignorance. Not disagreement. But unconsolidated agreement.

---

## The CAP Test

> "Could I write a theory paper from what the corpus already assumes, even though no one has written that theory paper yet?"

If yes: you have found a CAP.

---

## Gap Type Classification

| Type | What is missing |
|------|----------------|
| Unknown problem | The phenomenon itself |
| Research gap | Data |
| Theory gap | Explanation |
| **CAP** | Methodological + theoretical consolidation despite known, measured reality |

CAP is one layer deeper than a theory gap. The field knows the phenomenon is real and has operational tools to measure it. What is missing is doctrinal consolidation — a named, portable, reusable framework.

**Hubble tension** is the canonical CAP example: everyone agrees it is real, measurements are stable and reproducible, candidate mechanisms exist, but no consensus explanatory model or standardized methodological doctrine has been formalized.

---

## CAP Scoring Model

```
CAP = (EC + MS) + EE − (MF + TCR)
```

| Component | Symbol | What it measures |
|-----------|--------|-----------------|
| Existence Consensus | EC | Agreement across corpus that the phenomenon is real |
| Measurement Stability | MS | Low variance in how it is measured across papers |
| Explanatory Entropy | EE | Number and divergence of competing explanations |
| Methodological Formalization | MF | Presence of named, canonical frameworks |
| Temporal Convergence Rate | TCR | Whether explanatory convergence is increasing over time |

**High CAP score** → strong corpus-appreciated signal → gitgap GO candidate.

---

## Primary Detection Signal: Agreement–Formalization Divergence (AFD)

```
AFD = consensus(existence + measurement)
    − consensus(explanation + method)
```

High AFD means the corpus agrees the phenomenon exists and can measure it, but cannot agree on what it means or how to formally study it.

Supporting signals for high AFD:
- Measurement sections use certainty language: *robust, consistent, confirmed*
- Explanation sections use hedging language: *may, suggests, possible, remains unclear*
- Methods are operationally stable across papers with no named canonical protocol
- Repeated future-work calls for synthesis that are never resolved

---

## Corpus-Implied Theory Signals (Beyond Keyword Vectors)

Standard gap detection harvests keyword vectors from conclusions. CAP detection requires moving upstream and orthogonal.

### 1. Claim–Evidence Inversion Mapping
Extract all claims across sections. Map each to evidence strength and evidence type. High-claim / low-evidence regions are gap signals. Compute:
- Claim density vs. evidence density per section
- Repeated claims with non-independent evidence sources

### 2. Limitation Density Mapping
The same limitation appearing across multiple papers with no follow-up resolution is a high-yield gap signal.
- "Small sample size" → 400+ papers → no large-scale follow-up
- Persistent limitation over years = confirmed CAP signal

### 3. Method–Question Mismatch
Compare what the paper asks (introduction) vs. what it actually tests (methods). Structural mismatches are methodological blind spots:
- Requires longitudinal → uses cross-sectional
- Requires causal → uses correlational
- Requires real-world → uses simulation

### 4. Temporal Resolution Failure
Track explanation clusters over time. If the number of competing explanations stays constant or increases, the field senses the problem but is not converging:
```
d(convergence)/dt ≈ 0  OR  < 0
```

### 5. Citation Echo Chamber Detection
Build citation graph. High internal citation density + low external cross-domain referencing = epistemic isolation = hidden blind spot.

### 6. Negative Result Suppression Mapping
Research questions with many attempts, weak or null results, and no theoretical revision are suppressed-gap signals.

### 7. Concept Drift Without Resolution
Same term with shifting operational definitions across years, and no standardization attempt = semantic instability gap.

### 8. Unanswered Question Persistence
Extract future-work statements. Track whether the exact questions are ever resolved. Persistent unresolved calls for synthesis are a strong CAP signal.

---

## Section Asymmetry Rule

Corpus-appreciated phenomena hide in different sections than formal theories.

| Section | CAP signal type |
|---------|----------------|
| Introduction | Recurring problem framing without canonical citation |
| Methods | Operational assumptions quietly agreed on, never formalized |
| Discussion | Explanatory language stretching beyond local data |
| Limitations / Future Work | Admission that the model is not yet formalized |

**Gaps rarely live in abstracts.** Phase 1 gateway (explicit declarations) and Phase 2 gateway (implicit signals) both target sub-conclusion sections.

---

## Pre-Formal Theory vs. CAP

A pre-formal theory is one layer above a topic but below a CAP.

| Layer | Signal |
|-------|--------|
| Topic | People discuss the same subject |
| Emerging construct | People describe the same pattern or mechanism |
| Pre-formal theory | People repeatedly use a shared explanatory structure without canonical definition |
| **CAP** | Pre-formal theory + stable measurement consensus + no convergence over time |

CAP requires temporal persistence. An emerging construct that converges over 2–3 years is a normal science cycle. A CAP is a construct that *should* have converged but has not.

---

## Gap Age as Appreciation Amplifier

Gap age is not a deterrence signal. It is an appreciation signal.

A gap that has persisted unresolved for N years is evidence that:
- The field recognizes the problem (else it would not recur in literature)
- The field has not solved it (else the gap declarations would stop)
- The methodological or theoretical barrier is real

**The correct inference from gap age is: high-value target, not unsolvable.**

The failure mode is the opposite inference — "unsolved for 75 years → probably intractable → not worth attempting." This is survivorship bias applied to open problems. The gaps that *appear* intractable are the ones that have not yet been addressed from the right frame.

### Appreciation Duration Index (ADI)

```
ADI = years_since_first_articulation × persistence_factor
```

Where `persistence_factor` = rate at which the gap continues to appear in new literature (citations per year, last 5 years).

High ADI + High AFD = **prime CAP** — a phenomenon the corpus has measured for decades, cannot explain, and keeps encountering.

### Temporal Blindness Extended

eaiou's Temporal Blindness doctrine (submission dates hidden during review) extends to gap age:

> A reviewer evaluating a response to a 1957 gap should evaluate the response on its methodological merit, not on how long the gap has gone unaddressed.

The age of the source gap is displayed as provenance — it is never a scoring dimension. A paper that resolves a 70-year gap is not penalized for the age of the problem. It is appreciated for the duration of the opportunity that went unclaimed.

---

## Integration with NAUGHT → CAUGHT → FOUND

```
NAUGHT:  Unknown — phenomenon not yet observed in the corpus
CAUGHT:  CAP — phenomenon observed, measurement stable, explanation unconsolidated
FOUND:   Formalized — canonical framework, named methodology, portable number
```

gitgap detects the NAUGHT/CAUGHT boundary.
eaiou is the CAUGHT layer — it holds the record of what has been detected but not yet formalized.
A FOUND paper is a formal response to a CAUGHT record.

---

## Relation Vector > Keyword Vector

Pre-formal theories drift lexically. The entity-relation-condition triple is more stable than any single term.

Extract per paper:
- Entity (subject of the claim)
- Relation (what is claimed about it)
- Condition (boundary conditions on the claim)
- What the authors imply but do not define

Cluster semantically similar propositions. Detect recurring explanatory clusters. Score for recurrence, dispersion, and absence of canonical definition.

**"Where is theory X mentioned?"** → keyword vector → finds formal theories

**"What claims keep co-occurring?"** → relation vector → finds CAP

---

*Author: Eric D. Martin | ORCID 0009-0006-5944-1742*
