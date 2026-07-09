"""CapacityAwarePlanner — golden worlds, determinism, capacity legality, and
the Regime-B headline regression (plan report §7 Step 3).

Golden adversarial worlds have KNOWN optimal outcomes and KNOWN greedy failure
modes; the planner must hit the optimum where it exists and type every failure.
The Regime-B pins are published evidence, re-derived in this codepath.
"""

from __future__ import annotations

from collections import Counter

from epure_arena import _engine
import pyarrow.ipc as ipc

from epure_arena.scenarios.golden_worlds import (
    bottleneck_bridge,
    cascade,
    fallback_required,
    time_window_trap,
)
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner, default_registry
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest

_H = 3_600_000
SITE_QUEUE_DEATH = 2


def _ctx_and_requests(bundle):
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        _H, "time_aware",
    )
    ctx = build_plan_context(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        eng.get_lane_id_to_csr(),
    )
    dem = ipc.open_stream(bundle.demand_ipc).read_all()
    cols = {c: dem[c].to_pylist() for c in (
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


def _assert_capacity_legal(planner, ctx):
    """No (lane, slot) is overbooked by the committed plans."""
    booked: Counter = Counter()
    sizes = {d.flow_unit_id: d for d in planner.last_decision_trace}
    for d in planner.last_decision_trace:
        if d.status != "planned":
            continue
        for eid, slot_pos in d.path_slots:
            booked[(eid, slot_pos)] += 1  # size_units == 1 in these worlds
    for (eid, slot_pos), n in booked.items():
        cap = ctx.schedule_departures[eid][slot_pos][1]
        assert n <= cap, f"overbooked lane {eid} slot {slot_pos}: {n} > {cap}"
    assert sizes  # trace populated


class TestGoldenWorlds:
    def test_cascade_plans_everything(self):
        b = cascade(n_flow_units=30, fast_capacity=5, medium_capacity=20,
                    slow_capacity=50, horizon_hours=6)
        _eng, ctx, reqs = _ctx_and_requests(b)
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        assert sum(r.status == "planned" for r in res) == 30  # ILP strands 0
        assert p.last_reason_counts == {}
        _assert_capacity_legal(p, ctx)

    def test_bottleneck_bridge_plans_everything(self):
        b = bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                              long_path_capacity=10, horizon_hours=6)
        _eng, ctx, reqs = _ctx_and_requests(b)
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        assert sum(r.status == "planned" for r in res) == 10
        _assert_capacity_legal(p, ctx)

    def test_fallback_required_planner_uses_alternative_path(self):
        b = fallback_required(n_flow_units=5)
        eng, ctx, reqs = _ctx_and_requests(b)
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        # Planner: optimal_stranded == 0 — one fast, four slow.
        assert sum(r.status == "planned" for r in res) == 5
        _assert_capacity_legal(p, ctx)
        slow_users = sum(1 for d in p.last_decision_trace if 2 in d.path_lanes)
        assert slow_users == 4

        # Known greedy failure: everyone chases e0's single slot; the queue
        # dies (site QueueDeath) despite the feasible slow path.
        eng.set_enforce_capacity(True)
        eng.run(8 * _H)
        sr = eng.strand_records()
        assert len(sr["flow_unit_id"]) == 4
        assert set(sr["site"]) == {SITE_QUEUE_DEATH}

    def test_time_window_trap_types_true_capacity_shortfall(self):
        b = time_window_trap(n_flow_units=3)
        eng, ctx, reqs = _ctx_and_requests(b)
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        # Exactly one on-time plan exists; the other two are CAPACITY_BLOCKED
        # (capacity-free supply was on time — the canonical LP-refinement case).
        assert sum(r.status == "planned" for r in res) == 1
        assert p.last_reason_counts == {"CAPACITY_BLOCKED": 2}

        # Known greedy behavior: the queued flow_units deliver LATE — the
        # shortfall surfaces as silent SLA misses, not typed failures.
        eng.set_enforce_capacity(True)
        eng.run(8 * _H)
        import json
        k = json.loads(eng.get_kpis())
        assert k["total_delivered"] == 3
        assert k["total_late"] == 2


class TestDeterminism:
    def test_double_run_identical(self):
        b = bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                              long_path_capacity=10, horizon_hours=6)
        _eng, ctx, reqs = _ctx_and_requests(b)
        runs = []
        for _ in range(2):
            p = CapacityAwarePlanner()
            res = p.plan(reqs, ctx)
            runs.append([
                (r.flow_unit_id, tuple(r.path), r.status, r.reason,
                 r.planned_arrival_atu)
                for r in res
            ])
        assert runs[0] == runs[1]


class TestRegimeBHeadline:
    """Pins the published Step-3 benchmark numbers (Regime B, 40k, seed 42):
    planner 31,221 planned vs greedy 30,531 delivered; every failure typed;
    zero unexplained mass."""

    def test_regime_b_40k_attribution(self):
        spec = ScenarioSpec(
            topology="backbone", n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24, seed=42,
        )
        b = generate_scenario(spec)
        _eng, ctx, reqs = _ctx_and_requests(b)
        p = CapacityAwarePlanner()
        res = p.plan(reqs, ctx)
        planned = sum(r.status == "planned" for r in res)
        assert planned == 31221
        assert p.last_reason_counts == {
            "CAPACITY_BLOCKED": 8695,
            "ENVELOPE_CLIFF": 84,
        }
        assert planned + sum(p.last_reason_counts.values()) == 40_000

        # Capacity legality over the full 40k commitment (size_units vary).
        booked: Counter = Counter()
        size_of = {r.flow_unit_id: r for r in reqs}
        for d in p.last_decision_trace:
            if d.status == "planned":
                for eid, slot_pos in d.path_slots:
                    booked[(eid, slot_pos)] += size_of[d.flow_unit_id].size_units
        for (eid, slot_pos), units in booked.items():
            cap = ctx.schedule_departures[eid][slot_pos][1]
            assert units <= cap


class TestRegistryWiring:
    def test_capacity_aware_registered(self):
        assert "capacity_aware" in default_registry
        assert default_registry.get("capacity_aware") is CapacityAwarePlanner

    # The application-level StrategySelector (legacy planner family) lives
    # downstream; its registry wiring is covered by the downstream suite.
