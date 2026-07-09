"""Greedy-wait commit policy (Gate P1c) — seam fidelity + determinism.

Pinned: the sequential wait-adjusted greedy is deterministic, commits
only capacity-legal routes expressible by the real seam, never commits a
non-positive marginal, and its committed-basis planned value is >= the
raw greedy's PRICED committed value at steep theta on a congested world
(the own-wait ordering property; the full ordering vs the oracle is
asserted in the P1c harness).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

pytest.importorskip("pulp")

import pyarrow.ipc as ipc  # noqa: E402

from epure_arena import _engine  # noqa: E402
from epure_arena.optimize.capacity_aware import CapacityAwarePlanner  # noqa: E402
from epure_arena.optimize.interface import PlanRequest  # noqa: E402
from epure_arena.pricing import STEEP_SCARCE, plan_times, v_realized  # noqa: E402
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario  # noqa: E402
from epure_arena.world.graph_adapter import build_plan_context  # noqa: E402

# greedy_wait is a repro-tier policy (repro/ is not a package).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "repro"))
from greedy_wait import plan_greedy_wait  # noqa: E402

_H = 3_600_000


@pytest.fixture(scope="module")
def world():
    b = generate_scenario(ScenarioSpec(
        topology="grid", n_nodes=9, density="moderate", capacity="tight",
        demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=40, horizon_hours=24, seed=42))
    dem = ipc.open_stream(b.demand_ipc).read_all()
    cols = {k: dem.column(k).to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class")}
    reqs = [PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                        cols["dest_node_id"][i], cols["appear_atu"][i],
                        cols["due_atu"][i], cols["size_units"][i],
                        cols["priority_class"][i])
            for i in range(dem.num_rows)]
    eng = _engine.PyEngine(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                           b.demand_ipc, _H, "time_aware")
    ctx = build_plan_context(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                             eng.get_lane_id_to_csr())
    return b, cols, reqs, ctx


def test_deterministic_and_positive_marginals(world):
    _, _, reqs, ctx = world
    c1 = plan_greedy_wait(reqs, ctx, STEEP_SCARCE)
    c2 = plan_greedy_wait(reqs, ctx, STEEP_SCARCE)
    assert [(c.flow_unit_id, c.path_slots) for c in c1] == \
           [(c.flow_unit_id, c.path_slots) for c in c2]
    assert all(c.marginal_v_realized > 0 for c in c1)
    # Marginals are non-increasing? NOT required (residuals shift), but
    # each committed marginal was the max at its step — spot-check the
    # first exceeds the last.
    if len(c1) >= 2:
        assert c1[0].marginal_v_realized >= c1[-1].marginal_v_realized - 1e-9


def test_context_not_mutated(world):
    _, _, reqs, ctx = world
    before = {eid: list(slots) for eid, slots in ctx.schedule_departures.items()}
    plan_greedy_wait(reqs, ctx, STEEP_SCARCE)
    after = {eid: list(slots) for eid, slots in ctx.schedule_departures.items()}
    assert before == after


def test_capacity_legal_against_original_context(world):
    _, _, reqs, ctx = world
    commits = plan_greedy_wait(reqs, ctx, STEEP_SCARCE)
    sizes = {r.flow_unit_id: r.size_units for r in reqs}
    booked: dict[tuple, int] = {}
    for c in commits:
        for eid, pos in c.path_slots:
            booked[(eid, pos)] = booked.get((eid, pos), 0) + sizes[c.flow_unit_id]
    for (eid, pos), units in booked.items():
        assert units <= ctx.schedule_departures[eid][pos][1]


def test_own_wait_ordering_vs_raw_greedy_planned_value(world):
    """Σ priced marginals of greedy-wait >= the raw greedy's committed
    plan priced at the same theta (the own-wait property, plan-side)."""
    _, cols, reqs, ctx = world
    theta = STEEP_SCARCE
    commits = plan_greedy_wait(reqs, ctx, theta)
    v_gw = sum(c.marginal_v_realized for c in commits)

    planner = CapacityAwarePlanner(policy="value_weighted")
    planner.plan(reqs, ctx)
    appear = dict(zip(cols["flow_unit_id"], cols["appear_atu"]))
    due = dict(zip(cols["flow_unit_id"], cols["due_atu"]))
    size = dict(zip(cols["flow_unit_id"], cols["size_units"]))
    v_raw = 0.0
    for d in planner.last_decision_trace:
        if d.status != "planned" or not d.path_slots:
            continue
        held, delivered = plan_times(d.path_slots, appear[d.flow_unit_id], ctx)
        v_raw += v_realized(held, delivered, due[d.flow_unit_id],
                            size[d.flow_unit_id], theta)
    assert v_gw >= v_raw - 1e-9, (
        f"own-wait greedy planned value {v_gw} < raw greedy {v_raw}")
