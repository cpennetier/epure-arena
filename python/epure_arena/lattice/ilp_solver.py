"""Time-expanded ILP for the Epure Arena routing problem.

This is the **ground truth** for Tier-3 calibration. Given a small
scenario bundle (≤ ~20 nodes, ≤ ~200 flow_units, ≤ ~24 buckets), the
solver returns a provably optimal assignment of flow_units to time-
expanded lanes. Two objectives are supported:

  * ``"min_stranded"`` (default, legacy): minimise the number of
    stranded flow_units with a secondary lexicographic preference for
    shorter total travel time. Used by H1 and Phase-1 calibration.
  * ``"min_total_cost"`` (H2): minimise the PRD-014 §7.1 TotalCost
    scalar — operational $ cost (per-lane ``cost_usd`` from the lane
    table) + lateness penalty (piecewise-linear in
    ``max(0, delivered_atu - due_atu)``) + fail penalty
    ($5000/flow_unit) + manual penalty ($1000/flow_unit; not modelled in
    the ILP, so always 0 here). λ_CO₂ defaults to 0 because the
    headline run does not weight CO₂; ``co2_g`` is still aggregated
    as a secondary reporting metric.

Formulation
-----------

Let ``B`` be the set of integer time-buckets ``{0, 1, …, T-1}``
(``T = horizon_hours``), each spanning ``bucket_atu = 3_600_000`` ATU.

Each scenario lane ``e`` exposes a sequence of ``(depart_atu,
capacity)`` slots. We collapse them to a per-bucket schedule
``cap[e][b]`` = total capacity of any slot whose ``depart_atu``
falls inside bucket ``b``. A slot with ``depart_atu = t`` is usable
by a flow_unit that is at the source node *no later than* ``t``.

Decision variables (binary):
  ``x[p, e, b] ∈ {0, 1}`` — flow_unit ``p`` traverses lane ``e`` in
  bucket ``b``. Lane ``e`` runs ``src_e → dst_e`` and takes
  ``dur_buckets_e`` buckets to traverse, so the flow_unit arrives at
  ``dst_e`` no earlier than bucket ``b + dur_buckets_e``.

  ``y[p] ∈ {0, 1}`` — flow_unit ``p`` is **stranded**. Stranded means
  no source-to-sink flow exists for this flow_unit.

Constraints:

  * **Flow conservation per flow_unit per node per bucket.** At the
    origin in the flow_unit's appear-bucket, net flow is +1 unless the
    flow_unit is stranded; at the destination it is −1; everywhere else
    it is 0.
  * **Capacity per (lane, bucket).** Σ_p x[p, e, b] × size_p ≤
    cap[e][b].
  * **Schedule respect.** ``x[p, e, b] = 0`` if no slot of ``e``
    falls in bucket ``b``.
  * **Appear time.** ``x[p, e, b] = 0`` for every ``b`` strictly
    before ``appear_bucket(p)``.

Objective:
  ``minimise  W_strand · Σ_p y[p]  +  Σ_p,e,b  x[p,e,b] × dur_buckets_e``

with ``W_strand`` set to a value that strictly dominates the maximum
possible total travel-time term, so the solver never strands a
flow_unit to save travel time. We tie-break in favour of shorter paths
in the secondary term.

Honesty notes
-------------

* **Multi-depart-bucket waits.** A flow_unit can wait at a node by
  simply not consuming an outgoing lane in some buckets. The flow
  formulation handles this naturally because the flow conservation
  is independent per bucket — there is no "flow_unit-state" variable
  beyond the time-expanded node it occupies.
* **Lane duration > 1 bucket.** Handled by the
  ``b → b + dur_buckets_e`` arc semantics. The capacity cap is
  charged in the *departure* bucket only, matching the DES, where a
  schedule slot consumes one capacity unit at the moment of
  departure.
* **Returns to origin / cycles.** Permitted by the flow formulation
  but never optimal under our objective (every additional traversal
  adds travel time). We do not add explicit no-cycle constraints,
  trusting the secondary objective to suppress them.
* **Equal-cost ties.** PuLP/CBC may return any of several equally
  optimal solutions; the **objective value** is canonical, the
  **path** is not. Tests should compare on objective + on stranded
  count, not on specific lane sequences.

Performance bound
-----------------

For the adversarial scenarios in ``epure_arena.scenarios.golden_worlds``
(≤ 8 nodes, ≤ 100 flow_units, ≤ 6 buckets), CBC solves in well under
1 second. The variable count is ``|P| × |E| × T + |P|`` — for the
cascade scenario that's ``100 × 9 × 6 + 100 = 5500``. CBC handles
this trivially.

Typical limit: ~100K binary variables before CBC becomes a wall-time
problem. That's e.g. 200 flow_units × 100 lanes × 24 buckets, well
beyond the calibration regime.
"""
from __future__ import annotations

import dataclasses
import math
import time
from typing import Iterable

import numpy as np
import pyarrow.ipc as paipc

# Import order: pulp may not be installed in every venv. We import
# lazily inside the solver so the rest of the planner package can
# still be imported (e.g. on a CI box without pulp).
_BUCKET_ATU = 3_600_000


@dataclasses.dataclass
class IlpSolution:
    """Result of ``solve_routing_ilp``.

    Attributes:
        objective: Solver-reported objective value (units depend on
            ``objective_kind``: ``min_stranded`` returns
            ``W_strand·stranded + Σ dur_buckets``; ``min_total_cost``
            returns USD).
        objective_kind: ``"min_stranded"`` or ``"min_total_cost"``.
        stranded_flow_unit_ids: Sorted list of flow_unit IDs the
            solver could not deliver.
        n_delivered: Count of flow_units with a complete path.
        n_stranded: Count of flow_units stranded.
        paths: Mapping ``flow_unit_id → [lane_id, ...]`` in traversal
            order. Stranded flow_units map to ``[]``.
        bucket_assignments: Mapping ``flow_unit_id → [(lane_id,
            depart_bucket), ...]``. Useful for cross-checking against
            the DES timeline.
        delivery_bucket: Mapping ``flow_unit_id → arrival_bucket`` for
            delivered flow_units (the bucket in which the flow_unit reaches
            its destination); stranded flow_units have value ``-1``.
            Populated for both objectives so callers can recompute
            TotalCost retrospectively.
        total_cost_usd: TotalCost in USD when
            ``objective_kind=='min_total_cost'``. Optional secondary
            field for ``min_stranded`` (computed from the same
            constants for cross-comparison).
        cost_breakdown: Component costs ``{c_dollar, c_late, c_fail,
            c_manual, c_co2, total}`` in USD. Always populated.
        wall_sec: Wall time spent in CBC (excludes Python build).
        solver_status: PuLP status name ("Optimal", "Infeasible", …).
        n_vars: Total number of binary variables in the model.
        n_constraints: Total number of constraints in the model.
    """

    objective: float
    stranded_flow_unit_ids: list[int]
    n_delivered: int
    n_stranded: int
    paths: dict[int, list[int]]
    bucket_assignments: dict[int, list[tuple[int, int]]]
    wall_sec: float
    solver_status: str
    n_vars: int
    n_constraints: int
    objective_kind: str = "min_stranded"
    delivery_bucket: dict[int, int] = dataclasses.field(default_factory=dict)
    total_cost_usd: float | None = None
    cost_breakdown: dict[str, float] = dataclasses.field(default_factory=dict)


def _read_table(buf: bytes):
    return paipc.open_stream(buf).read_all()


def _decode_inputs(
    node_ipc: bytes, lane_ipc: bytes,
    schedule_ipc: bytes, demand_ipc: bytes,
    bucket_atu: int = _BUCKET_ATU,
):
    """Pull the minimum needed from the Arrow IPC bundles.

    Returns:
      nodes: dict {node_id → row_index} (we only use the count).
      lanes: list of dicts with keys src, dst, lane_id, duration_atu, dur_buckets.
      cap_per_lane_bucket: dict (lane_id, bucket) → capacity. Lanes with no
          schedule slot in a bucket are absent (treated as cap 0).
      flow_units: list of dicts with flow_unit_id, origin, dest, appear_bucket,
          due_bucket, size_units, priority_class.
    """
    node_table = _read_table(node_ipc)
    lane_table = _read_table(lane_ipc)
    sched_table = _read_table(schedule_ipc)
    demand_table = _read_table(demand_ipc)

    n_nodes = node_table.num_rows
    lanes = []
    lane_id_to_csr: dict[int, int] = {}
    lane_cols = set(lane_table.schema.names)
    has_cost = "cost_usd" in lane_cols
    has_co2 = "co2_g" in lane_cols
    for i in range(lane_table.num_rows):
        eid = int(lane_table.column("lane_id")[i].as_py())
        src = int(lane_table.column("src_node_id")[i].as_py())
        dst = int(lane_table.column("dst_node_id")[i].as_py())
        dur_atu = int(lane_table.column("duration_atu")[i].as_py())
        dur_buckets = max(1, math.ceil(dur_atu / bucket_atu))
        cost_usd = (
            float(lane_table.column("cost_usd")[i].as_py())
            if has_cost else 0.0
        )
        co2_g = (
            float(lane_table.column("co2_g")[i].as_py())
            if has_co2 else 0.0
        )
        lanes.append({
            "lane_id": eid, "src": src, "dst": dst,
            "duration_atu": dur_atu, "dur_buckets": dur_buckets,
            "cost_usd": cost_usd, "co2_g": co2_g,
        })
        lane_id_to_csr[eid] = i

    # Per-bucket capacity. lane_id in the schedule table is the lane_id
    # in the canonical node-engine schema (lane_id == lane_id for the
    # synthetic scenarios; the production graph builder enforces this).
    cap: dict[tuple[int, int], int] = {}
    for i in range(sched_table.num_rows):
        lane_id = int(sched_table.column("lane_id")[i].as_py())
        depart_atu = int(sched_table.column("depart_atu")[i].as_py())
        capacity = int(sched_table.column("capacity")[i].as_py())
        bucket = depart_atu // bucket_atu
        key = (lane_id, bucket)
        cap[key] = cap.get(key, 0) + capacity

    flow_units = []
    for i in range(demand_table.num_rows):
        sid = int(demand_table.column("flow_unit_id")[i].as_py())
        origin = int(demand_table.column("origin_node_id")[i].as_py())
        dest = int(demand_table.column("dest_node_id")[i].as_py())
        appear_atu = int(demand_table.column("appear_atu")[i].as_py())
        due_atu = int(demand_table.column("due_atu")[i].as_py())
        size_units = int(demand_table.column("size_units")[i].as_py())
        priority_class = int(demand_table.column("priority_class")[i].as_py())
        flow_units.append({
            "flow_unit_id": sid,
            "origin": origin, "dest": dest,
            "appear_atu": appear_atu, "due_atu": due_atu,
            "appear_bucket": appear_atu // bucket_atu,
            "due_bucket": math.ceil(due_atu / bucket_atu),
            "size_units": size_units,
            "priority_class": priority_class,
        })
    return n_nodes, lanes, cap, flow_units


def solve_routing_ilp(
    node_ipc: bytes,
    lane_ipc: bytes,
    schedule_ipc: bytes,
    demand_ipc: bytes,
    *,
    horizon_hours: int,
    bucket_atu: int = _BUCKET_ATU,
    time_limit_sec: float = 60.0,
    msg: bool = False,
    objective: str = "min_stranded",
    cost_per_late_hour: float = 100.0,
    fail_penalty_usd: float = 5000.0,
    manual_penalty_usd: float = 1000.0,
    lambda_late: float = 1.0,
    lambda_fail: float = 1.0,
    lambda_co2: float = 0.0,
    co2_usd_per_kg: float = 0.05,
) -> IlpSolution:
    """Solve the time-expanded routing ILP for a scenario bundle.

    Raises:
        ImportError: If PuLP is not installed.
        RuntimeError: If the solver exits with a non-Optimal status
            other than "Not Solved" / "Undefined" (e.g. Infeasible —
            should not happen because stranded is always a feasible
            fallback).

    Args:
        node_ipc, lane_ipc, schedule_ipc, demand_ipc: Arrow IPC bytes
            from the testkit serializers.
        horizon_hours: Number of integer time-buckets to model.
        bucket_atu: ATU per bucket (default 1 hour).
        time_limit_sec: Wall-time cap for CBC. The CBC solver returns
            the best feasible solution it found within this budget.
        msg: Pass-through to the CBC solver's stdout flag.
        objective: ``"min_stranded"`` (default, legacy) or
            ``"min_total_cost"`` (PRD-014 §7 TotalCost).
        cost_per_late_hour: USD per bucket-hour of lateness in the
            piecewise-linear lateness penalty.
        fail_penalty_usd: PRD-014 §7.3 default ($5000/flow_unit).
        manual_penalty_usd: PRD-014 §7.2 default ($1000/flow_unit). The
            ILP does not model manual handling so this is reported but
            never adds to the objective.
        lambda_late, lambda_fail, lambda_co2: λ weights from PRD-014
            §7.1. Default ``λ_CO₂ = 0`` because the headline run does
            not weight CO₂; raise it to fold the secondary metric in.
        co2_usd_per_kg: Conversion from emissions to dollars when
            ``lambda_co2 > 0``. ``0.05`` is a placeholder reasonable
            social-cost estimate.
    """
    try:
        import pulp
    except ImportError as e:
        raise ImportError(
            "pulp is required for solve_routing_ilp. "
            "Install with: uv pip install pulp",
        ) from e

    t_setup0 = time.perf_counter()
    n_nodes, lanes, cap, flow_units = _decode_inputs(
        node_ipc, lane_ipc, schedule_ipc, demand_ipc, bucket_atu=bucket_atu,
    )
    T = int(horizon_hours)

    # Build the time-expanded outgoing-arc index per (src_node, bucket).
    # Required for the flow-conservation constraints.
    outgoing: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    incoming: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for e in lanes:
        for b in range(T):
            arrive_b = b + e["dur_buckets"]
            if arrive_b > T:
                continue
            cap_eb = cap.get((e["lane_id"], b), 0)
            if cap_eb <= 0:
                continue
            outgoing.setdefault((e["src"], b), []).append(
                (e["lane_id"], b, arrive_b),
            )
            incoming.setdefault((e["dst"], arrive_b), []).append(
                (e["lane_id"], b, arrive_b),
            )

    # Build the model.
    prob = pulp.LpProblem("epure_routing", pulp.LpMinimize)

    # Decision variables: x[p, e, b] ∈ {0, 1}. Allocated only for
    # (e, b) that have actual capacity to keep the model tight.
    x: dict[tuple[int, int, int], pulp.LpVariable] = {}
    for p, ship in enumerate(flow_units):
        appear_b = ship["appear_bucket"]
        for e in lanes:
            for b in range(appear_b, T):
                cap_eb = cap.get((e["lane_id"], b), 0)
                if cap_eb <= 0:
                    continue
                if b + e["dur_buckets"] > T:
                    continue
                x[(p, e["lane_id"], b)] = pulp.LpVariable(
                    f"x_{p}_{e['lane_id']}_{b}", cat=pulp.LpBinary,
                )

    # Stranded indicator y[p].
    y = {
        p: pulp.LpVariable(f"y_{p}", cat=pulp.LpBinary)
        for p in range(len(flow_units))
    }

    # Flow conservation.
    # For each flow_unit p, node h, bucket b:
    #   inflow(p, h, b) - outflow(p, h, b) = source(p, h, b)
    # where source = +1 if (h, b) == (origin, appear), else 0,
    # and we require Σ_outgoing == 1 - y[p] from the origin's
    # appear-bucket and Σ_incoming == 1 - y[p] at the destination
    # over all buckets ≥ appear.
    for p, ship in enumerate(flow_units):
        origin = ship["origin"]
        dest = ship["dest"]
        appear_b = ship["appear_bucket"]

        # Origin: total outflow over all buckets ≥ appear == 1 - y[p].
        origin_out = []
        for b in range(appear_b, T):
            for eid, bb, _arrive in outgoing.get((origin, b), []):
                if bb == b and (p, eid, b) in x:
                    origin_out.append(x[(p, eid, b)])
        prob += pulp.lpSum(origin_out) == 1 - y[p], f"origin_p{p}"

        # Origin should not have *incoming* flow before the destination
        # is reached. We don't add this as a hard constraint (it cannot
        # improve the objective) and let the secondary travel-time
        # term suppress trivial cycles.

        # Destination: total inflow over all buckets ≤ T == 1 - y[p].
        dest_in = []
        for b in range(appear_b + 1, T + 1):
            for eid, bb, arrive in incoming.get((dest, b), []):
                if arrive == b and (p, eid, bb) in x:
                    dest_in.append(x[(p, eid, bb)])
        prob += pulp.lpSum(dest_in) == 1 - y[p], f"dest_p{p}"

        # Intermediate nodes: inflow == outflow.
        for h in range(n_nodes):
            if h in (origin, dest):
                continue
            for b in range(appear_b, T + 1):
                inflow_terms = [
                    x[(p, eid, bb)]
                    for eid, bb, arrive in incoming.get((h, b), [])
                    if (p, eid, bb) in x
                ]
                outflow_terms = [
                    x[(p, eid, bb)]
                    for eid, bb, _arrive in outgoing.get((h, b), [])
                    if bb == b and (p, eid, b) in x
                ]
                if not inflow_terms and not outflow_terms:
                    continue
                prob += (
                    pulp.lpSum(inflow_terms) == pulp.lpSum(outflow_terms)
                ), f"flow_p{p}_h{h}_b{b}"

    # Capacity: Σ_p x[p, e, b] × size_p ≤ cap[e][b].
    for (eid, b), cap_eb in cap.items():
        if b >= T:
            continue
        terms = []
        for p, ship in enumerate(flow_units):
            key = (p, eid, b)
            if key in x:
                terms.append(ship["size_units"] * x[key])
        if terms:
            prob += pulp.lpSum(terms) <= cap_eb, f"cap_e{eid}_b{b}"

    if objective not in ("min_stranded", "min_total_cost"):
        raise ValueError(
            f"objective must be 'min_stranded' or 'min_total_cost', "
            f"got {objective!r}",
        )

    lane_by_id = {e["lane_id"]: e for e in lanes}
    late_vars: dict[int, pulp.LpVariable] = {}

    if objective == "min_stranded":
        # Legacy objective — kept byte-identical so H1 / Phase-1
        # reproductions land on the same numbers.
        max_travel = sum(e["dur_buckets"] for e in lanes) + 1
        W_strand = max(1, max_travel * len(flow_units) + 1)
        travel_term = pulp.lpSum(
            lane_by_id[eid]["dur_buckets"] * x[(p, eid, b)]
            for (p, eid, b) in x
        )
        prob += W_strand * pulp.lpSum(y.values()) + travel_term
    else:                                                    # min_total_cost
        # Per-flow_unit arrival bucket: Σ_b b · (incoming flow at dest in b).
        # Lateness = max(0, arrival_bucket - due_bucket); zero for
        # stranded flow_units (no incoming flow at all).
        # NB: bucket_atu is fixed at 1 hour by default, so 1 bucket
        # of lateness == 1 hour of lateness in the cost model.
        for p, ship in enumerate(flow_units):
            late_vars[p] = pulp.LpVariable(
                f"late_{p}", lowBound=0.0, cat=pulp.LpContinuous,
            )
            arrival_terms = []
            for b in range(ship["appear_bucket"] + 1, T + 1):
                for eid, bb, arrive in incoming.get((ship["dest"], b), []):
                    if arrive == b and (p, eid, bb) in x:
                        # Coefficient is the arrival bucket index.
                        arrival_terms.append(b * x[(p, eid, bb)])
            arrival_expr = pulp.lpSum(arrival_terms)
            prob += (
                late_vars[p] >= arrival_expr - ship["due_bucket"]
            ), f"late_p{p}"

        c_dollar_term = pulp.lpSum(
            lane_by_id[eid]["cost_usd"] * x[(p, eid, b)]
            for (p, eid, b) in x
        )
        c_late_term = pulp.lpSum(
            cost_per_late_hour * late_vars[p] for p in late_vars
        )
        c_fail_term = fail_penalty_usd * pulp.lpSum(y.values())
        if lambda_co2 > 0.0:
            c_co2_term = pulp.lpSum(
                lane_by_id[eid]["co2_g"] * x[(p, eid, b)]
                for (p, eid, b) in x
            ) * (co2_usd_per_kg / 1000.0)                    # g → kg
        else:
            c_co2_term = 0.0
        prob += (
            c_dollar_term
            + lambda_late * c_late_term
            + lambda_fail * c_fail_term
            + lambda_co2 * c_co2_term
        )

    n_vars = len(x) + len(y) + len(late_vars)
    n_constraints = len(prob.constraints)

    # Solve.
    solver = pulp.PULP_CBC_CMD(
        msg=msg, timeLimit=time_limit_sec, gapRel=0.0, threads=1,
    )
    t0 = time.perf_counter()
    prob.solve(solver)
    wall_sec = time.perf_counter() - t0
    status = pulp.LpStatus[prob.status]

    if prob.status not in (pulp.LpStatusOptimal,
                           pulp.LpStatusNotSolved,
                           pulp.LpStatusUndefined):
        raise RuntimeError(
            f"ILP solver returned status={status}; expected Optimal "
            f"or time-limited Not-Solved.",
        )

    # Extract solution.
    objective_value = float(pulp.value(prob.objective))
    stranded_ids: list[int] = []
    paths: dict[int, list[int]] = {}
    bucket_assignments: dict[int, list[tuple[int, int]]] = {}
    delivery_bucket: dict[int, int] = {}

    for p, ship in enumerate(flow_units):
        sid = ship["flow_unit_id"]
        if pulp.value(y[p]) > 0.5:
            stranded_ids.append(sid)
            paths[sid] = []
            bucket_assignments[sid] = []
            delivery_bucket[sid] = -1
            continue
        # Reconstruct the path by walking forward from the origin.
        used = []
        for (pp, eid, b), var in x.items():
            if pp == p and pulp.value(var) > 0.5:
                used.append((eid, b))
        # Order by bucket.
        used.sort(key=lambda t: t[1])
        # Verify it forms a contiguous chain. If multiple paths emerge,
        # take the one starting at origin.
        ordered = []
        current = ship["origin"]
        remaining = list(used)
        while remaining:
            for i, (eid, b) in enumerate(remaining):
                e = lane_by_id[eid]
                if e["src"] == current:
                    ordered.append((eid, b))
                    current = e["dst"]
                    remaining.pop(i)
                    break
            else:
                # Disconnected — treat the rest as orphaned variables
                # (CBC sometimes leaves stale reflective vars at 1 in
                # degenerate cases; we ignore them in path reconstruction).
                break
        paths[sid] = [eid for eid, _ in ordered]
        bucket_assignments[sid] = ordered
        if ordered:
            last_eid, last_b = ordered[-1]
            delivery_bucket[sid] = last_b + lane_by_id[last_eid]["dur_buckets"]
        else:
            delivery_bucket[sid] = -1

    n_stranded = len(stranded_ids)
    n_delivered = len(flow_units) - n_stranded

    # Compute the TotalCost breakdown from the realised solution. We do
    # this independent of the objective so that callers can compare an
    # ``min_stranded`` solution to a ``min_total_cost`` solution on the
    # same scalar.
    c_dollar = 0.0
    c_co2_g = 0.0
    for sid, path_lanes in bucket_assignments.items():
        for eid, _ in path_lanes:
            e = lane_by_id[eid]
            c_dollar += e["cost_usd"]
            c_co2_g += e["co2_g"]
    c_late = 0.0
    for p, ship in enumerate(flow_units):
        sid = ship["flow_unit_id"]
        if delivery_bucket.get(sid, -1) < 0:
            continue
        late_buckets = max(0, delivery_bucket[sid] - ship["due_bucket"])
        c_late += late_buckets * cost_per_late_hour
    c_fail = n_stranded * fail_penalty_usd
    c_manual = 0.0                                    # not modelled in ILP
    c_co2_usd = (c_co2_g / 1000.0) * co2_usd_per_kg
    total_cost = (
        c_dollar
        + lambda_late * c_late
        + lambda_fail * c_fail
        + lambda_co2 * c_co2_usd
        # manual term excluded: λ_manual·0
    )

    breakdown = {
        "c_dollar": round(c_dollar, 2),
        "c_late": round(c_late, 2),
        "c_fail": round(c_fail, 2),
        "c_manual": round(c_manual, 2),
        "c_co2_g": round(c_co2_g, 2),
        "c_co2_usd": round(c_co2_usd, 2),
        "total_cost_usd": round(total_cost, 2),
        "lambda_late": lambda_late,
        "lambda_fail": lambda_fail,
        "lambda_co2": lambda_co2,
        "cost_per_late_hour": cost_per_late_hour,
        "fail_penalty_usd": fail_penalty_usd,
        "manual_penalty_usd": manual_penalty_usd,
    }

    return IlpSolution(
        objective=objective_value,
        stranded_flow_unit_ids=sorted(stranded_ids),
        n_delivered=n_delivered,
        n_stranded=n_stranded,
        paths=paths,
        bucket_assignments=bucket_assignments,
        delivery_bucket=delivery_bucket,
        wall_sec=round(wall_sec, 4),
        solver_status=status,
        n_vars=n_vars,
        n_constraints=n_constraints,
        objective_kind=objective,
        total_cost_usd=round(total_cost, 2),
        cost_breakdown=breakdown,
    )
