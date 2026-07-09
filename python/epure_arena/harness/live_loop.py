"""The thin live loop (C2 Phase 1): World→Substrate→{Gate, Propose, Imagine,
Navigate, Optimize}→World on synthetic disruption scenarios.

Canonical decision point: a disruption invalidates commitments mid-execution.
Reservation RELEASE is physics (always happens — the invalidated reservations
return to the pool via ``PyEngine.cancel_pinned_routes``); the agent's choice
is whether to RE-CERTIFY the affected flow_units (intervene: re-plan on the
post-disruption masked timetable and current residuals, re-pin) or LET THEM
DEGRADE to the greedy residual pass. Roles map onto existing seams: Gate =
``GateBase`` (the canonical-evaluation ABC; AlwaysOn/AlwaysOff are the
skeleton stubs), Propose = candidate generator (Phase 1: the single
recommit-all candidate), Imagine = Δ estimator per candidate (Phase 1: stub),
Navigate = FIXED declared selector (argmax estimate, first-candidate
tiebreak; not varied this campaign), Optimize = the standing
CapacityAwarePlanner under the same certification discipline as ex-ante.

Mass typing (zero unexplained mass at all times): every flow_unit ends in
exactly one flow — COMMITTED_INTACT (never disrupted; zero-divergence),
RECOMMITTED (disrupted, re-certified; zero-divergence vs its final plan),
DEGRADED (disrupted, not re-certified; greedy fate on-time/late/stranded —
no certificate, so on-time IS possible), REFUSED_EXANTE / REFUSED_EPOCH
(typed certificates), SURGE_* (surge demand, committed or degraded).

Certificate caveat under disruption, stated: the C1 refusal-certificate
theorem assumed reservations only grow. Disruption releases BREAK that
monotonicity — a refusal certified at time t can become deliverable after a
later release. Certificates are therefore TIME-INDEXED under disruption:
``opportunistic on-time ≡ 0`` remains an invariant only for undisrupted
runs; in disrupted runs, post-release recoveries are a typed flow
(``refused_recovered_on_time``), not a bug.

Main exports: :func:`run_live_loop`, :class:`LoopResult`,
:class:`EpochTrace`, :class:`EpochFeatures`. The role implementations live
in their own packages (:mod:`epure_arena.propose`, :mod:`epure_arena.imagine`,
:mod:`epure_arena.navigate`, :mod:`epure_arena.gate`) and are re-exported
here for convenience.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from epure_arena.optimize.fidelity import apply_plan, assert_plan_capacity_legal
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanContext, PlanRequest
from epure_arena.optimize.capacity_aware import CapacityAwarePlanner

_H = 3_600_000

# Surge flow_unit_ids live at/above this base (mirrors
# epure_arena.scenarios.disruptions.SURGE_ID_BASE; a documented mirror, not an
# import — the planner package stays node-engine-free).
SURGE_ID_BASE = 10_000_000


# ── role surfaces ─────────────────────────────────────────────────────────
# The implementations are first-class role packages; the harness imports
# them (re-exported here so the historical surface stays importable).

from epure_arena.imagine import ImagineBase, ZeroImagine  # noqa: E402
from epure_arena.navigate import navigate_argmax  # noqa: E402
from epure_arena.propose import Candidate, ProposeBase, SingleRecommitPropose  # noqa: E402


@dataclass(frozen=True)
class EpochFeatures:
    """Observation context handed to the Gate (and persisted per decision).
    Deliberately raw — a future learned Gate trains on exactly this surface."""

    epoch_idx: int
    t_atu: int
    kind: str
    severity: str
    n_affected_committed: int
    n_surge: int
    n_committed_total: int
    n_slots_lost: int  # cancelled/outage-blocked slots in the window
    descriptor: dict[str, Any]


# ── traces and results ────────────────────────────────────────────────────


@dataclass
class EpochTrace:
    features: EpochFeatures
    gate_decision: bool
    candidates: list[str]
    estimates: list[float]
    selected: int | None
    n_cancelled: int
    n_recommitted: int
    n_refused_epoch: int
    n_degraded: int
    refused_reasons: dict[str, int]
    # Phase-2 scoring pass (populated when score_epochs=True on a never-leg
    # run): the severity proxy, the dry-run value estimate and its parts.
    severity_score: int = 0          # s_i = n_affected_committed + n_surge
    value_estimate: float = 0.0      # Δ̂ = n_recertifiable − degraded_UB
    n_recertifiable: int = 0         # dry-run planner on the masked context
    degraded_feasible_ub: int = 0    # masked-timetable capacity-free on-time


@dataclass
class LoopResult:
    """Final typed accounting. ``flows`` maps flow name → flow_unit count;
    zero unexplained mass: Σ flows == base demand + surge demand."""

    flows: dict[str, int]
    epoch_traces: list[EpochTrace]
    kpis: dict[str, Any]
    n_demand_total: int
    guaranteed_on_time: int  # intact + recommitted, all realize exactly
    digest: str  # sha256 over flows + per-epoch decisions (byte-identity key)

    @property
    def mass_closed(self) -> bool:
        return sum(self.flows.values()) == self.n_demand_total


# ── the loop ──────────────────────────────────────────────────────────────


def _requests_from_demand_ipc(demand_ipc: bytes) -> list[PlanRequest]:
    import pyarrow.ipc as ipc

    dem = ipc.open_stream(demand_ipc).read_all()
    cols = {k: dem[k].to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class",
    )}
    return [
        PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                    cols["dest_node_id"][i], cols["appear_atu"][i],
                    cols["due_atu"][i], cols["size_units"][i],
                    cols["priority_class"][i])
        for i in range(dem.num_rows)
    ]


def _lane_meta(ctx: PlanContext) -> dict[int, tuple[int, int, int]]:
    meta: dict[int, tuple[int, int, int]] = {}
    for u, adj in enumerate(ctx.adjacency):
        for v, eid, dur, _c, _co, _m in adj:
            meta[eid] = (u, v, dur)
    return meta


def run_live_loop(
    bundle,
    disruptions,
    *,
    gate=None,
    propose=None,
    imagine=None,
    policy: str = "value_weighted",
    horizon_hours: int = 24,
    intervene_epochs: set[int] | None = None,
    score_epochs: bool = False,
    truncate_at: int | None = None,
) -> LoopResult:
    """Run the thin live loop end-to-end. Deterministic: same (bundle,
    disruptions, roles, policy) ⇒ byte-identical LoopResult digest.

    Phase-2 controls: ``intervene_epochs`` (a set of epoch indices) overrides
    the gate with an explicit intervention plan — ∅ = never, {i} = at-i-only
    (the paired-Δ leg), top-k sets = the budgeted ranking arms.
    ``truncate_at`` (ATU) stops execution early for the truncated-rollout
    estimator: committed flow_units with planned arrival ≤ truncate_at are
    asserted delivered exactly (zero-divergence still enforced inside the
    window); later commitments count as guaranteed WITHOUT assertion
    (certified-pending — they realize by the invariance theorem); realized
    flows beyond the cut are censored. ``score_epochs=True`` computes
    per-epoch scores WITHOUT acting (intended
    on the never leg): the severity proxy s_i, and the value estimate
    Δ̂_i = n_recertifiable (dry-run planner, no commit) − degraded_UB
    (capacity-free profile-oracle on-time count of the affected set on the
    STRUCTURALLY masked post-disruption timetable — slots lost to the
    disruption are removed, not zero-capped, because the capacity-free
    profile ignores capacities)."""
    from epure_arena import _engine
    import hashlib

    propose = propose or SingleRecommitPropose()
    imagine = imagine or ZeroImagine()
    horizon_atu = horizon_hours * _H

    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        bundle.demand_ipc, _H, "time_aware",
    )
    eng.set_enforce_capacity(True)
    ctx = build_plan_context(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        eng.get_lane_id_to_csr(),
    )
    e2c = ctx.lane_id_to_csr
    emeta = _lane_meta(ctx)

    base_reqs = _requests_from_demand_ipc(bundle.demand_ipc)
    req_by_sid: dict[int, PlanRequest] = {r.flow_unit_id: r for r in base_reqs}
    sizes = {r.flow_unit_id: r.size_units for r in base_reqs}

    # Ex-ante plan + pin.
    planner = CapacityAwarePlanner(policy=policy)
    results = planner.plan(base_reqs, ctx)
    apply_plan(eng, results, ctx, decision_trace=planner.last_decision_trace,
               sizes=sizes, pinned=True)

    # Live state: final plan hops per committed sid; flow membership.
    plan_hops: dict[int, list[tuple[int, int, int]]] = {}  # sid -> [(eid, depart, arrival)]
    plan_arrival: dict[int, int] = {}
    flow: dict[int, str] = {}  # sid -> current flow tag
    for d in planner.last_decision_trace:
        if d.status == "planned":
            hops = []
            for eid, slot_pos in d.path_slots:
                dep = ctx.schedule_departures[eid][slot_pos][0]
                hops.append((eid, dep, dep + emeta[eid][2]))
            plan_hops[d.flow_unit_id] = hops
            plan_arrival[d.flow_unit_id] = d.planned_arrival_atu
            flow[d.flow_unit_id] = "committed_intact"
        else:
            flow[d.flow_unit_id] = "refused_exante"

    # Pending arrival of RECOMMITTED flow_units: the in-flight/waiting ride to
    # the replan's start position is no longer represented in plan_hops, so
    # its (node, time) is tracked here — it is the flow_unit's effective
    # materialization point for later-epoch doom checks, and the board-time
    # origin of its new hop chain. (Found by the zero-divergence assertion:
    # an old boarded leg arriving into a node outaged by a LATER epoch.)
    pending_arrival: dict[int, tuple[int, int]] = {}

    # Accumulated outage windows (engine set_* replaces, so re-set full lists).
    lane_windows: list[tuple[int, int, int]] = []  # (csr, start, end)
    node_windows: list[tuple[int, int, int]] = []
    surge_reqs_all: list[PlanRequest] = []

    epoch_traces: list[EpochTrace] = []
    n_committed = sum(1 for f in flow.values() if f == "committed_intact")

    for ei, d in enumerate(sorted(disruptions, key=lambda x: (x.onset_atu, x.kind))):
        eng.step_until(d.onset_atu)
        t0 = d.onset_atu

        # ── install the disruption (physics) ─────────────────────────────
        lost_slot_keys: set[tuple[int, int]] = set()  # (eid, depart) made unusable
        if d.kind == "lane_outage":
            for eid in d.lane_ids:
                lane_windows.append((e2c[eid], d.onset_atu, d.end_atu))
                for dep, _cap in ctx.schedule_departures.get(eid, []):
                    if d.onset_atu <= dep < d.end_atu:
                        lost_slot_keys.add((eid, dep))
        elif d.kind == "capacity_shock":
            for eid, s, e in d.slot_windows:
                lane_windows.append((e2c[eid], s, e))
                lost_slot_keys.add((eid, s))
        elif d.kind == "node_outage":
            for hid in d.node_ids:
                node_windows.append((hid, d.onset_atu, d.end_atu))
            for eid, (u, v, dur) in emeta.items():
                for dep, _cap in ctx.schedule_departures.get(eid, []):
                    arr = dep + dur
                    if (u in d.node_ids and d.onset_atu <= dep < d.end_atu) or (
                            v in d.node_ids and d.onset_atu <= arr < d.end_atu):
                        lost_slot_keys.add((eid, dep))
        eng.set_lane_outages(lane_windows)
        eng.set_node_outages(node_windows)

        surge_reqs: list[PlanRequest] = []
        if d.kind == "demand_surge" and d.surge_ipc is not None:
            eng.inject_demand(d.surge_ipc)
            surge_reqs = _requests_from_demand_ipc(d.surge_ipc)
            for r in surge_reqs:
                req_by_sid[r.flow_unit_id] = r
                sizes[r.flow_unit_id] = r.size_units
                flow[r.flow_unit_id] = "surge_degraded"  # until committed
            surge_reqs_all.extend(surge_reqs)

        # ── affected commitments ──────────────────────────────────────────
        # A hop is BOARDED iff the flow_unit reached its source node before t0
        # (board_time = arrival at the hop's source = previous hop's arrival,
        # or the appear time for the first hop): its reservation is already
        # consumed and its Depart event queued. DECLARED PHYSICS (the
        # engine's §E semantics, now load-bearing): outages block BOARDING;
        # dispatched/boarded legs run. So only UN-BOARDED reservations can be
        # invalidated. Two DOOM cases are unavoidable physics (typed
        # disrupted_doomed; Phase-1 scope: no post-strand recovery): a
        # boarded hop arriving into an outaged node (strands NodeOutage on
        # arrival), and a flow_unit MATERIALIZING at an outaged origin during
        # the window (the inject→Arrive at appear strands at the origin).
        affected: list[int] = []
        inflight_doomed: list[int] = []
        outaged_nodes = set(d.node_ids) if d.kind == "node_outage" else set()
        for sid, hops in list(plan_hops.items()):
            if flow[sid] not in ("committed_intact", "recommitted",
                                 "surge_committed"):
                continue
            r = req_by_sid[sid]
            mat_node, mat_t = pending_arrival.get(
                sid, (r.origin, r.appear_atu))
            # Doom case 2: materializes (origin appearance, or the pending
            # arrival of a recommitted flow_unit's ride-in) at an outaged node
            # in-window, with the materialization still pending.
            if (mat_node in outaged_nodes
                    and d.onset_atu <= mat_t < d.end_atu and mat_t >= t0):
                inflight_doomed.append(sid)
                continue
            board_t = mat_t
            doomed = False
            hit = False
            for eid, dep, arr in hops:
                boarded = board_t < t0
                if boarded:
                    if (emeta[eid][1] in outaged_nodes
                            and d.onset_atu <= arr < d.end_atu):
                        doomed = True
                        break
                elif (eid, dep) in lost_slot_keys:
                    hit = True
                board_t = arr  # next hop boards at this hop's arrival
            if doomed:
                inflight_doomed.append(sid)
            elif hit:
                affected.append(sid)
        affected.sort()
        inflight_doomed.sort()

        # ── release (physics, not choice) ─────────────────────────────────
        n_cancelled = eng.cancel_pinned_routes(affected + inflight_doomed)
        hops_snapshot: dict[int, list[tuple[int, int, int]]] = {}
        for sid in affected:
            hops_snapshot[sid] = plan_hops.pop(sid)
            flow[sid] = "degraded"  # transitional PLAN_DISRUPTED state;
                                    # re-certification below may lift it
        for sid in inflight_doomed:
            plan_hops.pop(sid, None)
            flow[sid] = "disrupted_doomed"

        features = EpochFeatures(
            epoch_idx=ei, t_atu=t0, kind=d.kind,
            severity=d.descriptor.get("severity", "?"),
            n_affected_committed=len(affected), n_surge=len(surge_reqs),
            n_committed_total=n_committed, n_slots_lost=len(lost_slot_keys),
            descriptor=dict(d.descriptor),
        )

        # ── the decision ──────────────────────────────────────────────────
        if intervene_epochs is not None:
            decision = ei in intervene_epochs
        else:
            decision = bool(gate.should_invoke(features))
        cands: list[Candidate] = []
        ests: list[float] = []
        sel: int | None = None
        n_recommitted = 0
        n_refused_epoch = 0
        refused_reasons: dict[str, int] = {}

        need_masked = (decision or score_epochs) and (affected or surge_reqs)
        masked_ctx = None
        masked_sched: dict[int, list[tuple[int, int]]] = {}
        structural_sched: dict[int, list[tuple[int, int]]] = {}
        if need_masked:
            # Post-disruption world: masked timetable × CURRENT residuals
            # (slot_state post-release). structural_sched drops the
            # disruption-blocked slots entirely (for the capacity-free UB
            # profile, which ignores capacities).
            ss = eng.slot_state()
            residual: dict[tuple[int, int], int] = {}
            for eid, dep, cap, use, resv in zip(
                    ss["lane_id"], ss["depart_atu"], ss["capacity"],
                    ss["usage"], ss["reserved"]):
                residual[(int(eid), int(dep))] = int(cap) - int(use) - int(resv)
            blocked_lanes = {(c, s, e) for c, s, e in lane_windows}
            node_block = list(node_windows)
            for eid, slots in ctx.schedule_departures.items():
                u, v, dur = emeta[eid]
                keep = []
                structural = []
                for dep, _cap in slots:
                    if dep < t0:
                        keep.append((dep, 0))  # past: index-preserved, unusable
                        structural.append((dep, 1))  # harmless: eval starts ≥ t0
                        continue
                    csr = e2c[eid]
                    arr = dep + dur
                    disruption_blocked = (
                        any(c == csr and s <= dep < e for c, s, e in blocked_lanes)
                        or any(h == u and s <= dep < e for h, s, e in node_block)
                        or any(h == v and s <= arr < e for h, s, e in node_block)
                    )
                    if disruption_blocked:
                        keep.append((dep, 0))
                        continue
                    keep.append((dep, max(0, residual[(eid, dep)])))
                    structural.append((dep, 1))
                masked_sched[eid] = keep
                structural_sched[eid] = structural
            masked_ctx = PlanContext(
                n_nodes=ctx.n_nodes, n_lanes=ctx.n_lanes,
                adjacency=ctx.adjacency, schedule_departures=masked_sched,
                lane_id_to_csr=ctx.lane_id_to_csr, distances=ctx.distances,
                bucket_atu=ctx.bucket_atu,
            )

        replan_req_by_sid: dict[int, PlanRequest] = {}
        if need_masked:
            # Re-plan requests from current positions: the flow_unit rides its
            # BOARDED legs out (declared physics), so position = target of
            # the last boarded hop, ready = max(arrival there, t0); flow_units
            # not yet appeared re-plan from their origin/appear.
            # Materialization feasibility against ALL accumulated node
            # windows: a not-yet-appeared candidate whose origin is outaged
            # at its appear time strands at injection regardless of any
            # plan — doomed, excluded from certification (the symmetric
            # commitment-side check to the epoch-side doom scan; found by
            # the zero-divergence assertion on multi-epoch suites).
            full_scope = list(affected) + [r.flow_unit_id for r in surge_reqs]
            for sid in list(full_scope):
                r = req_by_sid[sid]
                mat_node, mat_t = pending_arrival.get(
                    sid, (r.origin, r.appear_atu))
                if mat_t >= t0 and any(
                        h == mat_node and ws <= mat_t < we
                        for h, ws, we in node_windows):
                    flow[sid] = "disrupted_doomed"
                    plan_hops.pop(sid, None)
                    full_scope.remove(sid)
            for sid in full_scope:
                r = req_by_sid[sid]
                hops = hops_snapshot.get(sid)
                mat_node, mat_t = pending_arrival.get(
                    sid, (r.origin, r.appear_atu))
                if hops is None or mat_t >= t0:
                    pos, ready = mat_node, mat_t
                else:
                    pos, ready = mat_node, mat_t
                    board_t = mat_t
                    for eid, dep, arr in hops:
                        if board_t < t0:  # boarded: it will ride this leg
                            pos, ready = emeta[eid][1], arr
                            board_t = arr
                        else:
                            break
                    ready = max(ready, t0)
                replan_req_by_sid[sid] = PlanRequest(
                    sid, pos, r.destination, ready, r.due_atu,
                    r.size_units, r.priority_class)

        # ── Phase-2 scoring pass (no commitment; never-leg instrument) ────
        sev_score = len(affected) + len(surge_reqs)
        n_recert = 0
        degraded_ub = 0
        if score_epochs and need_masked:
            dry = CapacityAwarePlanner(policy=policy)
            dry_reqs = sorted(replan_req_by_sid.values(),
                              key=lambda r: r.flow_unit_id)
            dry_res = dry.plan(dry_reqs, masked_ctx)
            n_recert = sum(1 for r in dry_res if r.status == "planned")
            # Capacity-free UB on the degraded fate of the AFFECTED set
            # (surge excluded: its no-op fate has no commitment to lose),
            # over the structurally masked timetable.
            shim = PlanContext(
                n_nodes=ctx.n_nodes, n_lanes=ctx.n_lanes,
                adjacency=ctx.adjacency,
                schedule_departures=structural_sched,
                lane_id_to_csr=ctx.lane_id_to_csr,
                bucket_atu=ctx.bucket_atu,
            )
            conns = CapacityAwarePlanner._connections_desc(shim)
            prof_cache: dict[int, Any] = {}
            for sid in affected:
                rr = replan_req_by_sid[sid]
                prof = prof_cache.get(rr.destination)
                if prof is None:
                    prof = CapacityAwarePlanner._build_profile(
                        rr.destination, ctx.n_nodes, conns)
                    prof_cache[rr.destination] = prof
                arr = (rr.appear_atu if rr.origin == rr.destination
                       else prof.eval(rr.origin, rr.appear_atu))
                if arr <= rr.due_atu:
                    degraded_ub += 1

        if decision and (affected or surge_reqs):
            cands = propose.propose(features, tuple(affected),
                                    tuple(r.flow_unit_id for r in surge_reqs))
            ests = [float(imagine.estimate(features, c)) for c in cands]
            sel = navigate_argmax(cands, ests)
            chosen = cands[sel]
            replan_reqs = sorted(
                (replan_req_by_sid[sid] for sid in chosen.sids
                 if sid in replan_req_by_sid),
                key=lambda r: r.flow_unit_id)

            re_planner = CapacityAwarePlanner(policy=policy)
            re_results = re_planner.plan(replan_reqs, masked_ctx)
            assert_plan_capacity_legal(re_planner.last_decision_trace,
                                       masked_ctx, sizes)
            apply_plan(eng, re_results, masked_ctx,
                       decision_trace=re_planner.last_decision_trace,
                       sizes=sizes, pinned=True)
            for rec in re_planner.last_decision_trace:
                sid = rec.flow_unit_id
                if rec.status == "planned":
                    rr = replan_req_by_sid[sid]
                    pending_arrival[sid] = (rr.origin, rr.appear_atu)
                    hops = []
                    for eid, slot_pos in rec.path_slots:
                        dep = masked_sched[eid][slot_pos][0]
                        hops.append((eid, dep, dep + emeta[eid][2]))
                    plan_hops[sid] = hops
                    plan_arrival[sid] = rec.planned_arrival_atu
                    flow[sid] = ("surge_committed"
                                 if sid >= SURGE_ID_BASE else "recommitted")
                    n_recommitted += 1
                else:
                    flow[sid] = ("surge_degraded"
                                 if sid >= SURGE_ID_BASE else "refused_epoch")
                    n_refused_epoch += 1
                    refused_reasons[rec.reason] = (
                        refused_reasons.get(rec.reason, 0) + 1)
                    plan_hops.pop(sid, None)
        n_degraded = sum(1 for f in flow.values() if f == "degraded")
        n_committed = sum(1 for f in flow.values()
                          if f in ("committed_intact", "recommitted",
                                   "surge_committed"))
        epoch_traces.append(EpochTrace(
            features=features, gate_decision=decision,
            candidates=[c.name for c in cands], estimates=ests, selected=sel,
            n_cancelled=n_cancelled, n_recommitted=n_recommitted,
            n_refused_epoch=n_refused_epoch, n_degraded=n_degraded,
            refused_reasons=refused_reasons,
            severity_score=sev_score,
            value_estimate=float(n_recert - degraded_ub),
            n_recertifiable=n_recert,
            degraded_feasible_ub=degraded_ub,
        ))

    # ── run out the horizon and account ───────────────────────────────────
    t_end = truncate_at if truncate_at is not None else horizon_atu
    eng.step_until(t_end)
    eng.finalize()
    kpis = json.loads(eng.get_kpis())

    dl = eng.delivery_records()
    realized = {int(s): int(t) for s, t in
                zip(dl["flow_unit_id"], dl["delivered_atu"])}
    due_of = {sid: r.due_atu for sid, r in req_by_sid.items()}

    flows: dict[str, int] = {}
    guaranteed_on_time = 0
    for sid, f in flow.items():
        t = realized.get(sid)
        if f in ("committed_intact", "recommitted", "surge_committed"):
            if truncate_at is not None and plan_arrival[sid] > truncate_at:
                # certified-pending: realizes after the cut (theorem-backed).
                flows[f"{f}_pending"] = flows.get(f"{f}_pending", 0) + 1
                guaranteed_on_time += 1
                continue
            # zero-divergence: must be delivered exactly at its final plan.
            assert t is not None and t == plan_arrival[sid], (
                f"committed flow_unit {sid} diverged: realized {t} vs "
                f"planned {plan_arrival[sid]}"
            )
            flows[f"{f}_on_time"] = flows.get(f"{f}_on_time", 0) + 1
            guaranteed_on_time += 1
        else:
            if t is None:
                tag = f"{f}_stranded"
            elif t <= due_of[sid]:
                tag = f"{f}_on_time"  # post-release recovery: typed, not a bug
            else:
                tag = f"{f}_late"
            flows[tag] = flows.get(tag, 0) + 1

    h = hashlib.sha256()
    h.update(json.dumps(flows, sort_keys=True).encode())
    for tr in epoch_traces:
        h.update(f"{tr.features.epoch_idx}|{tr.gate_decision}|"
                 f"{tr.n_cancelled}|{tr.n_recommitted}|"
                 f"{tr.n_refused_epoch}".encode())

    return LoopResult(
        flows=flows, epoch_traces=epoch_traces, kpis=kpis,
        n_demand_total=len(req_by_sid),
        guaranteed_on_time=guaranteed_on_time,
        digest=h.hexdigest(),
    )
