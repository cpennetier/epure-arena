# epure-arena — Project Overview

> **Audience.** This document is written for two readers. (a) A *Book*
> assistant that will read this file **and every file it links** to build the
> paper / PDF — so every claim points at the artifact that backs it.
> (b) External LLMs used for **literature retrieval** — so §4 states precisely
> which open problems to aim references at. It is self-contained: read it top to
> bottom and you know what epure-arena *is*, what is *proven*, and what is
> *envisioned but not yet proven*.
>
> **Honesty contract.** Three labels are used throughout and are load-bearing:
> **[PROVEN]** = backed by a test, golden, or benchmark in this repo;
> **[CHARACTERIZATION]** = a measured fact about a *specific regime*, not a
> general law; **[FRAME / OPEN]** = a research direction, not yet built or
> proven. A lit-review reader should aim references at the **[FRAME / OPEN]**
> items in §3–§4.

Canonical companions, all crawlable from here:
[`../README.md`](../README.md) (orientation),
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) (the directory-level map),
[`../RESULTS.md`](../RESULTS.md) (results index, with honesty labels + scope),
[`./benchmarks.md`](./benchmarks.md) (performance),
[`./protocol.md`](./protocol.md) (the wire format).

---

## 1. What it is

**epure-arena is a certified environment for decision quality under hard
constraints.** A *world* is a **capacitated time-graph**: discrete *flow units*
traverse *nodes* over *lanes* by boarding *connections* — scheduled,
capacity-limited departures — each flow unit carrying an appearance time, a
deadline (`due_atu`), a size, and a priority class. The vocabulary is
deliberately **domain-neutral** (flow units / nodes / lanes / connections, never
the vocabulary of any one application domain); neutrality is enforced by
[`../tests/test_no_domain_vocabulary.py`](../tests/test_no_domain_vocabulary.py),
and anything that is a capacitated time-graph — supply networks, compute-job
placement, patient flow, energy dispatch, rolling-stock rotation — maps onto it
through the wire protocol ([`./protocol.md`](./protocol.md)).

Over such a world epure-arena provides an *apparatus*, and the apparatus **is**
the artifact: not a router, but the machinery that says **what happened, whether
it was avoidable, whose fault it was, and what a decision was worth** — each
backed by a proof or an exact replay rather than an estimate.

### 1.1 The three layers

One engine, three responsibility layers (full directory tagging in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md)):

| Layer | Question it answers | Where it lives |
|---|---|---|
| **DES world** | *What physically happened?* A capacitated time-graph replayed exactly. | Rust: [`crates/ephemeris-des`](https://github.com/cpennetier/ephemeris-kernel/blob/v0.1.0/crates/ephemeris-des); Python world-gen: [`../python/epure_arena/scenarios/`](../python/epure_arena/scenarios/), [`../python/epure_arena/world/`](../python/epure_arena/world/) |
| **Substrate / certifier** | *Was this outcome avoidable? Whose fault — world or policy?* Typed reasons backed by proofs. | Rust: [`crates/ephemeris-ledger`](https://github.com/cpennetier/ephemeris-kernel/blob/v0.1.0/crates/ephemeris-ledger), [`crates/ephemeris-fsm`](https://github.com/cpennetier/ephemeris-kernel/blob/v0.1.0/crates/ephemeris-fsm); Python: [`../python/epure_arena/harness/`](../python/epure_arena/harness/), [`../python/epure_arena/lattice/`](../python/epure_arena/lattice/), [`../python/epure_arena/reasons.py`](../python/epure_arena/reasons.py), [`../python/epure_arena/optimize/fidelity.py`](../python/epure_arena/optimize/fidelity.py) |
| **Agents (seams)** | *What should we commit, and what was it worth?* Pluggable policies graded by the certifier. | [`../python/epure_arena/{gate,propose,imagine,navigate,optimize}/`](../python/epure_arena/), [`../python/epure_arena/env/`](../python/epure_arena/env/) |

The dependency arrow is one-way — **core never imports a downstream adapter**
([`../tests/test_dependency_direction.py`](../tests/test_dependency_direction.py)) —
so a domain application *extends* the apparatus without modifying it.

### 1.2 The apparatus, and where each piece lives

The substrate is **Rust execution + Python certification**, and the split is a
deliberate design decision, not an accident of history:

- **Rust is the mechanism — the physics that must be exact, fast, and
  byte-deterministic.** The discrete-event engine
  ([`crates/ephemeris-des/src/engine.rs`](https://github.com/cpennetier/ephemeris-kernel/blob/v0.1.0/crates/ephemeris-des/src/engine.rs))
  processes events in strict time order (Inject → Arrive → Depart → Deliver),
  with capacity-blocked flow units entering per-lane overflow queues ordered by
  `(priority_class, enqueued_atu, flow_unit_id)` — a **total order**, so the run
  is reproducible to the byte. All time is `i64` ATU (milliseconds); costs are
  integer microdollars; capacities are `u32`. The **capacity ledger**
  ([ephemeris-kernel](https://github.com/cpennetier/ephemeris-kernel)
  `crates/ephemeris-ledger`) is per-(lane, slot) residual accounting with
  typed reservations and commitment levels; the **flow-unit FSM**
  (`crates/ephemeris-fsm`, same repo) governs lifecycle transitions. The
  engine compiles (via PyO3 / maturin) in ephemeris-kernel and is re-exported
  here as `epure_arena._engine`. Rust is chosen here because the substrate
  has to be *trusted*: exact integer arithmetic, deterministic event ordering,
  and replay are correctness properties, and a compiled, total-ordered kernel is
  the cheapest honest way to guarantee them at 10⁶-flow-unit scale (see
  [`./benchmarks.md`](./benchmarks.md)).

- **Python is the judgment — the proofs *about* what the mechanism did.**
  Judgment changes more often than physics and benefits from expressiveness, so
  it lives in Python *above* the trusted kernel:
  - **The feasibility oracle**
    ([`../python/epure_arena/harness/feasibility_oracle.py`](../python/epure_arena/harness/feasibility_oracle.py),
    mirroring the Rust
    [`crates/ephemeris-des/src/feasibility_oracle.rs`](https://github.com/cpennetier/ephemeris-kernel/blob/v0.1.0/crates/ephemeris-des/src/feasibility_oracle.rs)):
    a **capacity-free, destination-rooted backward** computation of the earliest
    on-time arrival. It classifies every non-ideal outcome as
    `FEASIBLE_ON_TIME` (a path existed — policy's fault), `FEASIBLE_LATE`, or
    `INFEASIBLE` (no path at any release — world's fault, sub-split CLIFF vs
    STRUCTURAL). "The network was full" becomes a *theorem about the timetable*.
  - **The typed reason taxonomy**
    ([`../python/epure_arena/reasons.py`](../python/epure_arena/reasons.py)):
    every bad outcome maps to exactly one `ReasonCode` in one of three classes —
    **R1 world** (mechanically impossible), **R2 envelope** (outside the design
    envelope), **R3 algorithm** (a feasible/better action was missed). The
    attribution table is a `GROUP BY` over reason codes with **zero unexplained
    mass** (every flow unit maps or the call raises).
  - **The certificate lattice**
    ([`../python/epure_arena/lattice/`](../python/epure_arena/lattice/)): upper
    bounds on on-time deliverable mass — `oracle ⊇ cohort-LP ⊇ achieved` by
    construction. The cohort-flow LP
    ([`../python/epure_arena/lattice/cohort_flow.py`](../python/epure_arena/lattice/cohort_flow.py))
    is **flow-unit-count-invariant** (its size depends on the world, not the
    demand: 40k and 1M flow units build the same LP, only the right-hand side
    changes), which is what lets attribution scale.
  - **The certified planner**
    ([`../python/epure_arena/optimize/capacity_aware.py`](../python/epure_arena/optimize/capacity_aware.py)):
    **time-dependent A\*** over per-(lane, slot) residual capacity, guided by the
    capacity-free oracle as an **admissible lower bound**. It commits a flow unit
    only when a capacity-feasible on-time completion exists end-to-end (the
    "certify" step). The Dijkstra baseline
    ([`../python/epure_arena/optimize/static.py`](../python/epure_arena/optimize/static.py))
    is the capacity-blind control.
  - **Plan fidelity & the three-way split**
    ([`../python/epure_arena/optimize/fidelity.py`](../python/epure_arena/optimize/fidelity.py)):
    after pinning a plan and executing it, the realized run is decomposed into
    **GUARANTEED** (committed, realizes exactly under slot-pinning),
    **OPPORTUNISTIC** (uncommitted deliveries, split on-time/late), and
    **REFUSED-TYPED** (each strand carries its planner reason). The
    *refusal-certificate theorem* (a certified `CAPACITY_BLOCKED` refusal is
    unrecoverable ⇒ opportunistic-on-time ≡ 0) is **verified empirically per
    run** — a positive count is a certification-bug detector.
  - **The counterfactual harness**
    ([`../python/epure_arena/harness/live_loop.py`](../python/epure_arena/harness/live_loop.py)):
    the thin World→Substrate→{Gate,Propose,Imagine,Navigate,Optimize}→World loop
    on synthetic disruptions, with **paired same-seed rollouts** (act vs. no-op)
    and **mass closure** (Σ typed flows == total demand) asserted, producing a
    byte-identity digest.
  - **Commitment / replay**: reservations are released as *physics* on
    disruption (`cancel_pinned_routes`); plans are pinned via
    `set_forced_routes_pinned`; the run manifest embeds the protocol fingerprint
    ([`./protocol.md`](./protocol.md)) so a run under a different protocol is
    mechanically distinguishable.
  - **Wait-cost pricing** (Gate 0,
    [`../python/epure_arena/pricing.py`](../python/epure_arena/pricing.py)): the
    engine records `held_atu` — deterministic held-but-not-in-transit time per
    flow unit — and a value functional prices it as
    `v_realized = v − C_wait(held) − late_loss`, with convex
    `C_wait = v·(a·w + b·w²)`; the `b>0` cross-curvature makes `V(S)`
    non-additive through shared slots. Regimes are anchors named by *shape* —
    `STEEP_SCARCE`, `SLACK_ELASTIC`, `DEMO_ZERO` — never by application domain.
    A **conservative wait-pricing ILP oracle**
    ([`../python/epure_arena/lattice/ilp_wait.py`](../python/epure_arena/lattice/ilp_wait.py))
    lower-bounds `v_realized`, certified under the HiGHS backend (CBC is refused
    on the hard-tolerance big-M class where its presolve returns false
    infeasibility).

**Why Rust-execution + Python-certification.** The mechanism must be *trusted
and fast*; the judgment must be *expressive and frequently revised*. Putting the
deterministic physics in a compiled, total-ordered kernel and the proofs in
Python keeps the trusted base small and the certifier easy to extend — and
because the kernel replays to the byte, the Python proofs are proofs *about an
exact object*, not about a noisy sample.

---

## 2. What's proven now

### 2.1 The environment works  [PROVEN]

- **Exact replay.** Same seed ⇒ byte-identical run (KPIs, records, surfaces,
  manifest). Verified by the golden worlds
  ([`../tests/golden_worlds/`](../tests/golden_worlds/)) and, at scale, by
  `replay_identical = True` at every point in [`./benchmarks.md`](./benchmarks.md)
  (Table 4).
- **Typed + *proven* attribution.** Every non-ideal outcome is classified
  world's-fault vs policy's-fault by the destination-rooted oracle and the
  R1/R2/R3 taxonomy, with zero unexplained mass. Tests:
  [`../tests/test_feasibility_oracle.py`](../tests/test_feasibility_oracle.py),
  [`../tests/test_reasons.py`](../tests/test_reasons.py),
  [`../tests/test_three_way_split.py`](../tests/test_three_way_split.py),
  [`../tests/test_plan_fidelity.py`](../tests/test_plan_fidelity.py).
- **Exact counterfactual reward.** Because the world replays deterministically,
  a paired same-seed rollout (act vs. no-op) yields the *true* marginal value of
  an action — the reward is **certified by construction, not estimated**. The
  RL-facing surface is [`BudgetedRecourseEnv`](../python/epure_arena/env/recourse.py):
  `reward = realized marginal Δ − cost`, and cumulative reward **telescopes
  exactly** to `V(selected_set)` (checked in
  [`../tests/test_env_recourse.py`](../tests/test_env_recourse.py)).
- **Regret against a certified oracle.** Any policy is graded by **budgeted
  regret** `V(S*) − V(S_policy)` against the (cost, budget)-constrained ceiling
  ([`Oracle`](../python/epure_arena/env/grader.py),
  [`../python/epure_arena/env/world.py`](../python/epure_arena/env/world.py);
  demo [`../examples/random_vs_greedy_vs_oracle.py`](../examples/random_vs_greedy_vs_oracle.py)).

### 2.2 The performance is measured  [PROVEN]

Full tables and method in [`./benchmarks.md`](./benchmarks.md) (reproduce:
`make reproduce-benchmark` → [`../repro/benchmark.py`](../repro/benchmark.py);
deterministic counts byte-asserted in
[`../repro/expected/benchmark.json`](../repro/expected/benchmark.json), wall-times
reported in [`../data/benchmark.json`](../data/benchmark.json)). Headlines, on
one scenario family (backbone/hourly/loose, seed 42, Apple M2 Pro) — **absolute,
single-machine, not a cross-system claim**:

- **Throughput:** the DES world replays at ~1.1M→2.9M events/s (a 1M-flow-unit
  world in ~3 s).
- **Certify cost is mechanistic:** the certified planner runs **2.3× faster than
  epure-arena's own Dijkstra baseline** at 1M and the gap *widens* with scale —
  because the capacity-free oracle that makes the plan *certified* is the same
  admissible heuristic that makes the A\* search tiny. Decomposed, certify is
  **A\* ~58% / oracle <1% (constant, demand-independent) / attribution ~3%** at
  1M. The proof scales.

### 2.3 The estimation finding is a characterization, not a law  [CHARACTERIZATION]

`make reproduce-estimation`
([`../repro/estimation_vs_decision.py`](../repro/estimation_vs_decision.py))
shows estimator accuracy and decision quality **decoupled** in the currently
generated regime — but **only because that regime is over-determined**: realized
per-epoch value is dominated by ~2 interventions, so any top-k ranking reaches
the optimum and estimator MAE cannot move the committed set. This is a *measured
fact about an easy regime*, explicitly **not** "better prediction never helps
decisions." It is a *separate axis* from the §8.4 open regime — an earlier
draft conflated the two, a correction folded back here.

### 2.4 What the agents are *today*  [PROVEN scope statement]

The six canonical roles exist as **declared seams** — stable ABCs — populated so
far only by **non-learned baselines**:

- **World** — the DES engine + scenario generators.
- **Gate** ([`../python/epure_arena/gate/__init__.py`](../python/epure_arena/gate/__init__.py)):
  `AlwaysOnGate` / `AlwaysOffGate` / `BudgetedRankingGate` (top-k).
- **Propose** ([`../python/epure_arena/propose/__init__.py`](../python/epure_arena/propose/__init__.py)):
  `SingleRecommitPropose` (re-certify everything affected).
- **Imagine** ([`../python/epure_arena/imagine/__init__.py`](../python/epure_arena/imagine/__init__.py)):
  `ZeroImagine` stub + the certified dry-run estimator.
- **Navigate** ([`../python/epure_arena/navigate/__init__.py`](../python/epure_arena/navigate/__init__.py)):
  fixed `argmax` selector.
- **Optimize** — the certified A\* planner + Dijkstra baseline, extended via
  [`../python/epure_arena/optimize/registry.py`](../python/epure_arena/optimize/registry.py).

**There are no learned agents yet.** A swap into one seam cannot silently change
another's semantics — that invariant is the point of the decomposition, and it
is what makes the seams *ready* for learned policies (§3). The reason no learned
policy is shipped is honest: in the implemented **certified-full-recourse**
budgeted-selection regime the value function is submodular-like and
**greedy-marginal is provably optimal**, so a learned policy earns nothing
*there* (stated in code at
[`../python/epure_arena/env/recourse.py`](../python/epure_arena/env/recourse.py)).
The regime where greedy *does* leave a gap — the wait-cost / controlled-
bottleneck experiment — is measured in §2.6; a learned policy that closes it is
the open work (§3).

### 2.5 The wait-cost apparatus  [PROVEN]

The cost of *waiting* is priced as one physical quantity end to end. The engine
accumulates `held_atu` deterministically; the value functional
`v_realized = v − C_wait(held) − late_loss` prices it with **no cap and no
floor**, and the per-execution `ValueReport` closes with **zero unexplained
mass** (`total = v_realized + wait_loss + late_loss + strand_loss +
censored_loss`, asserted). Convexity (`b>0`) is load-bearing — it makes `V(S)`
non-additive through shared slots. The regimes are anchors named by shape
(`STEEP_SCARCE`, `SLACK_ELASTIC`, `DEMO_ZERO`), never by application domain. A
**conservative wait-pricing ILP oracle** (Gate P1a) certifies a *lower bound*
on `v_realized`: it prices `C_wait` by its chord envelope (over-charge ⇒ the
reported optimum cannot inflate a gap) over a strict subset of engine-feasible
schedules, and round-trips — the claimed value recomputed at exact pinned times
equals the engine's. The oracle's **trust boundary is itself tested**: HiGHS is
the certified backend, and CBC is refused on the hard-tolerance big-M class
where its presolve is proven to return false infeasibility (an
externally-verified HiGHS solution exists in a region CBC declared empty).
Tests: [`../tests/test_pricing.py`](../tests/test_pricing.py),
[`../tests/test_ilp_wait.py`](../tests/test_ilp_wait.py).

This is the **apparatus** for pricing wait — the measurement instrument. Using
it to compare policies and quantify a *decision gap* is a committed result,
stated next.

### 2.6 The decision gap and the inflicted-wait externality  [PROVEN]

With the wait-cost apparatus (§2.5) over a **controlled-bottleneck** world (a
ρ-knob that overwrites capacity on one named cut,
[`../repro/bottleneck.py`](../repro/bottleneck.py)):

- **The gap is real and grows with steepness** — greedy-vs-oracle regret
  `R_raw(s)` is monotone `+2.53 → +24.82` over `s ∈ [0, 0.75]`, 95% CIs exclude
  0, with a clean non-competing-unit control. `make reproduce-decision-gap`.
- **The inflicted-wait externality exists** — a wait-blind / own-wait greedy
  leaves `R_wait_S = +9.63 [+5.4, +13.8]` (domain-agnostic, s=0.75) and
  `+13.77 [+8.1, +19.5]` (steep_hard, admission-enforced) against the oracle,
  invisible to any single-unit marginal. `make reproduce-inflicted-wait`.

Scope rides every number: **n=12, d=120, HiGHS-certified, controlled bottleneck
ρ=2.0, conservative oracle** — so each positive gap is a **guaranteed-real
lower bound**. The own-wait / inflicted *split* is noisy at n=5 (a
characterization); a *learned* policy that closes the gap, and natural scale,
are future work (§3). Full index + honesty labels:
[`../RESULTS.md`](../RESULTS.md).

---

## 3. The theoretical frame, and what I envision to build  [FRAME / OPEN]

> **This section is a research frame and program, not proven results.** A
> lit-review reader should treat every item below as an *open problem to aim
> references at*, not a claim epure-arena has established.

### 3.1 Decision quality *above* optimization

The thesis: in constrained dynamic systems, the dominant lever on outcomes is
**not the optimizer** but **the decision of what to hand the optimizer** — *which
flow units to commit, which to refuse, what assumptions and envelope to assume,
and at which points in time to (re-)solve at all*. Optimization is a **banded
tool invoked at chosen decision points**, not the top-level loop. epure-arena is
built to make this measurable: the certifier turns "what should we have decided"
into a quantity with a certificate, so *decision* policies can be graded
independently of the *optimizer* they call.

Concretely, the **committed set** and the **assumptions/envelope** are the
decision variables; the certified A\* planner is the *banded tool* that turns a
committed set into a realized outcome; and budgeted regret against the certified
oracle is the score. The seam decomposition (Gate/Propose/Imagine/Navigate over
Optimize) exists precisely so that the *decision* layers can be learned while the
*optimizer* and the *physics* stay fixed and trusted.

### 3.2 The lineage (§6 / §7 / C4)

The frame descends from a documented internal lineage, referenced by section
markers throughout the code (the narrative paper that fixes these section
numbers is produced by the Book pipeline, not in this repo):

- **§6** — the offline certificate lattice: coarsened, flow-unit-count-invariant
  upper bounds (`oracle ⊇ cohort-LP ⊇ achieved`), realized in
  [`../python/epure_arena/lattice/cohort_flow.py`](../python/epure_arena/lattice/cohort_flow.py).
- **§7** — the certified online planner and its cost discipline: time-dependent
  A\* under the admissible capacity-free bound (the "certify" path), realized in
  [`../python/epure_arena/optimize/capacity_aware.py`](../python/epure_arena/optimize/capacity_aware.py)
  and characterized in [`./benchmarks.md`](./benchmarks.md).
- **§8.4** — the **open regime**: partial / uncertified recourse, where
  re-certification is limited by budget or observability, joint value stops being
  submodular, and a **learned policy can beat greedy-marginal**. Marked in code
  as deliberately *not implemented* so the boundary is explicit
  ([`../python/epure_arena/env/recourse.py`](../python/epure_arena/env/recourse.py)).
- **C4** — the budgeted-set result that closes the *certified-full-recourse*
  regime (greedy-marginal optimal there, so a learned agent earns nothing in
  *that* regime), which is exactly why the program moves to §8.4 — and to the
  wait-cost regime (§2.6), where greedy *does* leave a certified gap.

### 3.3 The forward program

1. **Learned agents into the seams.** Replace the baseline Gate / Propose /
   Imagine / Navigate with learned components, graded by certified regret. The
   seams already isolate them; what is missing is a regime where learning *pays*
   (§3.4) and the learned implementations themselves.
2. **Energy-based models for the committed-set decision.** The core decision is
   **combinatorial selection** of a committed set under a budget. An EBM over
   sets (energy = certified regret surrogate) is a candidate for the
   Gate/Navigate seams where the value is sub-additive and ranking is not
   enough.
3. **RL with the exact reward.** The environment's distinguishing property — an
   *exact, certified* reward from paired replay rather than a noisy proxy — makes
   it a clean substrate for RL **once the regime is hard enough** to have a
   target (§3.4). The reward is never on a gradient path; it is a replayed float.
4. **Latent-ODE / neural-ODE for anticipation.** The Imagine seam needs a cheap,
   *honest* forecast of "if we commit this, what congestion future does it
   create?" A latent-space / latent-ODE model of the world's congestion dynamics
   is the envisioned anticipatory estimator — trained against the certifier's
   exact realized Δ, so its errors are measurable.
5. **The congested / competing-bottleneck regime where decision provably beats
   reactive optimization.** The target regime has **many comparable-value,
   *competing* interventions** (sub-additive value), so reactive rerouting after
   the fact (e.g. CBS-style conflict resolution) cannot recover what an *ex-ante*
   decision would have prevented. The envisioned result: **ex-ante admission /
   priority / timing decisions that prevent congestion rather than reroute around
   it** — refusing or delaying the right flow units *before* commitment so the
   network never saturates — provably dominate any after-the-fact optimizer on
   the same world. A **controlled** version of such a world now exists — the
   ρ-knob bottleneck ([`../repro/bottleneck.py`](../repro/bottleneck.py)) — and a
   decision gap is measured there (§2.6). What remains open is the *natural-scale*
   congested world (the controlled one is n=12/d=120, ρ=2.0) and a learned policy
   that provably closes the gap; the default disruption generators stay
   over-determined (§2.3).

### 3.4 Why this is open, stated plainly

A learned policy earns its place **only** in a regime where the certified
greedy-marginal is *not* already optimal. The default budgeted-selection regime
is over-determined and full-recourse — a clean **testbed**, not a hard problem.
That greedy *is* sub-optimal in a competing-bottleneck regime is now **proven at
controlled scale** (§2.6: `R_wait > 0`, a certified lower bound). What remains
is the open program: **natural-scale** competing worlds, partial / uncertified
recourse (§8.4), and a **learned decision policy that closes** the gap the
optimizer alone leaves — none of *those* is proven; they are the program the
apparatus was built to make *provable*.

---

## 4. References wanted (for literature retrieval)

Aim recent (last ~5 years, plus seminal anchors) CS/AI literature at the
**[FRAME / OPEN]** problems in §3. Specifically:

1. **Certified / verifiable RL environments & simulators** — environments with
   *exact* or formally-guaranteed rewards, deterministic replay, and
   verification of the reward signal itself (vs. noisy proxies); reproducibility
   and determinism as first-class properties of an RL benchmark.
2. **Exact-reward & counterfactual credit assignment** — paired-rollout /
   same-seed counterfactuals, off-policy exact advantage, structural credit
   assignment, and telescoping/marginal-value decompositions for sequential
   decisions.
3. **Energy-based models for combinatorial selection** — EBMs / learned
   energies over discrete sets, subset selection, submodular and *sub-additive*
   value, neural combinatorial optimization for selection-under-budget.
4. **Latent-ODE / neural-ODE for anticipation** — latent-space continuous-time
   models for forecasting system dynamics (here: congestion), and using such
   forecasts as cheap honest value estimators inside a decision loop.
5. **Multi-agent path finding (CBS) at scale and its limits** — conflict-based
   search and successors, scalability ceilings, and especially the boundary
   where *reactive* conflict resolution underperforms *ex-ante* admission /
   scheduling (the case for deciding before routing).
6. **Decision-focused / decision-aware learning** — "predict-then-optimize",
   smart predict+optimize, end-to-end learning where the loss is decision
   regret rather than prediction error; when estimator accuracy does vs. does
   not move the decision (the inverse of the §2.3 over-determined case).
7. **Admission / congestion control under hard constraints** — admission
   control, priority and timing decisions that *prevent* saturation, and online
   resource allocation under capacity limits, framed as decision quality above
   reactive optimization.

---

*Reproduce everything here from a clean clone: `make develop` then
`make test` / `make reproduce-smoke` / `make reproduce-benchmark` (see
[`../Makefile`](../Makefile)). Counts and digests byte-assert; wall-times and
memory are reported with hardware noted.*
