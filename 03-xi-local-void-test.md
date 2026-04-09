# XI. Appreciation Capture Test — Local Void (KBC Void)

**Hypothesis:** Earth resides in a ~1 Gpc underdensity (KBC Void) that creates a local outflow, artificially boosting H₀ measurements by ~2–5 km/s/Mpc.

**Status:** Perfect candidate. Qualitatively popular but lacks a single definitive portable number that all researchers use to correct their H₀ measurements. Current 2026 literature still debates whether the void is 70 Mpc or 2 billion light-years in extent.

---

## Gate Test

| Question | Answer Required to Pass | Status for Local Void |
|----------|------------------------|-----------------------|
| Is the underlying idea already qualitatively accepted? | Yes | YES. Recent 2026 literature shows broad acceptance of a matter-deficient region (~20% underdensity) as a possible contributor to the tension. |
| Does this paper provide the first quantitative calibration? | Yes | TARGET. Must provide the exact km/s/Mpc shift per 1% of density contrast — e.g., dH₀/dδ ≈ −0.25. |
| Is the number reusable by another researcher? | Yes | TARGET. If the slope allows a Tully-Fisher catalog researcher to correct their data using Pantheon+ numbers, it is portable. |

**Gateway: PENDING** — passes qualitative acceptance; requires quantitative calibration to complete.

---

## How to Formalize for Publication

The paper's centerpiece should **not** be "The Void exists." It should be the measured bound:

1. **The Exclusion:** "We rule out a Local Void as the *sole* source of the Hubble tension; it accounts for at most X.X km/s/Mpc of the gap."
2. **The Calibration:** "For every 100 Mpc of void radius, the inferred H₀ shifts by Y.Y units."
3. **The Reversal (NAUGHT Test):** Run the void-correction script on a Homogeneous Universe dataset (scrambled density). If the pipeline finds a void where there isn't one, the novelty uncertainty is too high.

---

## Why This Is Appreciated in 2026

By providing the first rigorous slope of how H₀ scales with void depth, the field moves from "maybe there's a bubble" to "here is the exact correction factor for that bubble."

That is the Appreciated Gateway pass: not the existence of the void, but the portable slope that quantifies its contribution.

---

## Connection to Eric D. Martin's Existing Work

The methodology is identical to Paper 3 (MN-26-0967-P):

| Paper 3 | Local Void Extension |
|---------|----------------------|
| Variable: Ωm | Variable: density contrast δ |
| Slope: dH₀/dΩm ≈ −11.6 | Slope: dH₀/dδ ≈ TBD |
| Bound: ΔH₀ = −0.218 ± 0.005 | Bound: TBD |
| Pipeline: Pantheon+ | Pipeline: Pantheon+ + density maps |

The ξ-coordinate diagnostic that removed H₀ to expose the Ωm sensitivity can be adapted to remove H₀ and expose the void density sensitivity. Same architecture, new variable.
