# Epure Arena

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/cpennetier/epure-arena/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cpennetier/epure-arena/actions/workflows/ci.yml)

**A certified environment for measuring decision quality under hard constraints.**

> **Research program — verifiable learning and decision systems.**  
> Proposal mechanisms may be heuristic or learned; the structures they produce, the worlds they act in, and the value they create remain independently measurable.
>
> **Role of this repository:** certified evaluation of decisions in constrained dynamic systems.  
> [PatternBoost](https://github.com/cpennetier/spectral-graph-patternboost) ·
> [Graph diffusion](https://github.com/cpennetier/spectral-graph-diffusion) ·
> [Ephemeris Kernel](https://github.com/cpennetier/ephemeris-kernel) ·
> [Epure Arena](https://github.com/cpennetier/epure-arena)

## Research question

When a policy produces a better outcome, how do we know that the gain came from the decision rather than from stochastic drift, evaluator weakness, or a change in execution semantics?

Epure makes the comparison explicit:

```text
policy proposes a decision
          │
          ▼
certifier checks feasibility and reference value
          │
          ▼
deterministic world executes the decision
          │
          ▼
paired counterfactual replay measures causal value
          │
          ▼
policy is graded by regret against a certified reference
```

The current reference world is a **capacitated time-graph**. Flow units move across nodes and lanes by boarding scheduled, capacity-limited connections.

The artifact is not a single planner. It is the apparatus required to answer:

- What physically happened?
- Was a bad outcome avoidable?
- Which layer owns the failure?
- What was the decision worth?
- How far is a policy from a certified reference?

## Three guarantees

### 1. Exact replay

The same seed and inputs produce a byte-identical run on the supported platform:

- KPIs;
- event and outcome records;
- Arrow surfaces;
- run manifest.

Paired same-seed rollouts—action versus no-op—therefore isolate the causal value of a decision without simulator noise.

### 2. Typed, proven attribution

A destination-rooted backward oracle classifies non-ideal outcomes as:

- **world-caused:** no feasible path exists under the stated envelope;
- **policy-caused:** an on-time or late feasible path existed, but the policy did not realize it.

The attribution table closes with zero unexplained mass. A vague explanation such as “capacity was unavailable” becomes a checkable statement about the time-graph.

### 3. Regret against a certified reference

Any policy can be evaluated by

$$
\operatorname{regret}(S_{\text{policy}})
=
V(S^\*)-V(S_{\text{policy}}),
$$

where $S^*$ is the certified reference under the same cost and budget constraints.

A learned component can be inserted into one declared seam without silently changing the world, reward, or another component's semantics.

## Thesis and current result

The research thesis is that, in constrained dynamic systems, the high-value question is often **which units to commit or refuse, and when to reconsider the decision**—not merely which low-level optimizer is used after the decision boundary has already been chosen.

The repository does not present that thesis as a universal theorem.

What it establishes now is narrower and stronger:

> Under a controlled bottleneck with convex wait cost, a natural greedy policy leaves a positive oracle-certified decision gap, and part of that gap comes from waiting imposed on units committed later—an externality that a single-unit marginal cannot see.

Every result is indexed with an honesty label, scope, and reproduction command in [`RESULTS.md`](RESULTS.md).

> **Orientation.** This README explains what the artifact is and how to run it.  
> The longer research narrative is [`decision-quality.pdf`](decision-quality.pdf).  
> The arena's first published study is the coordination-frontier note:
> [`experiments/coordination-frontier/coordination-frontier.pdf`](experiments/coordination-frontier/coordination-frontier.pdf).
>
> **Presentation.** **[PLACEHOLDER — insert the confirmed public presentation URL here. Do not remove this block without replacing it.]**

## Architecture

Epure has three responsibility layers. The full directory-level map is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

| Layer | Question | Implementation |
|---|---|---|
| **Deterministic world** | What physically happened? | [`ephemeris-kernel`](https://github.com/cpennetier/ephemeris-kernel), `epure_arena.scenarios`, `epure_arena.world` |
| **Certifier** | Was the outcome avoidable, and what proves the attribution? | `epure_arena.harness`, `epure_arena.lattice`, `reasons.py`, `optimize/fidelity.py` |
| **Agent seams** | What should be committed, and what was it worth? | `optimize`, `env`, `gate`, `propose`, `imagine`, `navigate` |

The dependency direction is intentional:

```text
agent seams
    │
    ▼
certifier and evaluation harness
    │
    ▼
deterministic world
```

Core never imports a downstream adapter. This is enforced by `tests/test_dependency_direction.py`.

## What “certified” means here

“Certified” does not mean that every optimization problem is solved globally under every possible model.

It means that a stated quantity is attached to an explicit proof or invariant:

- **Exact replay:** byte-identity tests and committed golden worlds.
- **Feasibility attribution:** a backward oracle over the time-graph.
- **Plan fidelity:** pinned execution with a three-way guaranteed/opportunistic/refused split.
- **Mass closure:** every flow unit is accounted for or the run fails.
- **Wait-cost coherence:** planner reconstruction, engine-held time, and oracle value agree at pinned times.
- **Conservative oracle:** the wait-pricing ILP reports a lower bound on realized value, so a positive measured gap cannot be inflated by the oracle.
- **Regret:** a policy is compared under the same environment, cost, and budget.

The scope of each certificate is stated beside the result.

## Quick start

```bash
git clone https://github.com/cpennetier/epure-arena.git
cd epure-arena

pip install -e ".[oracle,dev]"
make reproduce-smoke
```

`make reproduce-smoke` is the fast end-to-end anchor. It reruns committed cells and exits successfully only when deterministic fields match the golden tables.

## Run the artifact

| Command | Purpose |
|---|---|
| `make test` | Python invariant, pricing, and golden-world suites (the Rust engine is tested in ephemeris-kernel's own CI) |
| `make reproduce-smoke` | Fast byte-asserted anchor cells |
| `python examples/random_vs_greedy_vs_oracle.py` | Compare random, greedy-marginal, and oracle policies by budgeted regret |
| `make reproduce-estimation` | Reproduce the estimation-versus-decision characterization |
| `make reproduce-benchmark` | Reproduce deterministic counts and report throughput and latency |
| `make reproduce-decision-gap` | Reproduce greedy-versus-oracle regret across wait-cost steepness |
| `make reproduce-inflicted-wait` | Reproduce own-wait and inflicted-wait decomposition |
| `make reproduce-paper` | Reproduce the longer report tables |
| `make reproduce-1m` | Run the million-flow-unit scale tier |

## Use the RL-facing environment

```python
from epure_arena.env import BudgetedRecourseEnv, Oracle, DEFER, INTERVENE

env = BudgetedRecourseEnv(demand=2000)

obs = env.reset(
    world="backbone",
    seed=42,
    severity="moderate",
    cost=1.0,
    budget=2,
)

done = False
while not done:
    action = my_policy(obs)  # DEFER (0) or INTERVENE (1)
    obs, reward, done = env.step(action)

grader = Oracle(env.suite, cost=1.0, budget=2)
regret = grader.budgeted_regret(env.selected_set)
```

The environment returns plain floating-point rewards from deterministic paired replay:

$$
r_t = \Delta V_t - \text{intervention cost}.
$$

Cumulative reward telescopes to the value of the selected set. The executor is part of the environment and is never placed on a gradient path.

## Reproducible results

The canonical, fully scoped result index is [`RESULTS.md`](RESULTS.md). The table below is an orientation layer, not a replacement for it.

| Result | Label | Scope | Reproduce |
|---|---|---|---|
| Certified A* is 2.43× faster than this repository's Dijkstra control at 1M flow units | **PROVEN** | Single machine, one scenario family, absolute—not cross-system | `make reproduce-benchmark` |
| Estimator MAE varies while top-2 decision regret stays at zero in the implemented budgeted-selection regime | **CHARACTERIZATION** | Over-determined regime; not a general claim about prediction and decision quality | `make reproduce-estimation` |
| Plan, engine, and oracle wait values agree and close with zero unexplained mass | **PROVEN** | Exact pinned execution | `make test` |
| Greedy-versus-oracle regret rises with wait-cost steepness | **PROVEN** | Controlled bottleneck, conservative HiGHS-certified oracle | `make reproduce-decision-gap` |
| A positive inflicted-wait externality remains beyond single-unit marginal reasoning | **PROVEN** | Existence and lower-bound claim under the stated controlled regime | `make reproduce-inflicted-wait` |
| The own-wait versus inflicted-wait split ratio is noisy | **CHARACTERIZATION** | The positive externality is load-bearing; the ratio is not | `make reproduce-inflicted-wait` |

### The decision-gap result

For steepness values $s\in[0,0.75]$, reported greedy-versus-oracle regret is

$$
2.53 \rightarrow 4.52 \rightarrow 9.88 \rightarrow 24.82,
$$

with 95% confidence intervals excluding zero in the stated experiment.

The oracle is conservative: it over-prices the convex wait cost and searches a restricted feasible set. Therefore a positive reported regret is a guaranteed-real lower bound on the true gap.

### The inflicted-wait result

The wait-blind or own-wait greedy policy leaves a residual that a single-unit marginal does not observe:

- $R_{\text{wait},S}=+9.63\,[+5.4,+13.8]$ at $s=0.75$;
- $R_{\text{wait},S}=+13.77\,[+8.1,+19.5]$ in the harder admission-enforced leg.

These are existence and lower-bound results under the exact scope documented in [`RESULTS.md`](RESULTS.md).

## Estimation versus decision

`make reproduce-estimation` reproduces a characterization of an **easy, over-determined regime**.

In that regime:

- approximately two interventions dominate each suite;
- top-2 ranking regret is zero for every tested estimator;
- estimator MAE varies by approximately 3.5×;
- the committed set therefore does not move.

This does **not** establish that better prediction never improves decisions. It establishes that estimator improvements cannot create value when the decision is already forced by the regime.

The controlled-bottleneck experiment is the separate regime in which competing commitments create a measured decision gap.

## A certified RL environment

`BudgetedRecourseEnv` provides a reinforcement-learning-facing surface with two unusual properties:

1. **The reward is exact with respect to the deterministic environment.**  
   It is computed from paired same-seed action/no-op replay rather than from a learned reward model.

2. **Policy quality is measured against a certified reference.**  
   Improvement is expressed as regret under the same cost and budget.

The currently implemented budgeted set-selection regime is a clean testbed rather than a difficult learned-control benchmark. The repository claims no learned policy that closes the controlled-bottleneck gap.

## Wait-cost apparatus

The engine records deterministic held-but-not-in-transit time as `held_atu`.

Realized value is

$$
v_{\text{realized}}
=
v-C_{\text{wait}}(\text{held})-\text{late loss},
$$

with

$$
C_{\text{wait}}
=
v\left(a\,w+b\,w^2\right).
$$

The quadratic term is load-bearing: it makes the value of a committed set non-additive through shared capacity.

The named regimes describe shape rather than application:

- `STEEP_SCARCE`;
- `SLACK_ELASTIC`;
- `DEMO_ZERO`.

The conservative wait-pricing ILP uses HiGHS for the certified hard-tolerance class. The implementation refuses a backend that returns false infeasibility on that class.

## Reproducibility contract

Reproduction commands byte-assert deterministic fields against committed golden data.

Two cases are distinguished:

- **From-world regeneration:** golden worlds and the environment demo regenerate from the engine.
- **Committed-extract reproduction:** when a raw run folder is not retained or a third-party model is not byte-stable across versions, the script reproduces the committed extract and states that boundary explicitly.

A script that reproduces an extract is not described as a from-zero re-derivation.

## Scope and open work

What is established:

- deterministic replay;
- typed attribution with mass closure;
- exact counterfactual reward;
- certified-reference regret;
- wait-cost coherence;
- a positive decision gap in the stated controlled regime;
- existence of an inflicted-wait externality.

What is not claimed:

- a universally optimal planner;
- a learned policy that closes the measured gap;
- a general law that estimation and decision quality are decoupled;
- full certification under partial or uncertain recourse;
- unrestricted scaling of the ILP oracle;
- applicability outside systems representable by the protocol.

The declared `gate`, `propose`, `imagine`, `navigate`, and `optimize` seams are ready for learned components, but the current implementations are non-learned baselines.

## First published study: the coordination frontier

*When Is Intelligence Worth Spending? Epure Arena and the Coordination
Frontier* — published in `v0.2.0` at
[`experiments/coordination-frontier/`](experiments/coordination-frontier/):

- [the note (PDF)](experiments/coordination-frontier/coordination-frontier.pdf), rendered from the committed Typst source;
- [`REPRODUCE.md`](experiments/coordination-frontier/REPRODUCE.md) — the ledger → numbers → figures chain and its exact public/private boundary;
- nine committed JSON ledgers as the primary records;
- `verify_note_numbers.py` — re-asserts every number quoted in the note against the ledgers (97 checks, stdlib only);
- `make_figures.py` — regenerates every figure from the same ledgers (stdlib only).

```bash
cd experiments/coordination-frontier
python3 verify_note_numbers.py   # 97 checks, exits non-zero on any mismatch
python3 make_figures.py          # figures regenerate from the ledgers
```

## Repository map

```text
python/epure_arena/
  scenarios/       deterministic world generation
  world/           world-to-planner bridge
  harness/         paired replay, feasibility, run lake
  lattice/         certificate bounds and wait-pricing oracle
  optimize/        planner interface, baselines, compilation, fidelity
  env/             RL-facing environment and grader
  gate/            intervention-selection seam
  propose/         candidate-generation seam
  imagine/         counterfactual-estimation seam
  navigate/        selection seam
  reasons.py       typed attribution vocabulary

examples/           runnable policy and fidelity examples
experiments/        published studies (coordination-frontier: note + ledgers)
repro/              reproduction scripts and committed goldens
tests/              invariants, golden worlds, pricing, and certification
docs/               overview, protocol, and benchmarks
ARCHITECTURE.md     dependency and responsibility map
RESULTS.md          canonical scoped result index
decision-quality.pdf longer research narrative
```

## Relationship to Ephemeris Kernel

[`ephemeris-kernel`](https://github.com/cpennetier/ephemeris-kernel) owns the deterministic execution substrate. Epure adds the world generators, certifier, policy seams, counterfactual harness, and grading logic above it.

Epure may depend on Ephemeris. Ephemeris does not depend on Epure.

## License

Dual-licensed:

- **Code:** Apache-2.0 — see [LICENSE](LICENSE).
- **Written artifacts:** CC-BY-4.0.

Copyright © 2026 Christophe Pennetier.
