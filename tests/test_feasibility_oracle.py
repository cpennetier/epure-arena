"""Validate the offline feasibility oracle against ILP ground truth.

The ILP (``solve_routing_ilp``) returns the certified optimum WITH capacity.
The oracle ignores capacity, so it is a strict RELAXATION: anything the ILP can
deliver on time, the oracle MUST find FEASIBLE_ON_TIME. Hence the invariant

    oracle.FEASIBLE_ON_TIME  ⊇  ILP-delivered

on scenarios where the ILP strands 0 (cascade, bottleneck_bridge). If the oracle
ever disagrees it is wrong — these tests pin it.
"""

from __future__ import annotations

import pyarrow.ipc as ipc

from epure_arena.harness.feasibility_oracle import (
    GENUINE,
    classify_engine,
    feasible_on_time_set,
)
from epure_arena.scenarios.golden_worlds import bottleneck_bridge, cascade
from epure_arena.lattice.ilp_solver import solve_routing_ilp


def _all_flow_unit_ids(demand_ipc: bytes) -> set[int]:
    table = ipc.open_stream(demand_ipc).read_all()
    return {int(s) for s in table.column("flow_unit_id").to_pylist()}


def _engine(bundle, router: str = "time_aware"):
    from epure_arena import _engine

    # earliest_arrival reads only graph + schedules, valid right after
    # construction — no run needed for the relaxation queries.
    return _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        3_600_000, router,
    )


def test_oracle_feasible_on_time_superset_of_ilp_cascade():
    b = cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                slow_capacity=50, horizon_hours=6)
    sol = solve_routing_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc,
                            horizon_hours=6)
    assert sol.solver_status == "Optimal"
    assert sol.n_stranded == 0  # ILP delivers everything

    delivered = _all_flow_unit_ids(b.demand_ipc) - set(sol.stranded_flow_unit_ids)
    fos = feasible_on_time_set(_engine(b), b.demand_ipc)

    missing = delivered - fos
    assert not missing, (
        f"oracle missed {len(missing)} ILP-delivered flow_units as not "
        f"FEASIBLE_ON_TIME: {sorted(missing)[:10]} — the oracle is wrong"
    )


def test_oracle_feasible_on_time_superset_of_ilp_bottleneck():
    b = bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                          long_path_capacity=10, horizon_hours=6)
    sol = solve_routing_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc,
                            horizon_hours=6)
    assert sol.solver_status == "Optimal"
    assert sol.n_stranded == 0

    delivered = _all_flow_unit_ids(b.demand_ipc) - set(sol.stranded_flow_unit_ids)
    fos = feasible_on_time_set(_engine(b), b.demand_ipc)
    assert delivered <= fos


def test_oracle_never_labels_a_feasible_scenario_genuine():
    """In cascade every flow_unit has a schedule path, so NO schedule_infeasible
    strand from a real DES run may be classified GENUINE; the three buckets must
    sum to the schedule_infeasible total.
    """
    from epure_arena import _engine

    b = cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                slow_capacity=50, horizon_hours=6)
    eng = _engine.PyEngine(b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc,
                              3_600_000, "time_aware")
    eng.set_enforce_capacity(True)
    eng.run(6 * 3_600_000)

    report = classify_engine(eng)
    # Cascade is fully schedule-feasible → no genuine shortage possible.
    assert report.genuine == 0, (
        f"oracle wrongly flagged {report.genuine} feasible flow_units as GENUINE"
    )
    assert (
        report.router_induced + report.deadline_bound + report.genuine
        == report.n_schedule_infeasible
    )
    assert all(v.verdict != GENUINE for v in report.verdicts)
