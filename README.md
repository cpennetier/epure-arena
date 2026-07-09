# Epure Arena

*A certified arena for measuring when intelligence is worth spending.*

**A certified environment for decision quality under hard constraints.**

A *world* is a capacitated time-graph: flow units traverse nodes and lanes by
boarding scheduled, capacity-limited connections. epure-arena gives you three
things over such a world, and they are the artifact:

1. **A world that replays exactly.** Same seed ⇒ byte-identical run — KPIs,
   records, surfaces, manifest. Paired same-seed rollouts (act vs. no-op)
   isolate the causal value of a decision with leg-level exactness.
2. **Typed, *proven* attribution.** Every non-ideal outcome is classified by a
   destination-rooted backward oracle as **world's-fault** (genuinely
   infeasible / structural) or **policy's-fault** (feasible-on-time — the
   policy refused it; or feasible-late). "The network was full" becomes a
   theorem about the timetable, not an excuse.
3. **Any policy graded by regret** against a certified reference. Plug a policy
   into the environment; the harness scores it by its gap to a certified
   oracle. A learned component swapped into one seam cannot silently change
   another's semantics.

**The thesis** (developed in [`docs/OVERVIEW.md`](docs/OVERVIEW.md) §3): in
constrained dynamic systems the dominant lever is *which* flow units to commit
or refuse and *when* to re-solve — the decision handed to the optimizer, not the
optimizer itself. The apparatus above makes that decision measurable; the first
result it yields — a certified **decision gap** that greedy leaves under wait
cost — and every other result, each with its honesty label and scope, are
indexed in [`RESULTS.md`](RESULTS.md).

> **Orientation.** This is a functional README — what the thing is and how to
> run it. The narrative paper is [`decision-quality.pdf`](decision-quality.pdf),
> rendered separately by the Book's Typst pipeline.

> **Presentation.** A visual, scrollable presentation of this work — the
> apparatus, the four proven guarantees, the measured results with their
> honesty labels and scope, and the research frame — is at
> <https://decision-quality-site.vercel.app/>. An evolving research program;
> the full paper and extended notebook are in progress there.

## The three layers

`epure-arena` is one engine in three responsibility layers (full map in
[`ARCHITECTURE.md`](ARCHITECTURE.md)):

- **DES world** — the deterministic engine (the
  [ephemeris-kernel](https://github.com/cpennetier/ephemeris-kernel)
  time-and-truth runtime, re-exported as `epure_arena._engine`) + world
  generators (`epure_arena.scenarios`).
- **Certifier** — the feasibility oracle, certificate lattice, plan fidelity,
  and typed reasons (`epure_arena.{harness,lattice}`, `optimize/fidelity.py`,
  `reasons.py`).
- **Agents** — the planner ABC + reference planners and the declared seams
  (`epure_arena.optimize`, `epure_arena.env`, and `gate/propose/imagine/navigate`).

The dependency arrow points one way: **core never imports an adapter**
(enforced by `tests/test_dependency_direction.py`), and carries **no domain
vocabulary** (enforced by `tests/test_no_domain_vocabulary.py`).

## Quick start

```bash
# pure Python; the compiled engine arrives via the ephemeris-kernel
# dependency (needs a Rust toolchain — pip builds it automatically)
pip install -e ".[oracle,dev]"
make reproduce-smoke    # anchor results, byte-asserted against committed tables
```

## Run it

| command | what it does |
|---|---|
| `make test` | Rust suites + Python suite (golden worlds, invariants) |
| `make reproduce-smoke` | per-commit tier (<10 min): anchor cells byte-asserted; exits 0 iff byte-identical |
| `python examples/random_vs_greedy_vs_oracle.py` | instantiate the environment; run random / greedy-marginal / oracle policies; grade each by budgeted regret |
| `make reproduce-estimation` | the estimation-vs-decision **characterization** (see below), byte-asserted from a committed extract |
| `make reproduce-benchmark` | performance characterization: throughput + latency-decomposed certify at 10k…1M; counts/digests byte-asserted, wall-times reported |
| `make reproduce-decision-gap` | Gate P1b: greedy-vs-oracle regret vs wait-cost steepness (~25 min, 30 HiGHS solves); deterministic fields byte-asserted ([`RESULTS.md`](RESULTS.md) §4) |
| `make reproduce-inflicted-wait` | Gate P1c: own-wait vs inflicted-wait decomposition (~10 min); the `R_wait` +9.63 / +13.77 goldens, byte-asserted ([`RESULTS.md`](RESULTS.md) §5) |
| `make reproduce-paper` / `reproduce-1m` | the longer tiers (full tables; 1M-flow-unit scale benchmark) |

## Using the environment

```python
from epure_arena.env import BudgetedRecourseEnv, Oracle, DEFER, INTERVENE

env = BudgetedRecourseEnv(demand=2000)
obs = env.reset(world="backbone", seed=42, severity="moderate", cost=1.0, budget=2)
done = False
while not done:
    action = my_policy(obs)              # DEFER (0) or INTERVENE (1)
    obs, reward, done = env.step(action) # reward = realized marginal Δ − cost

# cumulative reward telescopes to V(selected_set); grade it against the ceiling
grader = Oracle(env.suite, cost=1.0, budget=2)
regret = grader.budgeted_regret(env.selected_set)   # V(S*) − V(S_policy)
```

The executor (the DES live loop behind the reward) is the *environment* —
never on a gradient path. Rewards are plain floats from replaying a
deterministic simulation.

## Estimation vs. decision — a characterization, not a result

`make reproduce-estimation` reproduces a *measured fact about an
**over-determined** regime* — it is **not** a headline result and **not** a
law that "better prediction never helps decisions":

- The budgeted decision is dominated by ~2 high-value interventions per suite,
  so a simple top-k ranking already reaches the optimum. Estimator accuracy
  does not move the committed set beyond noise: **top-2** budgeted ranking
  regret is identically 0 for every estimator (including a non-value
  `severity` baseline); **top-1** does not separate beyond ~1 standard error
  over 72 suites (e.g. 82.6 ± 47 vs 103.5 ± 49).
- Estimator MAE meanwhile varies up to ~3.5× across arms — and that accuracy
  lands exactly where `slack = 0` makes the decision forced (`demand_surge`).

So in *this* regime, estimation accuracy and decision quality are decoupled
**because the decision is easy here**, stated with its noise. The regime where
method choice would actually matter — many comparable, *competing*
interventions (sub-additive value) — is where a decision gap appears, and a
purpose-built **controlled-bottleneck** world now measures one (see
[`RESULTS.md`](RESULTS.md) §4–§5). Whether *estimator* accuracy moves that
decision, and partial / uncertified recourse, remain future work.

## A certified RL environment

`BudgetedRecourseEnv` is a ready reinforcement-learning environment with a
property most lack: **the reward is exact, not estimated.** Because the world
replays deterministically, paired same-seed rollouts (act vs. no-op) yield the
true counterfactual value of any action — the reward signal is certified by
construction, not a noisy proxy. A policy's regret is measured against a
certified oracle, so "did the agent improve" is a proven quantity, not an
inference. The currently-implemented **budgeted set-selection** regime is
over-determined (above), so it is a clean testbed rather than a hard RL problem
today. The harder, competing regime where a decision gap appears is now
measured separately — the wait-cost / controlled-bottleneck experiment
([`RESULTS.md`](RESULTS.md) §4–§5); a *learned* policy that closes that gap,
and partial / uncertified recourse, remain future work.

## Wait-cost pricing — the held-time apparatus

The engine records **`held_atu`** (deterministic held-but-not-in-transit time
per flow unit), and a value functional prices it:
`v_realized = v − C_wait(held) − late_loss`, with convex
`C_wait = v·(a·w + b·w²)` — no cap, no floor, and a per-run decomposition that
closes with **zero unexplained mass**. The `b>0` term is load-bearing: it makes
`V(S)` non-additive through shared slots. Cost regimes are anchors named by
*shape*, never by domain — `STEEP_SCARCE`, `SLACK_ELASTIC`, and `DEMO_ZERO`
(the `C_wait≡0` point, where `v_realized` reduces to on-time value). A
**conservative wait-pricing ILP oracle**
([`python/epure_arena/lattice/ilp_wait.py`](python/epure_arena/lattice/ilp_wait.py))
lower-bounds `v_realized` (a chord envelope over-charges the convex cost ⇒ the
reported optimum cannot inflate), certified under the **HiGHS** backend; CBC is
refused on the hard-tolerance class where its presolve returns false
infeasibility. Pinned by [`tests/test_pricing.py`](tests/test_pricing.py) and
[`tests/test_ilp_wait.py`](tests/test_ilp_wait.py).

This is the pricing *apparatus* — the instrument. Using it to quantify a
*decision gap* between policies is now a **committed result**: greedy leaves a
real, oracle-certified gap that grows with wait-cost steepness, and the
inflicted-wait externality exists as a guaranteed-real lower bound — see
[`RESULTS.md`](RESULTS.md) §4–§5 (`make reproduce-decision-gap` /
`make reproduce-inflicted-wait`).

## Reproducibility

Reproductions byte-assert against **committed golden data**. Pin-reproducible
artifacts (the golden worlds, the `examples/` env demo) regenerate
deterministically from the engine. Where a result's **raw run folder is not
retained** (gitignored / absent), the script reproduces the *committed extract*
and says so in its output — it is not a from-zero re-derivation. The
estimation characterization is one such case: its `gbt` arm is not byte-stable
across sklearn versions, which is why the extract is committed (and why that
reproduction needs no sklearn).

## Domain adapters

epure-arena is domain-neutral by construction. Anything that is a capacitated
time-graph fits — supply networks, compute-job placement, patient flow, energy
dispatch, rolling-stock rotation. An adapter maps its entities onto the wire
protocol (`docs/protocol.md`), registers its own planners into the registry,
and inherits the full apparatus — determinism, attribution, certificates,
paired counterfactuals — for free.

## License

Dual-licensed:

- **Code** — Apache-2.0 ([`LICENSE`](LICENSE)).
- **Written artifacts** (this README, [`RESULTS.md`](RESULTS.md),
  [`docs/OVERVIEW.md`](docs/OVERVIEW.md), [`docs/benchmarks.md`](docs/benchmarks.md),
  [`docs/protocol.md`](docs/protocol.md), and the paper
  [`decision-quality.pdf`](decision-quality.pdf)) — CC-BY-4.0.

Copyright 2026 Christophe Pennetier.
