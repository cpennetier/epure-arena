"""Cohort-flow upper bound (Step 5) — golden-world exactness and the
certificate lattice.

The cohort LP must reproduce the KNOWN optima of the golden worlds (it is an
assignment-relaxed upper bound, so equality there proves tightness where the
ILP certifies truth), sit at or below the capacity-free oracle count
(lattice), and at or above any achieved execution. time_window_trap is the
regression that caught the prefix/suffix Hall-condition bug: the oracle says
3 feasible-on-time, the true (and LP) optimum is 1.
"""

from __future__ import annotations

from epure_arena import _engine
import pyarrow.ipc as ipc

from epure_arena.harness.feasibility_oracle import feasible_on_time_set
from epure_arena.scenarios.golden_worlds import (
    bottleneck_bridge,
    cascade,
    fallback_required,
    time_window_trap,
)
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest
from epure_arena.lattice.cohort_flow import solve_cohort_flow_upper_bound

_H = 3_600_000


def _bound(bundle, horizon):
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        _H, "time_aware",
    )
    ctx = build_plan_context(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        eng.get_lane_id_to_csr(),
    )
    dem = ipc.open_stream(bundle.demand_ipc).read_all()
    cols = {k: dem[k].to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class",
    )}
    reqs = [
        PlanRequest(
            cols["flow_unit_id"][i], cols["origin_node_id"][i],
            cols["dest_node_id"][i], cols["appear_atu"][i], cols["due_atu"][i],
            cols["size_units"][i], cols["priority_class"][i],
        )
        for i in range(dem.num_rows)
    ]
    fos = feasible_on_time_set(eng, bundle.demand_ipc)
    return solve_cohort_flow_upper_bound(ctx, reqs, fos, horizon_buckets=horizon)


class TestGoldenWorldExactness:
    def test_cascade_matches_ilp_truth(self):
        r = _bound(cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                           slow_capacity=50, horizon_hours=6), 6)
        assert r.solver_status == "optimal"
        assert abs(r.on_time_upper_bound - 30.0) < 1e-6  # ILP delivers all 30
        assert r.lattice_ok

    def test_bottleneck_bridge_matches_truth(self):
        r = _bound(bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                                     long_path_capacity=10, horizon_hours=6), 6)
        assert abs(r.on_time_upper_bound - 10.0) < 1e-6
        assert r.lattice_ok

    def test_fallback_required_matches_truth(self):
        r = _bound(fallback_required(n_flow_units=5), 8)
        assert abs(r.on_time_upper_bound - 5.0) < 1e-6
        assert r.lattice_ok

    def test_time_window_trap_is_tighter_than_oracle(self):
        """The suffix-Hall regression: true optimum 1, oracle 3."""
        r = _bound(time_window_trap(n_flow_units=3), 8)
        assert r.solver_status == "optimal"
        assert abs(r.on_time_upper_bound - 1.0) < 1e-6
        assert r.n_oracle_on_time == 3
        assert r.lattice_ok  # 1 ≤ 3


class TestLatticeAndDeterminism:
    def test_double_solve_identical(self):
        b = bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                              long_path_capacity=10, horizon_hours=6)
        r1, r2 = _bound(b, 6), _bound(b, 6)
        assert r1.on_time_upper_bound == r2.on_time_upper_bound
        assert r1.n_vars == r2.n_vars

    def test_ub_at_least_planner_achieved(self):
        """lattice bottom: achieved (planner commits, all size-1 here)
        ≤ cohort-LP UB."""
        from epure_arena import CapacityAwarePlanner

        b = cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                    slow_capacity=50, horizon_hours=6)
        eng = _engine.PyEngine(
            b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
        )
        ctx = build_plan_context(
            b.node_ipc, b.lane_ipc, b.schedule_ipc, eng.get_lane_id_to_csr(),
        )
        dem = ipc.open_stream(b.demand_ipc).read_all()
        cols = {k: dem[k].to_pylist() for k in (
            "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
            "due_atu", "size_units", "priority_class",
        )}
        reqs = [
            PlanRequest(
                cols["flow_unit_id"][i], cols["origin_node_id"][i],
                cols["dest_node_id"][i], cols["appear_atu"][i],
                cols["due_atu"][i], cols["size_units"][i],
                cols["priority_class"][i],
            )
            for i in range(dem.num_rows)
        ]
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        achieved = sum(r.status == "planned" for r in res)
        bound = _bound(b, 6)
        assert achieved <= bound.on_time_upper_bound + 1e-6
