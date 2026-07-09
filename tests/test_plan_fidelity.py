"""Plan-fidelity instrument (Step 4) — golden-world zero pins and the measured
Regime-B divergence (the gate evidence of 2026-06-10).

The golden worlds MUST execute with zero divergence (every planned flow_unit
delivers at exactly its planned arrival). Regime B's divergence is pinned as a
regression number: it is the MEASURED cost of lane-pinned (slot-free) forced
routes under congestion — the Step-4 gate verdict (material) that routed the
slot-reservation decision to the PI.
"""

from __future__ import annotations

import pyarrow.ipc as ipc

from epure_arena import _engine

from epure_arena.scenarios.golden_worlds import (
    bottleneck_bridge,
    cascade,
    fallback_required,
)
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.fidelity import apply_plan, measure_fidelity
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest

_H = 3_600_000


def _setup(bundle):
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
    return eng, ctx, reqs


def _plan_execute_measure(bundle, horizon_h, *, pinned=False):
    eng, ctx, reqs = _setup(bundle)
    p = CapacityAwarePlanner()
    res = p.plan(reqs, ctx)
    sizes = {r.flow_unit_id: r.size_units for r in reqs}
    apply_plan(eng, res, ctx, decision_trace=p.last_decision_trace,
               sizes=sizes, pinned=pinned)
    eng.set_enforce_capacity(True)
    eng.run(horizon_h * _H)
    return measure_fidelity(p.last_decision_trace, eng), eng


class TestGoldenWorldsZeroDivergence:
    def test_cascade_exact(self):
        rep, _ = _plan_execute_measure(
            cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                    slow_capacity=50, horizon_hours=6), 6,
        )
        assert rep.n_exact == rep.n_planned == rep.n_planned_delivered == 30
        assert rep.n_planned_stranded == 0
        assert rep.delta_atu_max == 0
        assert not rep.material

    def test_bottleneck_bridge_exact(self):
        rep, _ = _plan_execute_measure(
            bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                              long_path_capacity=10, horizon_hours=6), 6,
        )
        assert rep.n_exact == 10 and rep.delta_atu_max == 0
        assert not rep.material

    def test_fallback_required_exact(self):
        rep, _ = _plan_execute_measure(fallback_required(n_flow_units=5), 8)
        assert rep.n_exact == 5 and rep.n_planned_stranded == 0
        assert not rep.material


class TestSlotFreeAblationPins:
    """The STANDING ABLATION (slot-free commitment semantics, kept behind
    pinned=False per the Option-A decision): the measured Step-4 gate numbers
    (Regime B, 40k, seed 42, mixed traffic) — 6,303 of 31,221 planned flow_units
    strand; on-time shift 32.2%. This is the commitment-semantics result the
    paper cites; it must not drift silently."""

    def test_mixed_traffic_divergence_slot_free(self):
        spec = ScenarioSpec(
            topology="backbone", n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24, seed=42,
        )
        rep, _ = _plan_execute_measure(generate_scenario(spec), 24)
        assert rep.n_planned == 31221
        assert rep.n_planned_stranded == 6303
        assert rep.on_time_realized == 21157
        assert rep.n_exact == 10202
        assert rep.material  # the gate verdict


class TestPinnedExecutionZeroInvariant:
    """Option A (slot-pinned + reservations): a capacity-legal plan realizes
    EXACTLY — zero divergence is an invariant, not a target. MissedPinnedSlot
    (StrandSite 6) is the bug detector and must be absent."""

    def test_golden_worlds_pinned_exact(self):
        for bundle, horizon in (
            (cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                     slow_capacity=50, horizon_hours=6), 6),
            (bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                               long_path_capacity=10, horizon_hours=6), 6),
            (fallback_required(n_flow_units=5), 8),
        ):
            rep, eng = _plan_execute_measure(bundle, horizon, pinned=True)
            assert rep.n_exact == rep.n_planned == rep.n_planned_delivered
            assert rep.n_planned_stranded == 0 and rep.delta_atu_max == 0
            assert 6 not in set(eng.strand_records()["site"])

    def test_regime_b_pinned_mixed_traffic_exact(self):
        spec = ScenarioSpec(
            topology="backbone", n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24, seed=42,
        )
        rep, eng = _plan_execute_measure(generate_scenario(spec), 24, pinned=True)
        assert rep.n_planned == 31221
        assert rep.n_exact == 31221  # every planned flow_unit realizes exactly
        assert rep.n_planned_stranded == 0
        assert rep.on_time_realized == 31221
        assert not rep.material
        assert 6 not in set(eng.strand_records()["site"])  # no MissedPinnedSlot
        # Unplanned mass under reservations (the two-class semantics number,
        # published in the commitment-semantics artifact): system-level totals
        # dominate the all-greedy baseline.
        import json
        k = json.loads(eng.get_kpis())
        assert k["total_delivered"] == 32014
        assert k["total_on_time"] == 31221
        assert k["stranded_flow_units"] == 7986
