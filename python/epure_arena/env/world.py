"""The constrained world: suites, the (cost, budget) objective, the oracle.

A *suite* is one disruption scenario (world, scenario_seed, severity,
disruption_seed) with its epochs. From the pinned loop we measure, per
suite:
  * ``v_never`` — realized on-time with no intervention.
  * ``delta[i]`` — the per-epoch paired value Δ_i = v_of({i}) − v_never.
  * ``ctx[i]`` — the per-epoch decision context the loop traces on the
    never-leg (severity proxy, dry-run value estimates Δ̂_A / Δ̂_B).
  * ``v_of(S)`` — the TRUE joint realized on-time for any intervention set
    S, by paired rollout (cached, ``mass_closed`` asserted).

The estimand (net of cost, on-time-equivalent units):
    V(S) = [v_of(S) − v_never] − cost · |S|
The oracle under (cost, budget B) is argmax over |S| ≤ B of V(S) — the
ceiling, computed by paired rollout. Budgeted regret of a policy is
V(S*) − V(S_policy).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from epure_arena.harness.live_loop import run_live_loop
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario

_H = 3_600_000
KINDS = ("lane_outage", "node_outage", "capacity_shock", "demand_surge")
WORLDS = {
    # topology is the studied axis; density held at "moderate" for all so the
    # sub-additivity premium varies with topological sparsity, not density.
    "backbone": dict(topology="backbone", density="moderate"),
    "mesh": dict(topology="mesh", density="moderate"),
    "grid": dict(topology="grid", density="moderate"),
    "tree": dict(topology="tree", density="moderate"),
}


def bundle_for(world: str, scenario_seed: int, demand: int = 40_000):
    """C2-identical world generation (verbatim params), via the pin."""
    w = WORLDS[world]
    return generate_scenario(ScenarioSpec(
        topology=w["topology"], n_nodes=200, density=w["density"],
        capacity="tight", demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=demand, horizon_hours=24,
        seed=scenario_seed,
    ))


def realized_on_time(flows: dict[str, int]) -> int:
    return sum(v for k, v in flows.items() if k.endswith("_on_time"))


@dataclass
class Suite:
    """One disruption suite with its loop measurements + a cached set evaluator."""

    world: str
    scenario_seed: int
    severity: str
    disruption_seed: int
    n_epochs: int
    v_never: int
    delta: list[int]                       # Δ_i = v_of({i}) − v_never
    sev_score: list[int]                   # severity proxy s_i
    dhat_a: list[float]                    # Δ̂_A = value_estimate
    dhat_b: list[float]                    # Δ̂_B = n_recertifiable
    kind: list[str]                        # per-epoch disruption kind
    digest_never: str
    _bundle: Any = field(repr=False, default=None)
    _ds: Any = field(repr=False, default=None)
    _cache: dict[frozenset, int] = field(repr=False, default_factory=dict)

    def v_of(self, s) -> int:
        """True joint realized on-time for intervention set ``s`` (cached)."""
        key = frozenset(s)
        if key not in self._cache:
            leg = run_live_loop(self._bundle, self._ds,
                                intervene_epochs=set(key), horizon_hours=24)
            assert leg.mass_closed, f"mass not closed for {sorted(key)}"
            self._cache[key] = realized_on_time(leg.flows)
        return self._cache[key]

    def cost_of(self, s, cost) -> float:
        """Total cost of set S. ``cost`` is a scalar (flat per intervention)
        or a per-epoch sequence (e.g. per-kind pegging)."""
        if isinstance(cost, (int, float)):
            return cost * len(frozenset(s))
        return sum(cost[i] for i in frozenset(s))

    def net(self, s, cost) -> float:
        """V(S) = (v_of(S) − v_never) − cost(S)."""
        s = frozenset(s)
        return (self.v_of(s) - self.v_never) - self.cost_of(s, cost)


def build_suite(world: str, scenario_seed: int, severity: str,
                disruption_seed: int, demand: int = 40_000,
                events_per_kind: int = 2) -> Suite:
    """Measure one suite: never-leg context + the per-epoch Δ legs."""
    b = bundle_for(world, scenario_seed, demand)
    ds = make_disruptions(b, kinds=KINDS, severity=severity, horizon_hours=24,
                          seed=disruption_seed, events_per_kind=events_per_kind)
    n = len(ds)
    never = run_live_loop(b, ds, intervene_epochs=set(), score_epochs=True,
                          horizon_hours=24)
    assert never.mass_closed
    vn = realized_on_time(never.flows)
    # epoch order matches the loop's internal sort (onset, kind)
    kinds = [d.kind for d in sorted(ds, key=lambda x: (x.onset_atu, x.kind))]
    delta, sev, da, db = [], [], [], []
    suite = Suite(world, scenario_seed, severity, disruption_seed, n, vn,
                  delta, sev, da, db, kinds, never.digest, b, ds)
    suite._cache[frozenset()] = vn
    for i in range(n):
        delta.append(suite.v_of({i}) - vn)
    for t in never.epoch_traces:
        sev.append(t.severity_score)
        da.append(t.value_estimate)
        db.append(float(t.n_recertifiable))
    return suite


# ── the oracle-SET and the ranking baselines under (cost, budget) ─────────
def oracle_set(suite: Suite, cost, budget: int) -> tuple[frozenset, float]:
    """EXACT argmax over |S| ≤ budget of V(S), by exhaustive paired rollout.

    budget == None means uncapped (|S| ≤ n). The empty set (never) is always
    a candidate, so the oracle net is ≥ 0. Exhaustive — only tractable for
    small n (≤ ~10 epochs); use ``oracle_set_greedy`` beyond that.
    """
    n = suite.n_epochs
    cap = n if budget is None else min(budget, n)
    best_s, best_v = frozenset(), 0.0
    for k in range(cap + 1):
        for combo in itertools.combinations(range(n), k):
            v = suite.net(combo, cost)
            if v > best_v:
                best_v, best_s = v, frozenset(combo)
    return best_s, best_v


def oracle_set_greedy(suite: Suite, cost, budget: int) -> tuple[frozenset, float]:
    """Greedy marginal-gain oracle: add the epoch with the largest positive
    marginal net gain until none remains or the budget is spent.

    For sub-additive (submodular-like) interventions this is the standard
    tractable ceiling — within (1 − 1/e) of exact for submodular V, and
    typically exact in practice. O(budget · n) rollouts. Validate against
    ``oracle_set`` on small suites to bound the approximation.
    """
    n = suite.n_epochs
    cap = n if budget is None else min(budget, n)
    sel: set[int] = set()
    cur = 0.0  # net of the current set
    while len(sel) < cap:
        best_i, best_gain = None, 0.0
        for i in range(n):
            if i in sel:
                continue
            gain = suite.net(sel | {i}, cost) - cur
            if gain > best_gain:
                best_gain, best_i = gain, i
        if best_i is None:  # no positive marginal gain remains
            break
        sel.add(best_i)
        cur = suite.net(sel, cost)
    return frozenset(sel), cur


def _cost_i(cost, i: int) -> float:
    return cost if isinstance(cost, (int, float)) else cost[i]


def _ranked_policy(suite: Suite, scores: list[float], cost,
                   budget: int, *, cost_aware: bool) -> tuple[frozenset, float]:
    """Greedy budget-respecting selection by descending score.

    ``cost_aware`` policies skip epochs whose score (in value units) is at or
    below that epoch's cost (the act-iff-worth-it rule); severity-ranking is
    not cost-aware (no value unit) and simply spends its budget on the
    highest stress. Realized value uses the TRUE joint ``v_of``.
    """
    n = suite.n_epochs
    cap = n if budget is None else min(budget, n)
    order = sorted(range(n), key=lambda i: (-scores[i], i))
    sel: list[int] = []
    for i in order:
        if len(sel) >= cap:
            break
        if cost_aware and scores[i] <= _cost_i(cost, i):
            continue  # this epoch isn't worth its cost; try the next
        sel.append(i)
    return frozenset(sel), suite.net(sel, cost)


def baselines(suite: Suite, cost: float, budget: int) -> dict[str, tuple]:
    """All baseline policies' (set, net) under (cost, budget)."""
    n = suite.n_epochs
    out: dict[str, tuple] = {}
    out["never"] = (frozenset(), 0.0)
    cap = n if budget is None else min(budget, n)
    # always = spend full budget on the highest-Δ̂ epochs (cost-blind);
    # uncapped 'always' is every epoch.
    always_sel = frozenset(sorted(range(n), key=lambda i: (-suite.dhat_b[i], i))[:cap])
    out["always"] = (always_sel, suite.net(always_sel, cost))
    out["severity"] = _ranked_policy(suite, [float(s) for s in suite.sev_score],
                                     cost, budget, cost_aware=False)
    out["value_A"] = _ranked_policy(suite, suite.dhat_a, cost, budget,
                                    cost_aware=True)
    out["value_B"] = _ranked_policy(suite, suite.dhat_b, cost, budget,
                                    cost_aware=True)
    out["oracle_rank"] = _ranked_policy(suite, [float(d) for d in suite.delta],
                                        cost, budget, cost_aware=True)
    return out


def additivity_gap(suite: Suite, s) -> float:
    """v_of(S) − v_never − Σ_{i∈S} Δ_i  (0 = additive; <0 = sub-additive)."""
    s = frozenset(s)
    return (suite.v_of(s) - suite.v_never) - sum(suite.delta[i] for i in s)


# ── the bottleneck-disjoint count d (independent premium predictor) ────────
def pair_competes(suite: Suite, i: int, j: int, tau: float = 0.15) -> bool:
    """Two high-value interventions COMPETE if their joint value falls more
    than ``tau`` below the sum of their individual Δ (sub-additive pair)."""
    joint = suite.v_of({i, j}) - suite.v_never
    return joint < (1.0 - tau) * (suite.delta[i] + suite.delta[j])


def disjoint_count(suite: Suite, peg: float, tau: float = 0.15) -> tuple[int, list[int]]:
    """d = the count of bottleneck-disjoint high-value interventions.

    High-value set H = {i : Δ_i ≥ peg}. Build the competition graph on H
    (edge = competing pair) and return the size of its MAXIMUM INDEPENDENT
    SET — the largest mutually-non-competing subset. Measured WITHOUT any
    reference to the set>rank premium (an independent predictor). H is tiny,
    so brute-force the MIS.
    """
    import itertools

    H = [i for i in range(suite.n_epochs) if suite.delta[i] >= peg]
    if not H:
        return 0, H
    competes = {(i, j): pair_competes(suite, i, j, tau)
                for i, j in itertools.combinations(H, 2)}

    def independent(subset) -> bool:
        return all(not competes[(i, j)]
                   for i, j in itertools.combinations(sorted(subset), 2))

    best = 0
    for k in range(len(H), 0, -1):
        if any(independent(c) for c in itertools.combinations(H, k)):
            best = k
            break
    return best, H


def ranking_additive_gap(suite: Suite, budget: int) -> tuple[float, frozenset]:
    """G(B) = Σ_{i∈top-B-by-Δ} Δ_i − (v_of(top-B) − v_never).

    The FULL-SET (higher-order) sub-additivity of greedy value-ranking's own
    selection at budget B — how far the ranking's joint realized value falls
    below its additive expectation. Computed from the top-B-by-Δ set only,
    independent of the oracle-set search. G ≈ 0 ⇒ the ranking's pick is
    additive ⇒ no premium is recoverable; G > 0 is the necessary room.
    """
    n = suite.n_epochs
    cap = n if budget is None else min(budget, n)
    topB = sorted(range(n), key=lambda i: (-suite.delta[i], i))[:cap]
    additive = sum(suite.delta[i] for i in topB)
    joint = suite.v_of(topB) - suite.v_never
    return additive - joint, frozenset(topB)
