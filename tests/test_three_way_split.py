"""C1.5 three-way split + the opportunistic-on-time ≡ 0 invariant.

The refusal-certificate theorem (fidelity.py docstring) predicts ZERO
opportunistic on-time deliveries whenever certification is correct; any
violation is a certification bug, not an outcome. Verified on the golden
world with opportunistic traffic and on Regime B 40k mixed execution, for
every commit policy.
"""

from __future__ import annotations

from epure_arena import _engine
import pyarrow.ipc as ipc
import pytest

from epure_arena.scenarios.golden_worlds import time_window_trap
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.fidelity import apply_plan, three_way_split
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest

_H = 3_600_000


def _setup(bundle):
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        bundle.demand_ipc, _H, "time_aware",
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
        PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                    cols["dest_node_id"][i], cols["appear_atu"][i],
                    cols["due_atu"][i], cols["size_units"][i],
                    cols["priority_class"][i])
        for i in range(dem.num_rows)
    ]
    return eng, ctx, reqs


def _run_split(bundle, horizon_h, policy="slack_first", **kw):
    eng, ctx, reqs = _setup(bundle)
    p = CapacityAwarePlanner(policy=policy, **kw)
    res = p.plan(reqs, ctx)
    sizes = {r.flow_unit_id: r.size_units for r in reqs}
    apply_plan(eng, res, ctx, decision_trace=p.last_decision_trace,
               sizes=sizes, pinned=True)
    eng.set_enforce_capacity(True)
    eng.run(horizon_h * _H)
    return three_way_split(p.last_decision_trace, eng)


class TestTimeWindowTrapSplit:
    """1 guaranteed; the 2 CAPACITY_BLOCKED refusals deliver LATE via the
    residual greedy pass — opportunistic, never on-time (the theorem)."""

    def test_split(self):
        s = _run_split(time_window_trap(n_flow_units=3), 8)
        assert s["n_guaranteed"] == 1
        assert s["guaranteed_on_time"] == 1
        assert s["opportunistic_on_time"] == 0
        assert s["opportunistic_late"] == 2
        assert s["refused_stranded"] == {}  # both delivered, late


class TestRegimeBSplitInvariant:
    @pytest.mark.parametrize("policy,kw", [
        ("slack_first", {}),
        ("value_weighted", {}),
        ("batched_repack", {"wave_size": 4000, "repack_budget": 2}),
    ])
    def test_opportunistic_on_time_is_zero(self, policy, kw):
        spec = ScenarioSpec(
            topology="backbone", n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24, seed=42,
        )
        s = _run_split(generate_scenario(spec), 24, policy=policy, **kw)
        assert s["opportunistic_on_time"] == 0  # the theorem, empirically
        assert s["guaranteed_on_time"] == s["n_guaranteed"]  # pinned invariant
        # zero unexplained mass: guaranteed + opportunistic + refused = demand
        total = (s["n_guaranteed"] + s["opportunistic_on_time"]
                 + s["opportunistic_late"]
                 + sum(s["refused_stranded"].values()))
        assert total == 40_000
