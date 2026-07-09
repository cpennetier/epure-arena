# epure-arena — Architecture Map

> The public, domain-neutral apparatus. Apache-2.0. Pinned by downstream
> applications at a fixed git tag (`v0.1.0`). The dependency arrow is
> one-way: **core never imports an adapter** — enforced by
> `tests/test_dependency_direction.py`. Core carries **no domain
> vocabulary** — enforced by `tests/test_no_domain_vocabulary.py`. It speaks
> only in *flow units*, *nodes*, *lanes*, and *connections*.

## The three layers

epure-arena is one engine expressed in three responsibility layers. Every
top-level directory below is tagged with the layer it serves.

| Layer | Question it answers | Where it lives |
|---|---|---|
| **DES world** | *What physically happened?* A capacitated time-graph replayed exactly. | `ephemeris-kernel` (dependency), `python/epure_arena/{scenarios,world}` |
| **Substrate / certifier** | *Was this outcome avoidable? Whose fault — world or policy?* Typed reasons backed by proofs. | `ephemeris-kernel` ledger/FSM crates (dependency), `python/epure_arena/{harness,lattice,reasons.py}`, `optimize/fidelity.py` |
| **Agents (seams)** | *What should we commit, and what was it worth?* Pluggable decision policies graded by the certifier. | `python/epure_arena/{optimize,gate,propose,imagine,navigate}` |

## Top-level directories

| Dir | Layer | What it does | Imports | Imported by |
|---|---|---|---|---|
| `ephemeris-kernel` → `ephemeris._engine` | DES world | The deterministic discrete-event engine (external dependency; re-exported as `epure_arena._engine`). | `pyo3`, `arrow` | `epure_arena` (Python), every downstream sim path |
| `ephemeris-kernel` → `ephemeris_ledger_py` | certifier | Capacity ledger — per-(lane, slot) residual accounting. | `pyo3`, `arrow` | downstream pin (`ephemeris-ledger`), engine |
| `ephemeris-kernel` → `ephemeris_fsm_py` | certifier | Flow-unit state machine (lifecycle transitions). | `pyo3`, `arrow` | engine, harness |
| `python/epure_arena/scenarios/` | DES world | World generation: `library` (`ScenarioSpec`, `generate_scenario`), `generators`, `schedules`, `demand`, `disruptions`, `golden_worlds`, `serializer`. Deterministic from seed. | stdlib, numpy, `_engine` | `harness`, `optimize`, downstream worlds |
| `python/epure_arena/world/` | DES world ↔ agents bridge | `graph_adapter.build_plan_context` — turns a world into a `PlanContext` the planners consume. | `scenarios`, `optimize.interface` | downstream planning endpoints, `optimize` |
| `python/epure_arena/harness/` | certifier | `feasibility_oracle` (backward profile oracle: feasible-on-time / feasible-late / infeasible), `live_loop` (paired-rollout counterfactual, mass closure, byte identity), `runlake`. | `_engine`, `scenarios`, `optimize` | `repro/`, `tests/`, downstream live loops |
| `python/epure_arena/lattice/` | certifier | Certificate lattice upper bounds: `ilp_solver`, `cohort_flow` (destination-commodity cohorts, due-class budgets, suffix-Hall staircases). | numpy, optional `pulp`/`scipy` | `harness`, `tests/` |
| `python/epure_arena/optimize/` | agents + fidelity | `interface` (the `Planner` ABC, `PlanContext`/`PlanRequest`/`PlanResult`), `registry` (`default_registry` — the downstream-extension seam), `static` (Dijkstra baseline), `capacity_aware` (time-dependent A* over residuals — see below), `compiler` (`compile_for_engine`), `fidelity` (`apply_plan`, `three_way_split` — certifier). | `world`, `scenarios`, numpy | downstream planner family, `harness` |
| `python/epure_arena/{gate,propose,imagine,navigate}/` | agents | The declared agent seams — stable ABCs for the six-seam decomposition (World, Gate, Propose, Imagine, Navigate, Optimize). Swapping a learned component into one seam cannot silently change another's semantics. | `optimize.interface` | downstream learned policies |
| `python/epure_arena/reasons.py` | certifier | The typed reason vocabulary (e.g. `WORLD_CAPACITY_SHORTFALL`, `ALGO_CAPACITY_MYOPIA`). Each names the lever a failure is attributed to. | — | `optimize`, `harness` |
| `tests/` | all | Invariant suites: `test_dependency_direction` (one-way arrow), `test_no_domain_vocabulary` (neutrality), `golden_worlds/` (byte-identity, attribution pins), `test_feasibility_oracle`, `test_plan_fidelity`, `test_three_way_split`, `test_live_loop`. | the package | — (CI entry) |
| `repro/` | all | Reproduction scripts (`smoke`, `strand_matrix`, `regret_table`, `gate_experiment`, `benchmark_1m`, …) + `expected/` committed golden tables. Byte-asserted in CI. | the package | `make reproduce-*` |
| `examples/` | agents | Runnable usage examples (`plan_fidelity`, `strand_deep`). | the package | docs, onboarding |
| `docs/` | — | `OVERVIEW.md` (project overview), `protocol.md` (wire fingerprint), `benchmarks.md` (performance). | — | — |
| `scenarios/` (top-level) | — | Empty placeholder; the live generators are `python/epure_arena/scenarios/`. | — | — |
| `target/` | — | Rust build artifacts (gitignored). | — | — |

## Internal dependency direction (within core)

```
scenarios / world  ──►  optimize (interface, planners)  ──►  harness / lattice (certifier)
   (DES world)              (agents)                              (proofs over outcomes)
        ▲                                                                 │
        └──────────────────── reasons.py (typed attribution) ◄────────────┘
```

the kernel's compiled `_engine` underpins `scenarios` and `harness`.
Nothing in `epure_arena` imports anything outside
`{stdlib, numpy, pyarrow, h3, pulp, scipy, sklearn, epure_arena}` — the
`DECLARED` set in `tests/test_dependency_direction.py`. **A downstream
application package appearing in any core import fails CI.**

## The certified planner (what "certify" actually runs)

`optimize/capacity_aware.py :: CapacityAwarePlanner` is **time-dependent
A\***, not Dijkstra:

- Search over the **implicit time-expanded network** (CSR topology + the
  schedule), with **per-(lane, slot) residual capacity** mutated as flow
  units are committed.
- Guided by a **capacity-free destination profile** (backward connection-scan
  oracle) used as an **admissible lower bound** — states that cannot make the
  deadline even capacity-free are pruned.
- **Slack-ordered successive-shortest-path**: tightest `(due − capacity-free
  arrival)` first. A flow unit is committed only when a capacity-feasible
  completion to its deadline exists end-to-end.

`optimize/static.py :: StaticPlanner` is the **Dijkstra** baseline — ignores
schedule, capacity, and congestion. It always returns a path on a connected
graph; it is the "what does a capacity-blind router do" control, not the
product planner.

## How downstream attaches

A downstream app pins `epure-arena` at a fixed tag, maps its entities onto the
wire protocol (`docs/protocol.md`), and **registers its own planners into
`optimize.registry.default_registry`** — the one sanctioned extension seam.
It inherits determinism, attribution, certificates, and paired
counterfactuals without modifying core. See the companion
`ARCHITECTURE.md` in the downstream app for a worked instance.
