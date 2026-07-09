"""Conservative wait-pricing routing ILP (Gate P1a) — a LOWER-bound oracle
in v_realized.

Distinct from ``ilp_solver.solve_routing_ilp`` (whose legacy objectives are
byte-frozen for H1 reproductions). This oracle:

TIMING (engine-exact boarding). ``appear_bucket = ceil(appear/H)`` — a unit
may first board the slot at ``b·H ≥ appear`` (the engine's ``depart ≥
ready``), never the legacy ``appear//H`` slot that departs before the unit
exists. Lane durations enter arrival times EXACTLY (ms), so claimed times
are engine times. ILP-feasible ⟹ engine-feasible.

STRUCTURE (surfaced ILL-POSED finding, resolved conservatively). The
formulation has per-(node,bucket) flow conservation and NO hold arcs: a
unit arriving at an intermediate node in bucket b departs in bucket b.
Mid-route inter-bucket waiting — which the ENGINE allows — is outside the
feasible region. The oracle therefore optimizes over a strict SUBSET of
engine-feasible schedules, so its optimum UNDER-states the true optimum:
conservative for a lower-bound oracle, and every measured regret is a
guaranteed-real lower bound on the true gap. Intra-bucket dwell (< 1
bucket per hop, admitted by the region) IS priced exactly, because held
time is a linear function of the arc variables:

    held_j = (arrival_at_dest − appear_j) − Σ_path dur_ms
           = Σ_{arcs into dest}(b·H + dur_e)·x − appear_j·(1−y_j)
             − Σ_{all arcs} dur_e·x .

WAIT PRICING (secant / inner PWL — conservative). C_wait(w) = v·(a·w+b·w²)
is convex; the objective charges its CHORD envelope over breakpoints
spanning [0, horizon] (chords of a convex function OVER-state the cost ⇒
the ILP UNDER-states value ⇒ the reported optimum cannot inflate a gap).
The chord PWL of a convex function is itself convex, hence exactly
``max_i`` of its affine pieces — modelled as ``cwait_j ≥ α_i·held_j + β_i``
with minimization pressure making it tight. Lateness: linear mode is
charged UNCAPPED (≥ the true capped ℓ — conservative); hard mode is exact
via a big-M indicator. The claimed value is RECOMPUTED from exact pinned
slot times with the TRUE convex functional (``epure_arena.pricing`` — the
same single source the engine-side report uses), and the PWL gap is
reported.

Pricing consumes HELD time exclusively (Gate-0a item 6).

SOLVER BACKEND. HiGHS (highspy, declared in the ``oracle`` extra) is the
certified backend. CBC's presolve returns FALSE INFEASIBILITY on the
hard-tolerance big-M lateness constraints — proven by exhibiting a HiGHS
solution externally verified feasible in a region CBC certified empty
(oracle-integrity diagnosis, 2026-07-03) — so a missing HiGHS fails
loudly and ``solver_backend="cbc"`` is refused on hard-tolerance models.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from epure_arena.pricing import WaitCostProfile, late_loss, unit_value, wait_cost

from .ilp_solver import _decode_inputs

_H = 3_600_000


@dataclass
class WaitIlpSolution:
    """Solver output. ``claimed_v_realized`` is the TRUE-functional value of
    the chosen configuration at exact times (what the round trip compares
    against the engine); ``objective_v_realized`` is the chord-priced ILP
    optimum (``≤ claimed_v_realized`` — conservatism); ``certified`` is
    True only when CBC proved optimality (else the value is still a valid
    feasible lower bound, just not the subset-optimum)."""

    objective_v_realized: float
    claimed_v_realized: float
    pwl_gap: float                      # claimed − objective ≥ 0
    certified: bool
    solver_status: str
    committed: dict[int, list[tuple[int, int]]]   # sid → [(lane_id, depart_atu)]
    claimed_per_unit: dict[int, float]  # sid → v_realized (TRUE functional)
    claimed_held: dict[int, int]        # sid → held_atu (exact)
    claimed_delivered: dict[int, int]   # sid → delivered_atu (exact)
    stranded_ids: list[int]
    n_vars: int
    n_constraints: int
    wall_sec: float
    breakpoints_h: tuple[float, ...] = field(default_factory=tuple)
    max_chord_overcharge: float = 0.0   # sup over grid of (chord − true C)/v


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _chords(theta: WaitCostProfile, breakpoints_h: tuple[float, ...],
            ) -> list[tuple[float, float]]:
    """Affine pieces (α per ATU, β) of the chord envelope of C_wait/v.

    Returned per UNIT VALUE (multiply by v_j when charging unit j).
    C(w_h) = a·w_h + b·w_h² with w_h in hours; α converts to per-ATU.
    """
    pts = sorted(set(breakpoints_h))
    if pts[0] != 0.0:
        pts = [0.0, *pts]
    out: list[tuple[float, float]] = []
    for w0, w1 in zip(pts, pts[1:]):
        c0 = theta.a_per_hour * w0 + theta.b_per_hour2 * w0 * w0
        c1 = theta.a_per_hour * w1 + theta.b_per_hour2 * w1 * w1
        slope_h = (c1 - c0) / (w1 - w0)
        alpha = slope_h / _H
        beta = c0 - slope_h * w0
        out.append((alpha, beta))
    return out


def _max_chord_overcharge(theta: WaitCostProfile,
                          breakpoints_h: tuple[float, ...]) -> float:
    """max over segment midpoints of chord(w) − C(w), per unit value."""
    pts = sorted(set([0.0, *breakpoints_h]))
    worst = 0.0
    for w0, w1 in zip(pts, pts[1:]):
        mid = 0.5 * (w0 + w1)
        c = theta.a_per_hour * mid + theta.b_per_hour2 * mid * mid
        c0 = theta.a_per_hour * w0 + theta.b_per_hour2 * w0 * w0
        c1 = theta.a_per_hour * w1 + theta.b_per_hour2 * w1 * w1
        chord = c0 + (c1 - c0) * (mid - w0) / (w1 - w0)
        worst = max(worst, chord - c)
    return worst


def solve_wait_ilp(
    node_ipc: bytes,
    lane_ipc: bytes,
    schedule_ipc: bytes,
    demand_ipc: bytes,
    *,
    horizon_hours: int,
    theta: WaitCostProfile,
    bucket_atu: int = _H,
    time_limit_sec: float = 300.0,
    breakpoints_h: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 24.0),
    msg: bool = False,
    solver_backend: str = "auto",
) -> WaitIlpSolution:
    """Solve the conservative wait-pricing ILP. See module docstring.

    ``solver_backend``: "auto" (HiGHS, the certified backend — clear error
    if highspy is missing), "highs", or "cbc" (explicit only; refused on
    hard-tolerance models where CBC presolve is proven to return false
    infeasibility on the big-M lateness block; HiGHS verified correct).
    """
    import pulp

    n_nodes, lanes, cap, flow_units = _decode_inputs(
        node_ipc, lane_ipc, schedule_ipc, demand_ipc, bucket_atu=bucket_atu)
    T = int(horizon_hours)
    lane_by_id = {e["lane_id"]: e for e in lanes}

    # TIMING FIX: engine-exact first boardable bucket (ceil, not floor).
    for s in flow_units:
        s["appear_bucket"] = _ceil_div(s["appear_atu"], bucket_atu)

    outgoing: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    incoming: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for e in lanes:
        for b in range(T):
            arrive_b = b + e["dur_buckets"]
            if arrive_b > T:
                continue
            if cap.get((e["lane_id"], b), 0) <= 0:
                continue
            outgoing.setdefault((e["src"], b), []).append(
                (e["lane_id"], b, arrive_b))
            incoming.setdefault((e["dst"], arrive_b), []).append(
                (e["lane_id"], b, arrive_b))

    prob = pulp.LpProblem("epure_wait_oracle", pulp.LpMinimize)

    x: dict[tuple[int, int, int], Any] = {}
    for p, ship in enumerate(flow_units):
        for e in lanes:
            for b in range(ship["appear_bucket"], T):
                if cap.get((e["lane_id"], b), 0) <= 0:
                    continue
                if b + e["dur_buckets"] > T:
                    continue
                x[(p, e["lane_id"], b)] = pulp.LpVariable(
                    f"x_{p}_{e['lane_id']}_{b}", cat=pulp.LpBinary)

    y = {p: pulp.LpVariable(f"y_{p}", cat=pulp.LpBinary)
         for p in range(len(flow_units))}

    # Flow conservation (same shape as the legacy model; documented
    # zero-mid-dwell restriction — see module docstring).
    for p, ship in enumerate(flow_units):
        origin, dest, appear_b = ship["origin"], ship["dest"], ship["appear_bucket"]
        origin_out = [x[(p, eid, b)]
                      for b in range(appear_b, T)
                      for eid, bb, _a in outgoing.get((origin, b), [])
                      if bb == b and (p, eid, b) in x]
        prob += pulp.lpSum(origin_out) == 1 - y[p], f"origin_p{p}"
        dest_in = [x[(p, eid, bb)]
                   for b in range(appear_b + 1, T + 1)
                   for eid, bb, arrive in incoming.get((dest, b), [])
                   if arrive == b and (p, eid, bb) in x]
        prob += pulp.lpSum(dest_in) == 1 - y[p], f"dest_p{p}"
        for h in range(n_nodes):
            if h in (origin, dest):
                continue
            for b in range(appear_b, T + 1):
                inflow = [x[(p, eid, bb)]
                          for eid, bb, arrive in incoming.get((h, b), [])
                          if (p, eid, bb) in x]
                outflow = [x[(p, eid, bb)]
                           for eid, bb, _a in outgoing.get((h, b), [])
                           if bb == b and (p, eid, b) in x]
                if inflow or outflow:
                    prob += (pulp.lpSum(inflow) == pulp.lpSum(outflow)
                             ), f"flow_p{p}_h{h}_b{b}"

        # SIMPLE PATHS ONLY — cycle suppression is CONSERVATISM-CRITICAL
        # here, not cosmetic: held = arrival − appear − Σ transit, so a
        # degenerate cycle's durations would SUBTRACT from held and let
        # the solver undercharge C_wait (probe-caught exploit). Visiting
        # any node at most once (and never re-entering the origin) makes
        # the arc set exactly a simple path, so held_expr is exact. A
        # further conservative restriction of the searched region.
        for h in range(n_nodes):
            if h == origin:
                back_in = [x[(p, eid, bb)]
                           for b in range(appear_b, T + 1)
                           for eid, bb, arrive in incoming.get((h, b), [])
                           if (p, eid, bb) in x]
                if back_in:
                    prob += pulp.lpSum(back_in) == 0, f"noreturn_p{p}"
                continue
            visits = [x[(p, eid, bb)]
                      for b in range(appear_b, T + 1)
                      for eid, bb, arrive in incoming.get((h, b), [])
                      if (p, eid, bb) in x]
            if len(visits) > 1:
                prob += pulp.lpSum(visits) <= 1, f"visit1_p{p}_h{h}"

    for (eid, b), cap_eb in cap.items():
        if b >= T:
            continue
        terms = [ship["size_units"] * x[(p, eid, b)]
                 for p, ship in enumerate(flow_units) if (p, eid, b) in x]
        if terms:
            prob += pulp.lpSum(terms) <= cap_eb, f"cap_e{eid}_b{b}"

    # ── wait-priced objective (chord envelope; conservative) ───────────
    span = tuple(w for w in breakpoints_h if w <= horizon_hours)
    if not span or max(span) < horizon_hours:
        span = (*span, float(horizon_hours))  # cover the full held range
    chords = _chords(theta, span)

    cwait = {p: pulp.LpVariable(f"cw_{p}", lowBound=0.0)
             for p in range(len(flow_units))}
    lateq = {p: pulp.LpVariable(f"lq_{p}", lowBound=0.0)
             for p in range(len(flow_units))}
    zlate: dict[int, Any] = {}
    horizon_atu = T * bucket_atu

    obj_terms = []
    for p, ship in enumerate(flow_units):
        v = unit_value(ship["size_units"])
        # held_p (exact ATU, linear): arrival-at-dest + transit corrections.
        arrival_terms = []
        for b in range(ship["appear_bucket"] + 1, T + 1):
            for eid, bb, arrive in incoming.get((ship["dest"], b), []):
                if arrive == b and (p, eid, bb) in x:
                    e = lane_by_id[eid]
                    arrival_terms.append(
                        (bb * bucket_atu + e["duration_atu"]) * x[(p, eid, bb)])
        transit_terms = [lane_by_id[eid]["duration_atu"] * x[(p, eid, b)]
                         for (pp, eid, b) in x if pp == p]
        held_expr = (pulp.lpSum(arrival_terms)
                     - ship["appear_atu"] * (1 - y[p])
                     - pulp.lpSum(transit_terms))
        for i, (alpha, beta) in enumerate(chords):
            prob += cwait[p] >= v * (alpha * held_expr + beta * (1 - y[p])), \
                f"cw_p{p}_seg{i}"

        # Lateness on EXACT arrival time.
        arrival_expr = pulp.lpSum(arrival_terms)
        due = ship["due_atu"]
        if theta.late_mode == "linear":
            # Uncapped linear charge ≥ true capped ℓ — conservative.
            prob += lateq[p] >= (v / theta.late_tol_atu) * (
                arrival_expr - due * (1 - y[p])), f"late_p{p}"
        else:  # hard
            zlate[p] = pulp.LpVariable(f"z_{p}", cat=pulp.LpBinary)
            prob += (arrival_expr - due * (1 - y[p])
                     <= theta.late_tol_atu + horizon_atu * zlate[p]), \
                f"late_p{p}"
            prob += lateq[p] >= v * zlate[p], f"latechg_p{p}"

        obj_terms.append(-v * (1 - y[p]) + cwait[p] + lateq[p])

    prob += pulp.lpSum(obj_terms)

    # Backend selection. HiGHS (highspy) is THE certified backend: the
    # oracle-integrity diagnosis (2026-07-03) proved CBC's presolve
    # returns FALSE INFEASIBILITY on the hard-tolerance big-M lateness
    # constraints (an explicit HiGHS solution was externally verified
    # feasible in a region CBC certified empty), while CBC and HiGHS
    # agree to 4 decimals on linear-tolerance models. Hence:
    #   auto  -> HiGHS, or a CLEAR error if highspy is missing (never a
    #            silent CBC fallback whose Infeasible would be a mystery);
    #   cbc   -> allowed only explicitly, and REFUSED on hard-tolerance
    #            models where it is proven untrustworthy.
    has_highs = "HiGHS" in pulp.listSolvers(onlyAvailable=True)
    if solver_backend not in ("auto", "highs", "cbc"):
        raise ValueError(f"solver_backend must be auto|highs|cbc, "
                         f"got {solver_backend!r}")
    if solver_backend in ("auto", "highs") and not has_highs:
        raise RuntimeError(
            "certified backend HiGHS required: install highspy "
            "(pip install 'epure-arena[oracle]'). The CBC fallback cannot "
            "certify this oracle — CBC presolve returns false "
            "infeasibility on big-M lateness constraints (oracle-integrity "
            "diagnosis, 2026-07-03). Pass solver_backend='cbc' explicitly "
            "ONLY for linear-tolerance models.")
    if solver_backend == "cbc" and theta.late_mode == "hard":
        raise RuntimeError(
            "solver_backend='cbc' refused for hard-tolerance profiles: "
            "CBC presolve is proven to return false infeasibility on the "
            "big-M lateness block (oracle-integrity diagnosis, 2026-07-03; "
            "HiGHS verified correct). Use the default HiGHS backend.")
    if solver_backend == "cbc":
        solver = pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit_sec,
                                   gapRel=0.0, threads=1)
    else:
        solver = pulp.HiGHS(msg=msg, timeLimit=time_limit_sec, gapRel=0.0)
    t0 = time.perf_counter()
    prob.solve(solver)
    wall = time.perf_counter() - t0
    status = pulp.LpStatus[prob.status]
    certified = prob.status == pulp.LpStatusOptimal
    if not certified:
        # The oracle is only an oracle when the optimum is CERTIFIED. A
        # time-limited termination may expose fractional relaxation values
        # (observed with HiGHS), which are not an incumbent at all —
        # extracting from them would fabricate a configuration. Fail
        # loudly; the caller shrinks the instance or raises the limit.
        raise RuntimeError(
            f"wait-ILP did not certify within {time_limit_sec}s "
            f"(status={status}, wall={wall:.1f}s, n_vars={len(x) + len(y)}); "
            "no integral incumbent is extracted from a non-optimal "
            "termination")

    # ── extraction + TRUE-functional claim at exact times ──────────────
    committed: dict[int, list[tuple[int, int]]] = {}
    claimed_per_unit: dict[int, float] = {}
    claimed_held: dict[int, int] = {}
    claimed_delivered: dict[int, int] = {}
    stranded: list[int] = []
    claimed_total = 0.0
    for p, ship in enumerate(flow_units):
        sid = ship["flow_unit_id"]
        if pulp.value(y[p]) > 0.5:
            stranded.append(sid)
            continue
        used = sorted(
            ((eid, b) for (pp, eid, b), var in x.items()
             if pp == p and pulp.value(var) > 0.5),
            key=lambda t: t[1])
        # Time-consistent chain walk (defense in depth against solver
        # degeneracy): each hop must depart from the current node in the
        # exact bucket flow conservation dictates.
        ordered: list[tuple[int, int]] = []
        cur = ship["origin"]
        expect_b: int | None = None
        rem = list(used)
        while rem:
            for i, (eid, b) in enumerate(rem):
                if lane_by_id[eid]["src"] == cur and (
                        expect_b is None or b == expect_b):
                    ordered.append((eid, b))
                    cur = lane_by_id[eid]["dst"]
                    expect_b = b + lane_by_id[eid]["dur_buckets"]
                    rem.pop(i)
                    break
            else:
                break
        assert not rem, (
            f"unit {sid}: {len(rem)} arc(s) outside the time-consistent "
            f"chain — cycle suppression failed (solver degeneracy); "
            f"used={[(e, bb, lane_by_id[e]['src'], lane_by_id[e]['dst'], lane_by_id[e]['dur_buckets']) for e, bb in used]} "
            f"ordered={ordered}")
        assert cur == ship["dest"], (
            f"unit {sid}: reconstructed chain ends at {cur}, not dest "
            f"{ship['dest']}; "
            f"used={[(e, bb, lane_by_id[e]['src'], lane_by_id[e]['dst'], lane_by_id[e]['dur_buckets']) for e, bb in used]} "
            f"ordered={ordered}")
        # Exact times along the chain (slot in bucket b departs at b·H).
        ready = ship["appear_atu"]
        held = 0
        for eid, b in ordered:
            depart = b * bucket_atu
            assert depart >= ready, (
                f"unit {sid}: engine-infeasible boarding depart={depart} "
                f"< ready={ready} — timing fix violated")
            held += depart - ready
            ready = depart + lane_by_id[eid]["duration_atu"]
        delivered = ready
        vr = (unit_value(ship["size_units"])
              - wait_cost(held, ship["size_units"], theta)
              - late_loss(delivered - ship["due_atu"], ship["size_units"],
                          theta))
        committed[sid] = [(eid, b * bucket_atu) for eid, b in ordered]
        claimed_per_unit[sid] = vr
        claimed_held[sid] = held
        claimed_delivered[sid] = delivered

    # Sorted-by-sid summation — the same convention as
    # pricing.value_report, so totals are bit-identical when every
    # per-unit term is (float addition is not associative).
    claimed_total = sum(claimed_per_unit[s] for s in sorted(claimed_per_unit))

    objective_v = -float(pulp.value(prob.objective))
    return WaitIlpSolution(
        objective_v_realized=objective_v,
        claimed_v_realized=claimed_total,
        pwl_gap=claimed_total - objective_v,
        certified=certified,
        solver_status=status,
        committed=committed,
        claimed_per_unit=claimed_per_unit,
        claimed_held=claimed_held,
        claimed_delivered=claimed_delivered,
        stranded_ids=sorted(stranded),
        n_vars=len(x) + len(y) + len(cwait) + len(lateq) + len(zlate),
        n_constraints=len(prob.constraints),
        wall_sec=round(wall, 3),
        breakpoints_h=span,
        max_chord_overcharge=_max_chord_overcharge(theta, span),
    )
