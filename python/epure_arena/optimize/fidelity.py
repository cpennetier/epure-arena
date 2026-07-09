"""Plan-fidelity instrument (Step 4): planned vs realized execution.

Risk R1 of the plan report is the only threat to the headline claims:
``set_forced_routes`` pins EDGES, not slots, so a feasible plan can realize
differently under congestion (the DES boards each forced lane at its own
``next_departure`` + capacity check + priority-FIFO queues). This module
measures that divergence flow_unit-by-flow_unit by joining the planner's decision
trace (planned arrival per flow_unit) with the DES delivery/strand logs
(realized outcome per flow_unit).

Materiality (the Step-4 gate, recorded with the measurement):
divergence is MATERIAL iff
  (a) any PLANNED flow_unit strands in execution (typed attribution mass would
      revert to untyped strands), or
  (b) |on_time_realized − on_time_planned| / n_planned > 0.005 (would shift
      attribution/regret table cells beyond seed-CI width).

Main exports: :func:`apply_plan`, :func:`measure_fidelity`,
:func:`three_way_split`, :class:`FidelityReport`.

C1.5 addition — the three-way accounting and the refusal-certificate theorem.
``three_way_split`` decomposes every executed run into GUARANTEED (committed,
realizes exactly under pinning), OPPORTUNISTIC (uncommitted deliveries by the
greedy residual pass over ``capacity − reserved``, split on-time/late), and
REFUSED-TYPED (stranded/censored, each carrying its planner reason).

Theorem (certified refusals are unrecoverable): a flow_unit refused
``CAPACITY_BLOCKED`` was certified to have NO capacity-feasible on-time
completion on ``capacity − R_f`` where ``R_f`` are the reservations made
before it in commit order; final reservations ``R ⊇ R_f`` and unplanned usage
only shrink the residual further, and refusals typed ``ENVELOPE_*`` /
``WORLD_*`` are capacity-free-infeasible outright — so **opportunistic
on-time ≡ 0** whenever certification is correct. The hybrid residual pass
(policy d) therefore realizes exactly the control's guaranteed on-time plus
LATE opportunistic mass; it cannot close an on-time gap to greedy. The split
verifies the theorem empirically per run: ``opportunistic_on_time > 0`` is a
certification-bug detector, not an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from epure_arena.optimize.compiler import compile_for_engine
from epure_arena.optimize.interface import PlanContext, PlanResult

MATERIALITY_ON_TIME_SHIFT = 0.005


@dataclass
class FidelityReport:
    """Planned-vs-realized divergence over one executed plan."""

    n_planned: int
    n_planned_delivered: int
    n_planned_stranded: int
    n_planned_censored: int  # neither delivered nor stranded at horizon
    on_time_planned: int  # planned_arrival ≤ due
    on_time_realized: int  # realized arrival ≤ due (among planned flow_units)
    n_exact: int  # realized == planned arrival
    n_late_vs_plan: int  # realized > planned
    n_early_vs_plan: int  # realized < planned (re-routed onto earlier slot)
    delta_atu_p50: int  # percentiles of (realized − planned), delivered only
    delta_atu_p95: int
    delta_atu_max: int
    planned_stranded_ids: list[int] = field(default_factory=list)

    @property
    def on_time_shift(self) -> float:
        if self.n_planned == 0:
            return 0.0
        return abs(self.on_time_realized - self.on_time_planned) / self.n_planned

    @property
    def material(self) -> bool:
        return (
            self.n_planned_stranded > 0
            or self.on_time_shift > MATERIALITY_ON_TIME_SHIFT
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "n_planned": self.n_planned,
            "n_planned_delivered": self.n_planned_delivered,
            "n_planned_stranded": self.n_planned_stranded,
            "n_planned_censored": self.n_planned_censored,
            "on_time_planned": self.on_time_planned,
            "on_time_realized": self.on_time_realized,
            "on_time_shift": round(self.on_time_shift, 6),
            "n_exact": self.n_exact,
            "n_late_vs_plan": self.n_late_vs_plan,
            "n_early_vs_plan": self.n_early_vs_plan,
            "delta_atu_p50": self.delta_atu_p50,
            "delta_atu_p95": self.delta_atu_p95,
            "delta_atu_max": self.delta_atu_max,
            "material": self.material,
        }


def assert_plan_capacity_legal(
    decision_trace: list[Any],
    context: PlanContext,
    sizes: dict[int, int],
) -> None:
    """Raise if the committed plans overbook any (lane, slot). The engine
    re-checks atomically at install; this surfaces the violation with planner
    context BEFORE pinning (the Step-4b contract)."""
    booked: dict[tuple[int, int], int] = {}
    for d in decision_trace:
        if d.status != "planned":
            continue
        for eid, slot_pos in d.path_slots:
            booked[(eid, slot_pos)] = booked.get((eid, slot_pos), 0) + sizes[d.flow_unit_id]
    for (eid, slot_pos), units in booked.items():
        cap = context.schedule_departures[eid][slot_pos][1]
        if units > cap:
            raise ValueError(
                f"plan overbooks lane {eid} slot {slot_pos}: {units} > {cap}"
            )


def apply_plan(
    engine: Any,
    results: list[PlanResult],
    context: PlanContext,
    *,
    decision_trace: list[Any] | None = None,
    sizes: dict[int, int] | None = None,
    pinned: bool = True,
) -> int:
    """Install committed plans on a NOT-YET-RUN engine.

    ``pinned=True`` (default, Option A): slot-pinned forced routes with
    reservations via ``set_forced_routes_pinned`` — requires the planner's
    ``decision_trace`` (for the (lane, slot) hops) and per-flow_unit ``sizes``;
    asserts plan capacity-legality first. A legal plan then realizes exactly.

    ``pinned=False`` (standing ablation): lane-pinned ``set_forced_routes``,
    the slot-free commitment semantics whose measured decoherence is the
    Step-4 result.

    Failed results are skipped either way (they fall back to the engine's own
    router — the greedy baseline). Returns the number of routes installed.
    """
    if not pinned:
        compiled = compile_for_engine(results, context)
        engine.set_forced_routes(compiled)
        return len(compiled)

    if decision_trace is None or sizes is None:
        raise ValueError("pinned=True requires decision_trace and sizes")
    assert_plan_capacity_legal(decision_trace, context, sizes)
    e2c = context.lane_id_to_csr
    routes: list[tuple[int, list[tuple[int, int]]]] = []
    for d in decision_trace:
        if d.status != "planned" or not d.path_slots:
            continue
        hops = [
            (e2c[eid], context.schedule_departures[eid][slot_pos][0])
            for eid, slot_pos in d.path_slots
        ]
        routes.append((d.flow_unit_id, hops))
    return engine.set_forced_routes_pinned(routes)


def three_way_split(
    decision_trace: list[Any],
    engine: Any,
) -> dict[str, int]:
    """Guaranteed / opportunistic / refused-typed decomposition of one
    executed (pinned, mixed-traffic) run. See the module docstring for the
    opportunistic-on-time ≡ 0 theorem this verifies empirically."""
    planned_ids = {d.flow_unit_id for d in decision_trace if d.status == "planned"}
    due_of = {d.flow_unit_id: d.due_atu for d in decision_trace}
    reason_of = {d.flow_unit_id: d.reason for d in decision_trace
                 if d.status != "planned"}

    dl = engine.delivery_records()
    guaranteed_on_time = 0
    opp_on_time = 0
    opp_late = 0
    for s, t in zip(dl["flow_unit_id"], dl["delivered_atu"]):
        sid, t = int(s), int(t)
        if sid in planned_ids:
            guaranteed_on_time += 1 if t <= due_of[sid] else 0
        elif t <= due_of.get(sid, -1):
            opp_on_time += 1
        else:
            opp_late += 1

    sr = engine.strand_records()
    refused_stranded: dict[str, int] = {}
    for s in sr["flow_unit_id"]:
        reason = reason_of.get(int(s))
        if reason is not None:
            refused_stranded[reason] = refused_stranded.get(reason, 0) + 1

    return {
        "guaranteed_on_time": guaranteed_on_time,
        "n_guaranteed": len(planned_ids),
        "opportunistic_on_time": opp_on_time,  # ≡ 0 under correct certification
        "opportunistic_late": opp_late,
        "refused_stranded": refused_stranded,
        "n_refused": len(reason_of),
    }


def measure_fidelity(
    decision_trace: list[Any],
    engine: Any,
) -> FidelityReport:
    """Join the planner's decision trace with the engine's realized logs.

    Args:
        decision_trace: ``CapacityAwarePlanner.last_decision_trace`` records
            (needs ``flow_unit_id``, ``status``, ``planned_arrival_atu``,
            ``due_atu``).
        engine: an ALREADY-RUN PyEngine (delivery_records + strand_records).
    """
    delivered = engine.delivery_records()
    realized: dict[int, int] = {
        int(s): int(t)
        for s, t in zip(delivered["flow_unit_id"], delivered["delivered_atu"])
    }
    stranded: set[int] = {int(s) for s in engine.strand_records()["flow_unit_id"]}

    planned = [d for d in decision_trace if d.status == "planned"]
    deltas: list[int] = []
    n_delivered = n_stranded = n_censored = 0
    on_time_planned = on_time_realized = 0
    n_exact = n_late = n_early = 0
    stranded_ids: list[int] = []

    for d in planned:
        if d.planned_arrival_atu <= d.due_atu:
            on_time_planned += 1
        t = realized.get(d.flow_unit_id)
        if t is not None:
            n_delivered += 1
            if t <= d.due_atu:
                on_time_realized += 1
            delta = t - d.planned_arrival_atu
            deltas.append(delta)
            if delta == 0:
                n_exact += 1
            elif delta > 0:
                n_late += 1
            else:
                n_early += 1
        elif d.flow_unit_id in stranded:
            n_stranded += 1
            stranded_ids.append(d.flow_unit_id)
        else:
            n_censored += 1

    deltas.sort()

    def pct(p: float) -> int:
        return deltas[min(len(deltas) - 1, int(p * len(deltas)))] if deltas else 0

    return FidelityReport(
        n_planned=len(planned),
        n_planned_delivered=n_delivered,
        n_planned_stranded=n_stranded,
        n_planned_censored=n_censored,
        on_time_planned=on_time_planned,
        on_time_realized=on_time_realized,
        n_exact=n_exact,
        n_late_vs_plan=n_late,
        n_early_vs_plan=n_early,
        delta_atu_p50=pct(0.50),
        delta_atu_p95=pct(0.95),
        delta_atu_max=deltas[-1] if deltas else 0,
        planned_stranded_ids=stranded_ids,
    )
