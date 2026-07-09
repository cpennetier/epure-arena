"""Conservative wait-pricing ILP (Gate P1a) — the oracle's load-bearing
claims, pinned small.

  * TIMING: a unit appearing mid-bucket must NOT board that bucket's slot
    (engine ``depart ≥ ready``); the legacy floor-rounding would.
  * CHORDS: the secant envelope OVER-states the convex cost everywhere on
    the covered range (the conservatism direction), and is exact at the
    breakpoints.
  * END-TO-END: on a small scenario the solver certifies, the claimed
    value equals the true-functional recomputation (definitional), the
    chord-priced objective is ≤ the claimed value, and every committed
    boarding satisfies depart ≥ ready on exact times.
"""

from __future__ import annotations

import pytest

pulp = pytest.importorskip("pulp")

from epure_arena.lattice.ilp_wait import (  # noqa: E402
    WaitIlpSolution, _chords, _max_chord_overcharge, solve_wait_ilp,
)
from epure_arena.pricing import (  # noqa: E402
    STEEP_SCARCE, SLACK_ELASTIC, v_realized,
)
from epure_arena.scenarios.library import (  # noqa: E402
    ScenarioSpec, generate_scenario,
)

_H = 3_600_000


class TestChords:
    @pytest.mark.parametrize("theta", [STEEP_SCARCE, SLACK_ELASTIC],
                             ids=lambda t: t.name)
    def test_chord_envelope_overstates_convex_cost(self, theta):
        bps = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 24.0)
        chords = _chords(theta, bps)
        # Dense grid over [0, 24h]: max(chord lines) >= true C, == at bps.
        for k in range(0, 24 * 10 + 1):
            w_h = k / 10.0
            w_atu = w_h * _H
            true_c = theta.a_per_hour * w_h + theta.b_per_hour2 * w_h * w_h
            env = max(a * w_atu + b for a, b in chords)
            assert env >= true_c - 1e-12, f"under-charge at w={w_h}h"
            if w_h in bps:
                assert env == pytest.approx(true_c, abs=1e-9)

    def test_reported_overcharge_bounds_the_grid(self):
        bps = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 24.0)
        worst = _max_chord_overcharge(STEEP_SCARCE, bps)
        chords = _chords(STEEP_SCARCE, bps)
        for k in range(0, 24 * 10 + 1):
            w_h = k / 10.0
            true_c = (STEEP_SCARCE.a_per_hour * w_h
                      + STEEP_SCARCE.b_per_hour2 * w_h * w_h)
            env = max(a * (w_h * _H) + b for a, b in chords)
            assert env - true_c <= worst + 1e-9


class TestBackendGuards:
    """The oracle guards its own known failure mode (O2-A): CBC presolve
    returns false infeasibility on big-M lateness constraints, so HiGHS
    is the certified backend and CBC is fenced."""

    def _bundle(self):
        return generate_scenario(ScenarioSpec(
            topology="grid", n_nodes=6, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=30, horizon_hours=24, seed=42))

    def test_cbc_refused_on_hard_tolerance(self):
        b = self._bundle()
        with pytest.raises(RuntimeError, match="false\\s+infeasibility"):
            solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                           b.demand_ipc, horizon_hours=24,
                           theta=STEEP_SCARCE, time_limit_sec=30,
                           solver_backend="cbc")

    def test_missing_highs_fails_clearly_not_mystery_infeasible(self, monkeypatch):
        b = self._bundle()
        monkeypatch.setattr(pulp, "listSolvers",
                            lambda onlyAvailable=True: ["PULP_CBC_CMD"])
        with pytest.raises(RuntimeError, match="certified backend HiGHS required"):
            solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                           b.demand_ipc, horizon_hours=24,
                           theta=STEEP_SCARCE, time_limit_sec=30)

    def test_unknown_backend_rejected(self):
        b = self._bundle()
        with pytest.raises(ValueError, match="auto\\|highs\\|cbc"):
            solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                           b.demand_ipc, horizon_hours=24,
                           theta=STEEP_SCARCE, time_limit_sec=30,
                           solver_backend="gurobi")

    def test_cbc_allowed_and_agrees_on_linear_tolerance(self):
        """CBC remains trustworthy on linear-ℓ (diagnosis: agreement to
        4 decimals with HiGHS) — pin that the explicit escape hatch works
        and lands on the same certified value."""
        b = self._bundle()
        cbc = solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                             b.demand_ipc, horizon_hours=24,
                             theta=SLACK_ELASTIC, time_limit_sec=120,
                             solver_backend="cbc")
        hig = solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                             b.demand_ipc, horizon_hours=24,
                             theta=SLACK_ELASTIC, time_limit_sec=120,
                             solver_backend="highs")
        assert cbc.certified and hig.certified
        assert cbc.claimed_v_realized == pytest.approx(
            hig.claimed_v_realized, abs=1e-6)


@pytest.fixture(scope="module")
def small_solution() -> tuple:
    b = generate_scenario(ScenarioSpec(
        topology="grid", n_nodes=6, density="moderate", capacity="tight",
        demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=30, horizon_hours=24, seed=42))
    sol = solve_wait_ilp(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                         b.demand_ipc, horizon_hours=24,
                         theta=STEEP_SCARCE, time_limit_sec=120)
    return b, sol


class TestEndToEnd:
    def test_certifies_and_is_conservative(self, small_solution):
        _, sol = small_solution
        assert isinstance(sol, WaitIlpSolution)
        assert sol.certified
        assert sol.objective_v_realized <= sol.claimed_v_realized + 1e-9
        assert sol.pwl_gap >= -1e-9

    def test_engine_exact_boarding_on_every_committed_hop(self, small_solution):
        import pyarrow.ipc as ipc
        b, sol = small_solution
        dem = ipc.open_stream(b.demand_ipc).read_all()
        appear = dict(zip(dem.column("flow_unit_id").to_pylist(),
                          dem.column("appear_atu").to_pylist()))
        lanes = ipc.open_stream(b.lane_ipc).read_all()
        dur = dict(zip(lanes.column("lane_id").to_pylist(),
                       lanes.column("duration_atu").to_pylist()))
        for sid, hops in sol.committed.items():
            ready = appear[sid]
            for eid, dep in hops:
                assert dep >= ready, (sid, eid, dep, ready)
                ready = dep + dur[eid]

    def test_claim_is_the_true_functional_at_exact_times(self, small_solution):
        import pyarrow.ipc as ipc
        b, sol = small_solution
        dem = ipc.open_stream(b.demand_ipc).read_all()
        due = dict(zip(dem.column("flow_unit_id").to_pylist(),
                       dem.column("due_atu").to_pylist()))
        size = dict(zip(dem.column("flow_unit_id").to_pylist(),
                        dem.column("size_units").to_pylist()))
        for sid in sol.committed:
            assert sol.claimed_per_unit[sid] == v_realized(
                sol.claimed_held[sid], sol.claimed_delivered[sid],
                due[sid], size[sid], STEEP_SCARCE)
        assert sol.claimed_v_realized == sum(
            sol.claimed_per_unit[s] for s in sorted(sol.claimed_per_unit))
