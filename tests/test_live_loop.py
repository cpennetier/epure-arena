"""C2 Phase-1 live-loop tests: golden counterfactual, mass closure,
byte-identity, no-disruption batch equivalence, and the time-indexed
certificate semantics.

The golden disruption world has an EXACTLY known counterfactual: ex-ante,
one flow_unit pins the single fast slot (cap 1) and four take the slow path; an
lane outage cancels the fast slot before boarding. No-op: the disrupted
flow_unit degrades to greedy, queues on the outaged lane, and strands (no later
slot). Intervene: it re-certifies onto the slow path and delivers on time.
Δ(guaranteed) = +1 exactly.
"""

from __future__ import annotations

from epure_arena.gate import AlwaysOffGate, AlwaysOnGate

from epure_arena.scenarios.golden_worlds import fallback_required
from epure_arena.scenarios.disruptions import Disruption, make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.harness.live_loop import run_live_loop

_H = 3_600_000


def _regime_b(n=40_000):
    return generate_scenario(ScenarioSpec(
        topology="backbone", n_nodes=200, density="moderate", capacity="tight",
        demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=n, horizon_hours=24, seed=42,
    ))


class TestGoldenDisruptionCounterfactual:
    """fallback_required + an outage on the fast lane's only slot."""

    def _world(self):
        b = fallback_required(n_flow_units=5)
        # Lane 0's single slot departs at t=0; outage [0, 2h) kills it
        # before boarding (the loop pauses at onset 0, cancels, then the
        # boarding event at t=0 sees the outage).
        d = Disruption(kind="lane_outage", onset_atu=0, end_atu=2 * _H,
                       lane_ids=(0,),
                       descriptor={"kind": "lane_outage", "severity": "golden"})
        return b, [d]

    def test_noop_strands_the_disrupted_flow_unit(self):
        b, ds = self._world()
        res = run_live_loop(b, ds, gate=AlwaysOffGate(), horizon_hours=8)
        assert res.mass_closed
        assert res.flows["committed_intact_on_time"] == 4  # slow-path flow_units
        assert res.flows["degraded_stranded"] == 1
        assert res.guaranteed_on_time == 4

    def test_intervention_recommits_onto_the_slow_path(self):
        b, ds = self._world()
        res = run_live_loop(b, ds, gate=AlwaysOnGate(), horizon_hours=8)
        assert res.mass_closed
        assert res.flows["committed_intact_on_time"] == 4
        assert res.flows["recommitted_on_time"] == 1
        assert res.guaranteed_on_time == 5  # Δ = +1 exactly vs no-op
        assert res.epoch_traces[0].n_cancelled == 1
        assert res.epoch_traces[0].n_recommitted == 1


class TestLoopInvariants:
    def test_no_disruption_equals_batch_and_theorem_holds(self):
        """An empty disruption list reduces the loop to the C1 pinned batch:
        the value_weighted Regime-B pin (31,273 committed) reproduces, and
        the refusal-certificate theorem holds (no refused on-time flow)."""
        b = _regime_b()
        res = run_live_loop(b, [], gate=AlwaysOffGate(), horizon_hours=24)
        assert res.mass_closed
        assert res.flows["committed_intact_on_time"] == 31273  # C1.5 pin
        assert res.guaranteed_on_time == 31273
        assert "refused_exante_on_time" not in res.flows  # theorem, undisrupted

    def test_mass_closure_and_byte_identity_multi_kind(self):
        b = _regime_b()
        ds = make_disruptions(
            b, kinds=("lane_outage", "capacity_shock", "demand_surge"),
            severity="moderate", horizon_hours=24, seed=11,
        )
        r1 = run_live_loop(b, ds, gate=AlwaysOnGate(), horizon_hours=24)
        r2 = run_live_loop(b, ds, gate=AlwaysOnGate(), horizon_hours=24)
        assert r1.mass_closed and r2.mass_closed
        assert r1.digest == r2.digest  # byte-identical same-seed
        # Zero unexplained mass across base + surge demand:
        assert sum(r1.flows.values()) == r1.n_demand_total

    def test_post_release_recovery_is_typed_not_asserted(self):
        """Under disruption, releases break reservation monotonicity, so
        certified refusals MAY deliver on-time afterwards — a typed flow
        (time-indexed certificates), never an assertion failure."""
        b = _regime_b()
        ds = make_disruptions(b, kinds=("lane_outage",), severity="severe",
                              horizon_hours=24, seed=7)
        res = run_live_loop(b, ds, gate=AlwaysOffGate(), horizon_hours=24)
        assert res.mass_closed
        assert res.flows.get("refused_exante_on_time", 0) > 0  # measured 34

    def test_node_outage_dooms_inflight_typed(self):
        b = _regime_b()
        ds = make_disruptions(b, kinds=("node_outage",), severity="severe",
                              horizon_hours=24, seed=3)
        res = run_live_loop(b, ds, gate=AlwaysOnGate(), horizon_hours=24)
        assert res.mass_closed
        # Doomed flow_units (in-flight into outage, or materializing at an
        # outaged origin) are typed disrupted_doomed_*, never silently lost.
        doomed = sum(v for k, v in res.flows.items()
                     if k.startswith("disrupted_doomed"))
        assert doomed > 0  # this seed dooms committed flow_units
        stranded_node = res.kpis["strand_reasons"]["node_outage"]["count"]
        assert doomed <= stranded_node  # doom set ⊆ engine NodeOutage strands
