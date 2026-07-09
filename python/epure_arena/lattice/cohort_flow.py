"""Cohort-aggregated flow oracle: a flow_unit-count-INVARIANT upper bound on
on-time deliverable mass (Step 5 of the Optimize MVP plan).

The per-flow_unit-commodity LP (``lp_relaxed``) builds ~6.8k variables PER FLOW UNIT
and times out at 10³ flow_units (measured, plan report Appendix B). This module
replaces the formulation, not the pattern: demand enters ONLY through cohort
supply/budget vectors, so the LP's size depends on the WORLD
(|destinations| × |bucket-arcs|), not the flow_unit count — 40k and 1M flow_units
build the exact same LP, only the right-hand side changes.

Formulation (destination-commodity, due-class-budgeted bucket flow):
- Time-expanded at bucket granularity: nodes (node, bucket); travel arcs one
  per (lane, depart-bucket) with capacity = Σ slot capacities in the bucket;
  hold arcs (node, b) → (node, b+1) uncapacitated (the offline LP is the one
  sanctioned place for this coarsened expansion — plan report §6).
- One commodity per DESTINATION d. Cohort (origin, release-bucket, due-class)
  supplies enter at source nodes (origin, release-bucket); flow exits d via
  due-class sink arcs (d, b) → SINK_{db} allowed iff b ≤ db, with per-class
  budget Σ supplies of that class.
- Within a commodity the LP may re-assign due budgets across sources — an
  ASSIGNMENT RELAXATION, so the optimum is a valid UPPER bound on true
  on-time mass (any feasible routing induces a feasible LP flow).
- Supplies are pre-capped by the EXACT-TIME capacity-free oracle: flow_units the
  oracle marks infeasible-on-time contribute nothing (they cannot be on-time
  under any capacity). This enforces the certificate lattice
  ``oracle ⊇ cohort-LP ⊇ achieved`` by construction.

Solver: scipy HiGHS over sparse matrices. Main export:
:func:`solve_cohort_flow_upper_bound`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

_BUCKET_ATU = 3_600_000


@dataclass
class CohortFlowBound:
    """Result of the cohort-flow LP."""

    on_time_upper_bound: float  # LP optimum in CAPACITY UNITS (valid UB)
    n_flow_units: int  # total flow_units in demand
    n_oracle_on_time: int  # flow_units the exact-time oracle marks on-time
    n_commodities: int
    n_vars: int
    n_constraints: int
    wall_sec: float
    solver_status: str
    oracle_on_time_units: float = 0.0  # the same mass in UNITS (lattice top)

    @property
    def lattice_ok(self) -> bool:
        # The lattice is stated in UNITS end to end: the LP's flow, the
        # oracle cap, and achieved mass must share one currency (a sized
        # flow_unit contributes size_units everywhere or nowhere).
        return self.on_time_upper_bound <= self.oracle_on_time_units + 1e-6


def solve_cohort_flow_upper_bound(
    context,
    requests,
    oracle_on_time_ids: set[int],
    *,
    horizon_buckets: int,
    bucket_atu: int = _BUCKET_ATU,
    time_limit_sec: float = 600.0,
) -> CohortFlowBound:
    """Upper-bound the on-time deliverable mass for one scenario.

    Args:
        context: :class:`epure_arena.optimize.interface.PlanContext` (adjacency +
            ``schedule_departures``).
        requests: list of :class:`PlanRequest` (the full demand).
        oracle_on_time_ids: flow_unit_ids the exact-time capacity-free oracle
            classifies FEASIBLE_ON_TIME (lattice cap; others contribute 0).
        horizon_buckets: number of time buckets (horizon_hours for 1h buckets).
        bucket_atu: bucket width (default 1h).
        time_limit_sec: HiGHS time limit.

    Returns:
        :class:`CohortFlowBound` — ``on_time_upper_bound`` counts capacity
        units (== flow_units when size_units are 1; sized flow_units contribute
        their units).
    """
    from scipy import sparse
    from scipy.optimize import linprog

    t0 = time.perf_counter()
    n_nodes = context.n_nodes
    T = int(horizon_buckets)

    # ── arcs of the bucket-expanded network ──────────────────────────────
    # Travel arc a = (lane, depart-bucket): tail (u, b), head (v, b_arr),
    # capacity = Σ slot caps in bucket. Arrivals past T are dropped.
    lane_meta: dict[int, tuple[int, int, int]] = {}
    for u, adj in enumerate(context.adjacency):
        for v, eid, dur, _c, _co, _m in adj:
            lane_meta[eid] = (u, v, dur)

    arc_tail: list[int] = []  # node index = node * (T+1) + bucket
    arc_head: list[int] = []
    arc_cap: list[float] = []

    def node(h: int, b: int) -> int:
        return h * (T + 1) + b

    for eid, slots in context.schedule_departures.items():
        meta = lane_meta.get(eid)
        if meta is None:
            continue
        u, v, dur = meta
        per_bucket: dict[int, int] = {}
        for dep, cap in slots:
            b = dep // bucket_atu
            if 0 <= b <= T:
                per_bucket[b] = per_bucket.get(b, 0) + cap
        for b in sorted(per_bucket):
            # FLOOR the arrival bucket. Ceil would TIGHTEN (a true on-time
            # arrival could land past its floor-due bucket and be counted
            # late), breaking the upper-bound property. floor-arrival with
            # floor-due preserves every true on-time assignment:
            # a ≤ due ⇒ floor(a) ≤ floor(due). This also makes coarser
            # `bucket_atu` values a pure relaxation (travel rounds down,
            # release rounds down), so the LP trades tightness for speed.
            b_arr = (b * bucket_atu + dur) // bucket_atu
            if b_arr > T:
                continue
            arc_tail.append(node(u, b))
            arc_head.append(node(v, int(b_arr)))
            arc_cap.append(float(per_bucket[b]))

    n_travel = len(arc_tail)
    # Hold arcs (h, b) → (h, b+1), uncapacitated.
    for h in range(n_nodes):
        for b in range(T):
            arc_tail.append(node(h, b))
            arc_head.append(node(h, b + 1))
            arc_cap.append(np.inf)
    n_arcs = len(arc_tail)

    # ── cohort supplies and due-class budgets, oracle-capped ─────────────
    # supply[(d)][(o, rb)] += units ; budget[(d)][db] += units
    supplies: dict[int, dict[tuple[int, int], float]] = {}
    budgets: dict[int, dict[int, float]] = {}
    n_oracle_on_time = 0
    oracle_units = 0.0
    for r in requests:
        if r.flow_unit_id not in oracle_on_time_ids:
            continue
        n_oracle_on_time += 1
        oracle_units += float(r.size_units)
        rb = min(T, max(0, r.appear_atu // bucket_atu))
        db = min(T, r.due_atu // bucket_atu)
        units = float(r.size_units)
        supplies.setdefault(r.destination, {})
        budgets.setdefault(r.destination, {})
        key = (r.origin, int(rb))
        supplies[r.destination][key] = supplies[r.destination].get(key, 0.0) + units
        budgets[r.destination][int(db)] = budgets[r.destination].get(int(db), 0.0) + units

    dests = sorted(supplies.keys())
    K = len(dests)

    # ── variable layout ──────────────────────────────────────────────────
    # Per commodity k: arc flows x[k, a] (n_arcs each) plus one exit variable
    # per (k, b) — flow leaving the network at node (d, b). On-time assignment
    # of exits to due classes (exit bucket b must be ≤ the unit's due db) is
    # a bipartite matching whose Hall condition is the SUFFIX staircase:
    #   ∀t: Σ_{b ≥ t} exit[k, b] ≤ Σ_{db ≥ t} budget[k][db]
    # (exits at/after t can only consume due classes at/after t; the t = 0
    # case bounds the total). The prefix form is NOT sufficient — it lets an
    # early-due unit exit late, which the time_window_trap golden world
    # catches (UB 3 vs truth 1).
    n_exit_per_k = T + 1
    vars_per_k = n_arcs + n_exit_per_k
    n_vars = K * vars_per_k

    # Flow RELAXED conservation per (commodity, node), as ≤ (free disposal):
    #   Σ_out x + exit(d only) − Σ_in x ≤ supply(o, rb)
    # The ≤ keeps the LP feasible when some supply cannot exit on time
    # (the unroutable remainder is simply not routed); any true routing
    # still induces a feasible point, so the optimum stays a valid UB.
    iu_rows: list[int] = []
    iu_cols: list[int] = []
    iu_vals: list[float] = []
    rhs_ub: list[float] = []

    n_cons_rows = K * n_nodes * (T + 1)
    rhs_cons = np.zeros(n_cons_rows)
    for k, d in enumerate(dests):
        row0 = k * n_nodes * (T + 1)
        for (h, b), units in supplies[d].items():
            rhs_cons[row0 + node(h, b)] = units

    for k in range(K):
        base = k * vars_per_k
        row0 = k * n_nodes * (T + 1)
        for a in range(n_arcs):
            iu_rows.append(row0 + arc_tail[a])
            iu_cols.append(base + a)
            iu_vals.append(1.0)   # outflow from tail
            iu_rows.append(row0 + arc_head[a])
            iu_cols.append(base + a)
            iu_vals.append(-1.0)  # inflow to head
        d = dests[k]
        for b in range(T + 1):
            iu_rows.append(row0 + node(d, b))
            iu_cols.append(base + n_arcs + b)
            iu_vals.append(1.0)   # exit at (d, b)
    rhs_ub.extend(rhs_cons.tolist())
    n_ub = n_cons_rows
    for a in range(n_travel):  # hold arcs are uncapacitated
        for k in range(K):
            iu_rows.append(n_ub)
            iu_cols.append(k * vars_per_k + a)
            iu_vals.append(1.0)
        rhs_ub.append(arc_cap[a])
        n_ub += 1
    for k, d in enumerate(dests):
        base = k * vars_per_k + n_arcs
        bud = budgets[d]
        # suffix_budget(t) = Σ_{db ≥ t} budget[db]
        suffix = [0.0] * (T + 2)
        for t in range(T, -1, -1):
            suffix[t] = suffix[t + 1] + bud.get(t, 0.0)
        for t in range(T + 1):
            # Σ_{b ≥ t} exit[k, b] ≤ suffix_budget(t)
            for b in range(t, T + 1):
                iu_rows.append(n_ub)
                iu_cols.append(base + b)
                iu_vals.append(1.0)
            rhs_ub.append(suffix[t])
            n_ub += 1
    A_ub = sparse.coo_matrix(
        (iu_vals, (iu_rows, iu_cols)), shape=(n_ub, n_vars)
    ).tocsr()
    b_ub = np.array(rhs_ub)

    # Objective: maximize total exits ⇒ minimize −Σ exit.
    c = np.zeros(n_vars)
    for k in range(K):
        base = k * vars_per_k + n_arcs
        c[base: base + n_exit_per_k] = -1.0

    res = linprog(
        c, A_ub=A_ub, b_ub=b_ub,
        bounds=(0, None), method="highs-ipm",
        options={"time_limit": time_limit_sec},
    )
    wall = time.perf_counter() - t0
    ub = float(-res.fun) if res.status == 0 else float("nan")
    return CohortFlowBound(
        on_time_upper_bound=ub,
        n_flow_units=len(requests),
        n_oracle_on_time=n_oracle_on_time,
        oracle_on_time_units=oracle_units,
        n_commodities=K,
        n_vars=n_vars,
        n_constraints=n_ub,
        wall_sec=round(wall, 2),
        solver_status={0: "optimal", 1: "iteration_limit", 2: "infeasible",
                       3: "unbounded", 4: "numerical"}.get(res.status, str(res.status)),
    )
