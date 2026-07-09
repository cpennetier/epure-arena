"""Capacity-aware, time-dependent, deterministic ex-ante planner (Step 3).

The Optimize-role MVP of the plan report (§4.2-4.4): slack-ordered successive
shortest paths over the IMPLICIT time-expanded network — CSR topology + the
per-lane sorted departure lists already on ``PlanContext.schedule_departures``,
with capacity as a per-connection (lane, slot) residual. Holding/waiting is
implicit in the "first departure ≥ arrival" lookup; no holding arcs, no
materialized expansion, ever.

A flow_unit is COMMITTED only when a capacity-feasible completion to its deadline
exists end-to-end (the search verifies the suffix before any residual is
spent), so the planner cannot manufacture the greedy router's mid-route
dead-ends. Every non-committed flow_unit carries a typed reason aligned with
``epure_arena.reasons`` — never a silent failure.

Determinism (enforced, not aspired): integer ATU arithmetic only; requests
processed in (slack ASC, flow_unit_id ASC) total order; search labels tie-break
on (f, arrival, node); per-node profile/residual state in dense lists — no hash
iteration in any decision path.

Main exports: :class:`CapacityAwarePlanner`.
"""

from __future__ import annotations

import heapq
import logging
import time
from bisect import bisect_left
from dataclasses import dataclass, field

from epure_arena.optimize.interface import Planner, PlanContext, PlanRequest, PlanResult

logger = logging.getLogger(__name__)

_INF = (1 << 63) - 1  # i64::MAX sentinel, matches the Rust oracle

# Planner-level reason strings (PlanResult.reason). CAPACITY_BLOCKED maps to
# ALGO_CAPACITY_MYOPIA / WORLD_CAPACITY_SHORTFALL pending the Step-5 LP
# certificate; the others map 1:1 onto epure_arena.reasons codes.
REASON_CAPACITY_BLOCKED = "CAPACITY_BLOCKED"
REASON_ENVELOPE_DEADLINE = "ENVELOPE_DEADLINE"
REASON_ENVELOPE_CLIFF = "ENVELOPE_CLIFF"
REASON_WORLD_STRUCTURAL = "WORLD_STRUCTURAL"


@dataclass
class _Profiles:
    """Capacity-free destination profile: per node, parallel (deps, arrs) lists
    in ASC departure order (Pareto ⇒ arrivals ASC too). eval = first pair with
    dep ≥ t."""

    deps: list[list[int]]
    arrs: list[list[int]]

    def eval(self, node: int, t: int) -> int:
        deps = self.deps[node]
        i = bisect_left(deps, t)
        return self.arrs[node][i] if i < len(deps) else _INF

    def reachable_ever(self, node: int) -> bool:
        return bool(self.deps[node])


@dataclass
class _DecisionRecord:
    """Per-flow_unit decision trace (forward-compat: persisted per run so future
    Gate/Imagine agents are graded by exactly this instrument)."""

    flow_unit_id: int
    origin: int
    dest: int
    appear_atu: int
    due_atu: int
    oracle_arrival_atu: int  # capacity-free earliest arrival (slack input)
    status: str  # 'planned' | 'failed'
    reason: str | None
    planned_arrival_atu: int  # 0 if failed
    path_lanes: list[int] = field(default_factory=list)
    path_slots: list[tuple[int, int]] = field(default_factory=list)  # (lane_id, slot_pos)
    expansions: int = 0  # search effort (labels popped)
    search_ns: int = 0  # wall time of this flow_unit's search (Step-6 percentiles)
    # Saturated (lane_id, slot_pos) pairs skipped during a FAILED search —
    # the C1.5 repack policy's displacement candidates (capped, deterministic
    # encounter order). Empty for committed flow_units.
    blocked_slots: list[tuple[int, int]] = field(default_factory=list)
    # C1.5: how this flow_unit's final state was reached: "direct" (the plain
    # commit loop), "repack_inserted" (committed by displacing a blocker),
    # "repack_rerouted" (was committed, displaced, recommitted on a new path).
    commit_via: str = "direct"


class CapacityAwarePlanner(Planner):
    """Slack-ordered successive-shortest-path planner over per-slot residuals.

    Search: time-dependent A* guided by the capacity-free destination profile
    as an admissible lower bound (profile arrival ignores capacity, so it
    never overestimates); saturated connections are skipped during lane
    relaxation; states that cannot make the deadline even capacity-free are
    pruned.

    After ``plan()``, ``last_decision_trace`` holds one :class:`_DecisionRecord`
    per request (input order) and ``last_reason_counts`` the typed failure
    histogram.

    A per-flow_unit forward-CSA search variant was benchmarked against this A*
    on Regime B @ 40k (2026-06-10) and deleted per plan §7 Step 3: 252.9 vs
    46.0 µs/flow_unit (mean 2,565.7 connections scanned vs 7.8 labels popped) —
    the destination-profile heuristic is what keeps the search tiny, and CSA
    cannot amortize its scan under per-flow_unit residual mutation.
    """

    POLICIES = ("slack_first", "value_weighted", "batched_repack")

    def __init__(
        self,
        policy: str = "slack_first",
        *,
        wave_size: int = 4000,
        repack_budget: int = 2,
    ) -> None:
        """Args (C1.5 — the commit-policy experiment; ONE planner, one flag):
            policy: commit-order/commitment policy —
                ``slack_first`` (control): (slack ASC, flow_unit_id ASC).
                ``value_weighted``: declared scoring function
                    (priority_class ASC, slack ASC, flow_unit_id ASC) — VIP
                    commits first, deadline value second.
                ``batched_repack``: slack_first order, committed in waves of
                    ``wave_size``; at wave seal, each capacity-failed flow_unit
                    may displace up to ``repack_budget`` same-wave blockers
                    via a deterministic pairwise exchange, accepted only if
                    BOTH end up committed (net +1; every certification
                    invariant holds on the residuals at seal).
            wave_size / repack_budget: ``batched_repack`` knobs.
        """
        if policy not in self.POLICIES:
            raise ValueError(f"unknown policy {policy!r}; one of {self.POLICIES}")
        if wave_size < 1 or repack_budget < 0:
            raise ValueError("wave_size >= 1 and repack_budget >= 0 required")
        self.policy = policy
        self.wave_size = wave_size
        self.repack_budget = repack_budget
        self.last_decision_trace: list[_DecisionRecord] = []
        self.last_reason_counts: dict[str, int] = {}
        # Phase wall times of the last plan() call (Step-6 cost attribution):
        # t_setup_s (residuals + connection index), t_profiles_s (one-time
        # destination profiles, the O(K·|C|) term), t_commit_s (order +
        # per-flow_unit search/commit loop incl. any repack passes).
        self.last_timings: dict[str, float] = {}
        # batched_repack effort accounting (a result, not a footnote):
        # exchanges attempted/accepted, extra searches spent.
        self.last_repack_stats: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "capacity_aware"

    # ── profile construction (capacity-free, backward CSA) ──────────────

    @staticmethod
    def _connections_desc(
        context: PlanContext,
    ) -> list[tuple[int, int, int, int, int]]:
        """Global connection list (depart DESC, lane_id ASC): one tuple
        (depart_atu, lane_id, src, dst, duration_atu) per scheduled slot."""
        lane_meta: dict[int, tuple[int, int, int]] = {}
        for u, adj in enumerate(context.adjacency):
            for v, eid, dur, _cost, _co2, _mode in adj:
                lane_meta[eid] = (u, v, dur)
        conns: list[tuple[int, int, int, int, int]] = []
        for eid, slots in context.schedule_departures.items():
            meta = lane_meta.get(eid)
            if meta is None:
                continue
            u, v, dur = meta
            for dep, _cap in slots:
                conns.append((dep, eid, u, v, dur))
        conns.sort(key=lambda c: (-c[0], c[1]))
        return conns

    @staticmethod
    def _build_profile(
        dest: int,
        n_nodes: int,
        conns_desc: list[tuple[int, int, int, int, int]],
    ) -> _Profiles:
        """Backward profile-CSA (mirror of the Rust oracle, capacity-free)."""
        deps: list[list[int]] = [[] for _ in range(n_nodes)]
        arrs: list[list[int]] = [[] for _ in range(n_nodes)]
        for dep, _eid, u, v, dur in conns_desc:
            arr_v = dep + dur
            if v == dest:
                tau = arr_v
            else:
                dv = deps[v]
                i = bisect_left(dv, arr_v)
                tau = arrs[v][i] if i < len(dv) else _INF
            if tau == _INF:
                continue
            du, au = deps[u], arrs[u]
            # Pushed in DESC dep order ⇒ prepend-equivalent: keep ASC lists by
            # inserting at front only when strictly better (Pareto).
            if du and du[0] == dep:
                if tau < au[0]:
                    au[0] = tau
            elif not du or tau < au[0]:
                du.insert(0, dep)
                au.insert(0, tau)
        return _Profiles(deps=deps, arrs=arrs)

    # ── the planner ──────────────────────────────────────────────────────

    def plan(
        self,
        requests: list[PlanRequest],
        context: PlanContext,
    ) -> list[PlanResult]:
        n = context.n_nodes
        t_phase = time.perf_counter()

        # Residual capacity per (lane_id, slot_pos); deps per lane for bisect.
        lane_deps: dict[int, list[int]] = {}
        residual: dict[int, list[int]] = {}
        for eid, slots in context.schedule_departures.items():
            lane_deps[eid] = [d for d, _c in slots]
            residual[eid] = [c for _d, c in slots]

        conns_desc = self._connections_desc(context)
        t_setup = time.perf_counter() - t_phase

        t_phase = time.perf_counter()
        profiles: dict[int, _Profiles] = {}
        for req in requests:
            if req.destination not in profiles:
                profiles[req.destination] = self._build_profile(
                    req.destination, n, conns_desc,
                )
        t_profiles = time.perf_counter() - t_phase
        t_phase = time.perf_counter()

        # Slack order: tightest (due − capacity-free arrival) first; ties by
        # flow_unit_id. Unreachable flow_units sort last (slack _INF keeps them
        # out of the contention window; they never search anyway).
        def oracle_arrival(req: PlanRequest) -> int:
            if req.origin == req.destination:
                return req.appear_atu
            return profiles[req.destination].eval(req.origin, req.appear_atu)

        def slack_of(req: PlanRequest) -> int:
            arr = oracle_arrival(req)
            return (req.due_atu - arr) if arr < _INF else _INF

        if self.policy == "value_weighted":
            # Declared scoring function: priority class first (VIP=0 commits
            # first), deadline slack second, flow_unit_id tiebreak.
            order = sorted(
                range(len(requests)),
                key=lambda i: (requests[i].priority_class,
                               slack_of(requests[i]),
                               requests[i].flow_unit_id),
            )
        else:  # slack_first and batched_repack share the slack order
            order = sorted(
                range(len(requests)),
                key=lambda i: (slack_of(requests[i]), requests[i].flow_unit_id),
            )

        trace: list[_DecisionRecord | None] = [None] * len(requests)
        repack_stats = {"exchanges_attempted": 0, "exchanges_accepted": 0,
                        "extra_searches": 0}

        def commit_one(i: int) -> _DecisionRecord:
            req = requests[i]
            prof = profiles[req.destination]
            o_arr = oracle_arrival(req)
            record = _DecisionRecord(
                flow_unit_id=req.flow_unit_id, origin=req.origin,
                dest=req.destination, appear_atu=req.appear_atu,
                due_atu=req.due_atu, oracle_arrival_atu=o_arr,
                status="failed", reason=None, planned_arrival_atu=0,
            )
            if o_arr >= _INF:
                record.reason = (
                    REASON_ENVELOPE_CLIFF
                    if prof.reachable_ever(req.origin)
                    else REASON_WORLD_STRUCTURAL
                )
            elif o_arr > req.due_atu:
                record.reason = REASON_ENVELOPE_DEADLINE
            else:
                t_search = time.perf_counter_ns()
                found = self._search_astar(
                    req, context, prof, lane_deps, residual, record,
                )
                record.search_ns = time.perf_counter_ns() - t_search
                if found is not None:
                    arrival, hops = found
                    for eid, slot_pos in hops:
                        residual[eid][slot_pos] -= req.size_units
                    record.status = "planned"
                    record.reason = None
                    record.planned_arrival_atu = arrival
                    record.path_lanes = [eid for eid, _ in hops]
                    record.path_slots = hops
                else:
                    # Capacity-free supply was on time (checked above), so the
                    # binding constraint is capacity — refined by the LP.
                    record.reason = REASON_CAPACITY_BLOCKED
            trace[i] = record
            return record

        def uncommit(i: int) -> None:
            rec, req = trace[i], requests[i]
            for eid, slot_pos in rec.path_slots:
                residual[eid][slot_pos] += req.size_units

        def recommit_original(i: int) -> None:
            rec, req = trace[i], requests[i]
            for eid, slot_pos in rec.path_slots:
                residual[eid][slot_pos] -= req.size_units

        def repack_wave(wave: list[int]) -> None:
            """Bounded pairwise exchange at wave seal (batched_repack): each
            capacity-failed flow_unit may displace up to ``repack_budget``
            same-wave blockers; an exchange is accepted only if BOTH flow_units
            end up committed on the seal-time residuals (net +1)."""
            committed = [i for i in wave if trace[i].status == "planned"]
            failed = [i for i in wave
                      if trace[i].reason == REASON_CAPACITY_BLOCKED]
            for fi in failed:
                frec, freq = trace[fi], requests[fi]
                blocked = set(frec.blocked_slots)
                if not blocked:
                    continue
                # Loosest-slack blockers first (most likely to reroute),
                # flow_unit_id tiebreak — deterministic.
                blockers = sorted(
                    (ci for ci in committed
                     if blocked.intersection(trace[ci].path_slots)),
                    key=lambda ci: (-(trace[ci].due_atu
                                      - trace[ci].oracle_arrival_atu),
                                    trace[ci].flow_unit_id),
                )
                attempts = 0
                for ci in blockers:
                    if attempts >= self.repack_budget:
                        break
                    attempts += 1
                    repack_stats["exchanges_attempted"] += 1
                    crec, creq = trace[ci], requests[ci]
                    old_path = (crec.path_lanes, crec.path_slots,
                                crec.planned_arrival_atu)
                    uncommit(ci)
                    repack_stats["extra_searches"] += 1
                    found_f = self._search_astar(
                        freq, context, profiles[freq.destination],
                        lane_deps, residual, frec,
                    )
                    if found_f is None:
                        recommit_original(ci)
                        continue
                    f_arrival, f_hops = found_f
                    for eid, slot_pos in f_hops:
                        residual[eid][slot_pos] -= freq.size_units
                    repack_stats["extra_searches"] += 1
                    found_c = self._search_astar(
                        creq, context, profiles[creq.destination],
                        lane_deps, residual, crec,
                    )
                    if found_c is None:
                        # Reject: net 0 swap. Roll back f, restore c exactly.
                        for eid, slot_pos in f_hops:
                            residual[eid][slot_pos] += freq.size_units
                        recommit_original(ci)
                        continue
                    # Accept: both committed (net +1).
                    c_arrival, c_hops = found_c
                    for eid, slot_pos in c_hops:
                        residual[eid][slot_pos] -= creq.size_units
                    crec.planned_arrival_atu = c_arrival
                    crec.path_lanes = [eid for eid, _ in c_hops]
                    crec.path_slots = c_hops
                    crec.commit_via = "repack_rerouted"
                    frec.status = "planned"
                    frec.reason = None
                    frec.planned_arrival_atu = f_arrival
                    frec.path_lanes = [eid for eid, _ in f_hops]
                    frec.path_slots = f_hops
                    frec.commit_via = "repack_inserted"
                    committed.append(fi)
                    repack_stats["exchanges_accepted"] += 1
                    break

        if self.policy == "batched_repack":
            for w0 in range(0, len(order), self.wave_size):
                wave = order[w0: w0 + self.wave_size]
                for i in wave:
                    commit_one(i)
                repack_wave(wave)
        else:
            for i in order:
                commit_one(i)

        results: list[PlanResult | None] = [None] * len(requests)
        reason_counts: dict[str, int] = {}
        for i, record in enumerate(trace):
            if record is None:
                continue
            req = requests[i]
            if record.status == "planned":
                results[i] = PlanResult(
                    flow_unit_id=req.flow_unit_id,
                    path=record.path_lanes,
                    estimated_duration_atu=record.planned_arrival_atu - req.appear_atu,
                    status="planned",
                    planner_name=self.name,
                    planned_arrival_atu=record.planned_arrival_atu,
                )
            else:
                reason_counts[record.reason] = reason_counts.get(record.reason, 0) + 1
                results[i] = PlanResult(
                    flow_unit_id=req.flow_unit_id,
                    path=[],
                    status="failed",
                    planner_name=self.name,
                    reason=record.reason,
                )

        self.last_decision_trace = [t for t in trace if t is not None]
        self.last_reason_counts = reason_counts
        self.last_repack_stats = repack_stats
        self.last_timings = {
            "t_setup_s": round(t_setup, 3),
            "t_profiles_s": round(t_profiles, 3),
            "t_commit_s": round(time.perf_counter() - t_phase, 3),
        }
        return [r for r in results if r is not None]

    def _search_astar(
        self,
        req: PlanRequest,
        context: PlanContext,
        prof: _Profiles,
        lane_deps: dict[int, list[int]],
        residual: dict[int, list[int]],
        record: _DecisionRecord,
    ) -> tuple[int, list[tuple[int, int]]] | None:
        """Time-dependent A* from (origin, appear) to dest over residual
        capacity. Returns (arrival, [(lane_id, slot_pos), ...]) for an on-time
        path, or None if no capacity-feasible on-time completion exists.

        Admissible heuristic: h(v, t) = capacity-free profile arrival at dest
        from (v, t). f = that estimated final arrival; states with f > due are
        pruned (we only ever commit on-time plans).
        """
        origin, dest, due, size = req.origin, req.destination, req.due_atu, req.size_units
        if origin == dest:
            return (req.appear_atu, [])

        blocked: list[tuple[int, int]] = []
        best: dict[int, int] = {origin: req.appear_atu}
        parent: dict[int, tuple[int, int, int, int]] = {}  # v -> (u, eid, slot_pos, arr_v)
        h0 = prof.eval(origin, req.appear_atu)
        heap: list[tuple[int, int, int]] = [(h0, req.appear_atu, origin)]

        while heap:
            f, g, u = heapq.heappop(heap)
            record.expansions += 1
            if u == dest:
                hops: list[tuple[int, int]] = []
                v = dest
                while v != origin:
                    pu, eid, slot_pos, _arr = parent[v]
                    hops.append((eid, slot_pos))
                    v = pu
                hops.reverse()
                return (g, hops)
            if g > best.get(u, _INF):
                continue  # stale label
            for v, eid, dur, _cost, _co2, _mode in context.adjacency[u]:
                deps = lane_deps.get(eid)
                if not deps:
                    continue
                res = residual[eid]
                s = bisect_left(deps, g)
                while s < len(deps) and res[s] < size:
                    # Saturated connection skipped — a displacement candidate
                    # for the batched_repack policy (capped; encounter order).
                    if len(blocked) < 64:
                        blocked.append((eid, s))
                    s += 1
                if s >= len(deps):
                    continue
                arr_v = deps[s] + dur
                if arr_v >= best.get(v, _INF):
                    continue
                hv = arr_v if v == dest else prof.eval(v, arr_v)
                if hv > due:
                    continue  # cannot make the deadline even capacity-free
                best[v] = arr_v
                parent[v] = (u, eid, s, arr_v)
                heapq.heappush(heap, (hv, arr_v, v))
        record.blocked_slots = blocked
        return None
