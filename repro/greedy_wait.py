"""Greedy-wait: sequential wait-adjusted marginal commitment (Gate P1c).

The own-wait heuristic baseline: at each step, every uncommitted unit is
routed by the REAL commit seam (``CapacityAwarePlanner`` — same search,
same feasible region as the certified executor) against the CURRENT
residual capacities, its wait-adjusted marginal value
``Δ_j = v_realized(held_j, delivered_j)`` is priced from the plan-side
exact slots (``epure_arena.pricing.plan_times`` — the single pricing
source), and the best positive marginal commits, decrementing residuals
along its slots. Stops when no positive marginal remains.

Well-posedness (checked against the code, per the P1c spec):
``CapacityAwarePlanner.plan()`` rebuilds its residual table from
``context.schedule_departures`` on every call and does not mutate the
context, so sequential single-unit calls against a residual-decremented
context copy ARE the seam's own route search under sequential
commitment — no reinterpretation involved.

This baseline prices each unit's OWN wait only: the marginal is blind to
the wait a commitment inflicts on units committed later. The gap it
leaves against the oracle (R_wait) is therefore the inflicted-wait
externality — the quantity P1c measures.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from epure_arena.optimize.capacity_aware import CapacityAwarePlanner
from epure_arena.optimize.interface import PlanContext, PlanRequest
from epure_arena.pricing import WaitCostProfile, plan_times, v_realized


@dataclass(frozen=True)
class WaitCommit:
    """One committed unit: the seam's route + its priced marginal."""

    flow_unit_id: int
    status: str                       # "planned" (apply_plan contract)
    path_slots: list                  # [(lane_id, slot_pos)]
    marginal_v_realized: float
    planned_held_atu: int
    planned_delivered_atu: int


def _residual_ctx(ctx: PlanContext) -> PlanContext:
    """Shallow context copy whose schedule_departures is deep-copied so
    per-commit capacity decrements never touch the caller's context."""
    work = copy.copy(ctx)
    work.schedule_departures = {
        eid: list(slots) for eid, slots in ctx.schedule_departures.items()
    }
    return work


def plan_greedy_wait(
    requests: list[PlanRequest],
    ctx: PlanContext,
    theta: WaitCostProfile,
) -> list[WaitCommit]:
    """Sequential wait-adjusted greedy commitment. Deterministic: ties on
    the marginal break by ascending flow_unit_id.

    Returns commits in commitment order; capacity-legality against the
    ORIGINAL context holds because slot positions are shared and every
    decrement is mirrored from a successful seam route.
    """
    work = _residual_ctx(ctx)
    remaining: dict[int, PlanRequest] = {r.flow_unit_id: r for r in requests}
    commits: list[WaitCommit] = []
    planner = CapacityAwarePlanner(policy="value_weighted")

    while remaining:
        best: WaitCommit | None = None
        for sid in sorted(remaining):
            req = remaining[sid]
            planner.plan([req], work)
            d = planner.last_decision_trace[0]
            if d.status != "planned" or not d.path_slots:
                continue
            held, delivered = plan_times(d.path_slots, req.appear_atu, work)
            dv = v_realized(held, delivered, req.due_atu, req.size_units,
                            theta)
            if best is None or dv > best.marginal_v_realized:
                best = WaitCommit(sid, "planned", list(d.path_slots), dv,
                                  held, delivered)
        if best is None or best.marginal_v_realized <= 0.0:
            break  # no positive wait-adjusted marginal remains
        size = remaining[best.flow_unit_id].size_units
        for eid, pos in best.path_slots:
            dep, cap = work.schedule_departures[eid][pos]
            assert cap >= size, (
                f"greedy-wait residual underflow at lane {eid} pos {pos}")
            work.schedule_departures[eid][pos] = (dep, cap - size)
        commits.append(best)
        del remaining[best.flow_unit_id]
    return commits
