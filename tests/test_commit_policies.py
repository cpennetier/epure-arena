"""C1.5 commit-policy tests — one planner, one flag, mechanisms pinned.

The control (slack_first) is byte-identical to the C1 pins (covered by
test_capacity_aware / test_plan_fidelity). Here: policy validation, the
value_weighted ordering mechanism, the batched_repack exchange mechanism on a
crafted world with a known optimum, per-policy determinism, and the measured
Regime-B 40k policy numbers as regression pins.
"""

from __future__ import annotations

import pytest

from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.interface import PlanContext, PlanRequest

_H = 3_600_000


def _ctx(adjacency, schedule_departures, n_nodes):
    return PlanContext(
        n_nodes=n_nodes, n_lanes=sum(len(a) for a in adjacency),
        adjacency=adjacency, schedule_departures=schedule_departures,
        lane_id_to_csr=list(range(64)),
    )


def _req(sid, o, d, appear, due, prio=2, size=1):
    return PlanRequest(sid, o, d, appear, due, size, prio)


class TestPolicyFlag:
    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError, match="unknown policy"):
            CapacityAwarePlanner(policy="genetic")

    def test_default_is_the_control(self):
        assert CapacityAwarePlanner().policy == "slack_first"


class TestValueWeightedMechanism:
    """Two equal-slack flow_units contend for a capacity-1 slot: slack_first
    commits the lower flow_unit_id (Standard); value_weighted commits the VIP."""

    def _world(self):
        adjacency = [[(1, 0, _H, 0, 0, 0)], []]  # edge0: 0→1, dur 1h
        sched = {0: [(0, 1)]}  # one slot at t=0, capacity 1
        ctx = _ctx(adjacency, sched, 2)
        reqs = [
            _req(10, 0, 1, 0, 2 * _H, prio=2),  # Standard, lower sid
            _req(20, 0, 1, 0, 2 * _H, prio=0),  # VIP, higher sid
        ]
        return ctx, reqs

    def test_slack_first_commits_lower_sid(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="slack_first")
        res = {r.flow_unit_id: r.status for r in p.plan(reqs, ctx)}
        assert res == {10: "planned", 20: "failed"}

    def test_value_weighted_commits_the_vip(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="value_weighted")
        res = {r.flow_unit_id: r.status for r in p.plan(reqs, ctx)}
        assert res == {10: "failed", 20: "planned"}


class TestBatchedRepackMechanism:
    """Crafted exchange-win world (known optimum = 2):

        edge0: 0→1 dur 1h, ONE slot t=0, cap 1   (the contended fast leg)
        edge1: 1→2 dur 1h, slot t=2h, cap 10     (to A's dest)
        edge2: 1→3 dur 1h, slot t=2h, cap 10     (to B's dest)
        edge3: 0→2 dur 4h, slot t=0,  cap 10     (A's slow alternative)

    A (0→2, due 4h): best via fast = 3h ⇒ slack 1h; slow alt arrives 4h ≤ due.
    B (0→3, due 5h): ONLY path is fast ⇒ slack 2h; no alternative.

    slack_first commits A first (tighter slack) onto the fast leg and B is
    CAPACITY_BLOCKED — a maximal packing of 1. batched_repack displaces A to
    the slow path and commits both — the maximum packing of 2."""

    def _world(self):
        adjacency = [
            [(1, 0, _H, 0, 0, 0), (2, 3, 4 * _H, 0, 0, 0)],  # from 0
            [(2, 1, _H, 0, 0, 0), (3, 2, _H, 0, 0, 0)],      # from 1
            [], [],
        ]
        sched = {0: [(0, 1)], 1: [(2 * _H, 10)], 2: [(2 * _H, 10)],
                 3: [(0, 10)]}
        ctx = _ctx(adjacency, sched, 4)
        reqs = [
            _req(1, 0, 2, 0, 4 * _H),  # A
            _req(2, 0, 3, 0, 5 * _H),  # B
        ]
        return ctx, reqs

    def test_slack_first_is_maximal_not_maximum(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="slack_first")
        res = {r.flow_unit_id: r.status for r in p.plan(reqs, ctx)}
        assert res == {1: "planned", 2: "failed"}
        assert p.last_reason_counts == {"CAPACITY_BLOCKED": 1}

    def test_batched_repack_reaches_the_maximum(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="batched_repack", wave_size=10,
                                 repack_budget=2)
        res = {r.flow_unit_id: r.status for r in p.plan(reqs, ctx)}
        assert res == {1: "planned", 2: "planned"}
        via = {d.flow_unit_id: d.commit_via for d in p.last_decision_trace}
        assert via == {1: "repack_rerouted", 2: "repack_inserted"}
        assert p.last_repack_stats["exchanges_accepted"] == 1
        # A rerouted onto the slow alternative, still on time.
        trace = {d.flow_unit_id: d for d in p.last_decision_trace}
        assert trace[1].path_lanes == [3]
        assert trace[1].planned_arrival_atu == 4 * _H

    def test_zero_budget_disables_repack(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="batched_repack", wave_size=10,
                                 repack_budget=0)
        res = {r.flow_unit_id: r.status for r in p.plan(reqs, ctx)}
        assert res == {1: "planned", 2: "failed"}

    def test_capacity_legality_after_repack(self):
        ctx, reqs = self._world()
        p = CapacityAwarePlanner(policy="batched_repack", wave_size=10,
                                 repack_budget=2)
        p.plan(reqs, ctx)
        booked: dict[tuple[int, int], int] = {}
        for d in p.last_decision_trace:
            if d.status == "planned":
                for eid, sp in d.path_slots:
                    booked[(eid, sp)] = booked.get((eid, sp), 0) + 1
        for (eid, sp), n in booked.items():
            assert n <= ctx.schedule_departures[eid][sp][1]


class TestPolicyDeterminism:
    @pytest.mark.parametrize("policy,kw", [
        ("slack_first", {}),
        ("value_weighted", {}),
        ("batched_repack", {"wave_size": 10, "repack_budget": 2}),
    ])
    def test_double_run_identical(self, policy, kw):
        from epure_arena.scenarios.golden_worlds import bottleneck_bridge
        from epure_arena import _engine
        import pyarrow.ipc as ipc
        from epure_arena.world.graph_adapter import build_plan_context

        b = bottleneck_bridge(n_flow_units=10, short_path_capacity=2,
                              long_path_capacity=10, horizon_hours=6)
        eng = _engine.PyEngine(
            b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H,
            "time_aware",
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
            PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                        cols["dest_node_id"][i], cols["appear_atu"][i],
                        cols["due_atu"][i], cols["size_units"][i],
                        cols["priority_class"][i])
            for i in range(dem.num_rows)
        ]
        runs = []
        for _ in range(2):
            p = CapacityAwarePlanner(policy=policy, **kw)
            res = p.plan(reqs, ctx)
            runs.append([(r.flow_unit_id, tuple(r.path), r.status,
                          r.planned_arrival_atu) for r in res])
        assert runs[0] == runs[1]


class TestRegimeB40kPolicyPins:
    """Measured 2026-06-11 (seed 42); the four-policy committed counts are
    regression pins — the C1.5 matrix cites these via tests, not memory."""

    @pytest.fixture(scope="class")
    def regime_b(self):
        from epure_arena import _engine
        import pyarrow.ipc as ipc
        from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
        from epure_arena.world.graph_adapter import build_plan_context

        spec = ScenarioSpec(
            topology="backbone", n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24, seed=42,
        )
        b = generate_scenario(spec)
        eng = _engine.PyEngine(
            b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H,
            "time_aware",
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
            PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                        cols["dest_node_id"][i], cols["appear_atu"][i],
                        cols["due_atu"][i], cols["size_units"][i],
                        cols["priority_class"][i])
            for i in range(dem.num_rows)
        ]
        return ctx, reqs

    def _planned(self, ctx, reqs, policy, **kw):
        p = CapacityAwarePlanner(policy=policy, **kw)
        res = p.plan(reqs, ctx)
        return sum(r.status == "planned" for r in res), p

    def test_value_weighted_pin(self, regime_b):
        ctx, reqs = regime_b
        planned, p = self._planned(ctx, reqs, "value_weighted")
        assert planned == 31273
        prio_of = {r.flow_unit_id: r.priority_class for r in reqs}
        vip = sum(1 for d in p.last_decision_trace
                  if d.status == "planned" and prio_of[d.flow_unit_id] == 0)
        assert vip == 4044  # vs 3,149 under slack_first: +28% VIP coverage

    def test_batched_repack_pin(self, regime_b):
        ctx, reqs = regime_b
        planned, p = self._planned(ctx, reqs, "batched_repack",
                                   wave_size=4000, repack_budget=2)
        assert planned == 31282
        assert p.last_repack_stats["exchanges_accepted"] == 80
