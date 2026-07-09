"""Regression pins for the measured stranding attribution (plan report §3).

The numbers below are the published evidence of the stranding-attribution
analysis (40k cells, seed 42). Per the execution brief they are REGRESSION TESTS, not
citations: this codepath must keep reproducing them exactly. Any drift means
routing/strand behavior changed and must be re-derived, not waved through.

Also covers the Step-0 observability primitives end-to-end through PyO3:
strand-site column, delivery records (conserve with the KPI), and the
off-by-default hop trace.
"""

from __future__ import annotations

import json
from collections import Counter

from epure_arena import _engine
import pytest

from epure_arena.harness.feasibility_oracle import classify_engine
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario

_H = 3_600_000
SITE_ROUTER_NONE = 0
SITE_QUEUE_DEATH = 2


def _run(cadence: str, capacity: str, enforce: bool = True):
    spec = ScenarioSpec(
        topology="backbone", n_nodes=200, density="moderate", capacity=capacity,
        demand_pattern="diurnal", schedule_cadence=cadence, priority_mix="mixed",
        n_demand_events=40_000, horizon_hours=24, seed=42,
    )
    b = generate_scenario(spec)
    eng = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
    )
    eng.set_enforce_capacity(enforce)
    eng.run(24 * _H)
    return eng


@pytest.fixture(scope="module")
def regime_b():
    """hourly/tight — the router-induced-dominant regime (§3.2)."""
    return _run("hourly", "tight")


@pytest.fixture(scope="module")
def regime_a():
    """every_4h/loose — the genuine/cliff-dominant regime (§3.3)."""
    return _run("every_4h", "loose")


class TestRegimeBPins:
    def test_strand_counts(self, regime_b):
        k = json.loads(regime_b.get_kpis())
        assert k["stranded_flow_units"] == 9469
        by_reason = Counter(regime_b.strand_records()["reason"])
        assert by_reason[1] == 4117  # ScheduleInfeasible
        assert by_reason[2] == 5352  # CapacityExhausted

    def test_oracle_split(self, regime_b):
        rep = classify_engine(regime_b)
        assert rep.router_induced == 4033
        assert rep.deadline_bound == 0
        assert rep.genuine == 84
        assert rep.genuine_cliff + rep.genuine_structural == rep.genuine

    def test_all_sched_strands_decide_at_router_none(self, regime_b):
        sr = regime_b.strand_records()
        sites = Counter(
            sr["site"][i] for i in range(len(sr["site"])) if sr["reason"][i] == 1
        )
        assert sites == {SITE_ROUTER_NONE: 4117}

    def test_all_capacity_strands_decide_at_queue_death(self, regime_b):
        sr = regime_b.strand_records()
        sites = Counter(
            sr["site"][i] for i in range(len(sr["site"])) if sr["reason"][i] == 2
        )
        assert sites == {SITE_QUEUE_DEATH: 5352}

    def test_capacity_bucket_had_full_on_time_supply(self, regime_b):
        rep = classify_engine(regime_b, reasons=(2,))
        assert rep.router_induced == 5352  # 100% had on-time capacity-free supply

    def test_delivery_records_conserve_with_kpi(self, regime_b):
        k = json.loads(regime_b.get_kpis())
        dl = regime_b.delivery_records()
        assert len(dl["flow_unit_id"]) == k["total_delivered"] == 30531

    def test_hop_trace_off_by_default(self, regime_b):
        assert len(regime_b.hop_records()["flow_unit_id"]) == 0


class TestRegimeAPins:
    def test_all_genuine_and_all_cliff(self, regime_a):
        k = json.loads(regime_a.get_kpis())
        assert k["stranded_flow_units"] == 19737
        rep = classify_engine(regime_a)
        assert rep.router_induced == 0
        assert rep.deadline_bound == 0
        assert rep.genuine == 19737
        assert rep.genuine_cliff == 19737  # §3.3: 100% timetable cliff
        assert rep.genuine_structural == 0


class TestProfileOracleAgreesWithDijkstra:
    """Step-2 evidence: the destination-rooted profile oracle and the
    per-(origin, release) source-Dijkstra are the SAME math — exact agreement
    on every classified flow_unit of both §3 regimes, and byte-equal across two
    profile runs (determinism)."""

    @pytest.mark.parametrize("regime", ["regime_a", "regime_b"])
    def test_exact_agreement(self, regime, request):
        eng = request.getfixturevalue(regime)
        prof = classify_engine(eng, reasons=(1, 2, 3), method="profile")
        dij = classify_engine(eng, reasons=(1, 2, 3), method="dijkstra")
        assert len(prof.verdicts) == len(dij.verdicts)
        for p, d in zip(prof.verdicts, dij.verdicts):
            assert (p.flow_unit_id, p.arrival_atu, p.verdict, p.genuine_kind) == (
                d.flow_unit_id, d.arrival_atu, d.verdict, d.genuine_kind,
            )

    def test_double_run_byte_equal(self, regime_b):
        a = classify_engine(regime_b, reasons=(1, 2), method="profile")
        b = classify_engine(regime_b, reasons=(1, 2), method="profile")
        assert a.verdicts == b.verdicts


class TestCapacityOffAblation:
    """§3.2: remove capacity and only the GENUINE world-property survives."""

    def test_ablation_leaves_exactly_the_genuine_set(self):
        eng = _run("hourly", "tight", enforce=False)
        k = json.loads(eng.get_kpis())
        assert k["stranded_flow_units"] == 84
        by_reason = Counter(eng.strand_records()["reason"])
        assert by_reason == {1: 84}
        assert k["on_time_pct"] == 1.0
