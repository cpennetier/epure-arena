# RESULTS

A factual index of what this repository establishes — one line per result,
each with its **honesty label**, its **scope inline**, and the command that
reproduces it from a clean clone. This points and labels; the narrative lives
in the Book, not here.

**Honesty labels.** **[PROVEN]** = backed by a test / byte-asserted golden /
benchmark in this repo. **[CHARACTERIZATION]** = a measured fact about a
*specific regime*, not a general law. **[FRAME]** = a research direction, not
yet built or proven.

**A note on the oracle.** The wait-pricing oracle (Gate P1a) is a *conservative
lower bound* on `v_realized` — it prices the convex wait cost by its chord
envelope (over-charge) over a strict subset of engine-feasible schedules. So
every **positive** regret it reports is a **guaranteed-real lower bound** on
the true gap: the true gap is at least this large, never smaller.

---

## Reproducible results

### 1. Certified planning is not slower than blind routing — [PROVEN]
The certified A\* planner runs **2.43× faster than this repo's own Dijkstra
baseline** at 1M flow units (throughput 2.50M events/s), and the gap widens
with scale. Scope: **absolute, single-machine** (Apple M2 Pro), backbone /
hourly / loose, seed 42 — a mechanistic statement about this engine, not a
cross-system claim.
- Reproduce: `make reproduce-benchmark` → golden `repro/expected/benchmark.json`
  (counts/digests byte-asserted; wall-times reported).

### 2. Estimation vs. decision are decoupled *here* — [CHARACTERIZATION]
In the currently-generated **budgeted set-selection** regime, estimator
accuracy does not move the committed decision: top-2 budgeted-ranking regret
is identically 0 for every estimator (incl. a non-value baseline), while
estimator MAE varies up to ~3.5×. Scope: this holds **because the regime is
over-determined** (~2 dominant interventions per suite) — explicitly *not*
"better prediction never helps decisions."
- Reproduce: `make reproduce-estimation` → golden
  `repro/expected/estimation_vs_decision.json`.

### 3. Wait-cost coherence across three planes — [PROVEN]
The cost of waiting is one quantity end to end: the plan-side reconstruction
(`plan_times`), the engine's realized `held_atu`, and the oracle's claimed
value agree **bit-exact** at exact pinned times (`claimed_per_unit ==
v_realized(...)`), and every execution's `ValueReport` closes with **zero
unexplained mass**. Scope: exact under Option-A pinned execution.
- Reproduce: `make test` (`tests/test_ilp_wait.py`, `tests/test_pricing.py`);
  re-asserted as `roundtrip_ok` in the P1 goldens (§5, §6).

### 4. The decision gap is real and grows with wait-cost steepness — [PROVEN]
Greedy-vs-oracle regret `R_raw(s) = V_oracle − V_greedy` is **monotone in
steepness** over `s ∈ [0, 0.75]` — `+2.53 → +4.52 → +9.88 → +24.82` — with
95% CIs that **exclude 0**, and a clean falsification control (non-competing
units show ≈0 |Δ|). Scope: **n=12, d=120, HiGHS-certified, controlled
bottleneck ρ=2.0, conservative oracle** (the certified oracle and a
natural-scale congested world are mutually exclusive at current solver
capability). The steep end (s=1.0, steep_hard) has wide CIs under full-system
semantics — the remainder divergence the admission model (§5) resolves.
- Reproduce: `make reproduce-decision-gap` → golden
  `repro/expected/decision_gap.json`.

### 5. The inflicted-wait externality EXISTS — [PROVEN] (existence; lower bound)
A wait-blind / own-wait greedy leaves a gap against the wait-aware oracle that
a **single-unit marginal cannot see** — the wait a commitment inflicts on
units committed *later*. Measured on the committed basis:
- **`R_wait_S = +9.63 [+5.4, +13.8]`** — domain-agnostic, `s = 0.75`.
- **`R_wait_S = +13.77 [+8.1, +19.5]`** — steep_hard, admission-enforced.

Both are **guaranteed-real lower bounds** (conservative oracle). Scope: **n=12,
d=120, HiGHS-certified, controlled bottleneck ρ=2.0, conservative oracle**; the
+13.77 leg additionally under the **admission model** (each policy plans
ex-ante, then only its committed set S runs; one shared demand filter, asserted
exact). Seed-42 oracle leg `253.41083981606636`, 106/106.
- Reproduce: `make reproduce-inflicted-wait` → golden
  `repro/expected/inflicted_wait.json`.

### 6. The own-wait / inflicted split — [CHARACTERIZATION]
Decomposing the gap into own-wait-recoverable (`R_raw − R_wait`, a heuristic
captures it) vs the inflicted residual (`R_wait`) is **noisy at n=5** and does
not license a sharp split ratio. The load-bearing claim is §5 (`R_wait > 0`);
the split is reported as measured, not as a result.
- Reproduce: `make reproduce-inflicted-wait` (the `own_wait_*` / `R_raw_*`
  fields of the same golden).

---

## Framed, not proven

### 7. Decision quality above optimization — [FRAME]
The thesis that *which* flow units to commit / refuse / when to re-solve is the
dominant lever (not the optimizer) is the program the apparatus is built to
make provable. Stated as a research frame, not a general law established here.

### 8. Closing the gap — [FRAME]
The path from "the gap exists" (§5) to "a decision policy closes it" is future
work, and deliberately narrow: **Level-1 set-selection** (which committed set),
**tractable-first**, with learning used for **amortization only** — *not* a
learned end-to-end policy, and *not* CBS / multi-agent routing. No learned
policy is claimed anywhere in this repo.
