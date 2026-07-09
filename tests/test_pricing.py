"""Wait-cost pricing (Gate 0) — the value functional's load-bearing claims.

Pinned here: the exact functional form (no cap, no floor), the DEMO_ZERO
reduction (v_realized ≡ v·1[on-time] — the demo guarantee's algebraic
half), convexity (the cross-curvature carrier), the value-closure
identity (zero unexplained mass), the held-not-wait pricing constraint,
and plan-side/record-side agreement on a hand-built chain.
"""

from __future__ import annotations

import pytest

from epure_arena.pricing import (
    DEMO_ZERO,
    STEEP_SCARCE,
    SLACK_ELASTIC,
    WaitCostProfile,
    late_loss,
    plan_times,
    unit_value,
    v_realized,
    value_report,
    wait_cost,
)

_H = 3_600_000


class TestFunctionalForm:
    def test_demo_zero_reduces_to_on_time_value(self):
        # on time -> full value regardless of held time (C_wait == 0)
        assert v_realized(10 * _H, 100, 200, 3, DEMO_ZERO) == 3.0
        # late -> zero (hard, tol 0)
        assert v_realized(0, 201, 200, 3, DEMO_ZERO) == 0.0

    def test_wait_cost_is_convex_and_prices_held_only(self):
        p = STEEP_SCARCE
        c1 = wait_cost(1 * _H, 1, p)
        c2 = wait_cost(2 * _H, 1, p)
        c4 = wait_cost(4 * _H, 1, p)
        assert c2 > 2 * c1, "b>0 must make doubling the wait more than double the cost"
        # discrete convexity: c(4) - c(2) > c(2) - c(0)
        assert (c4 - c2) > (c2 - 0.0)

    def test_no_floor_negative_realized_value_is_kept(self):
        # 6h held at steep-scarce steepness costs > 1x value -> negative.
        vr = v_realized(6 * _H, 100, 200, 1, STEEP_SCARCE)
        assert vr < 0.0

    def test_late_modes(self):
        assert late_loss(1, 2, STEEP_SCARCE) == 2.0          # hard, tol 0
        assert late_loss(6 * _H, 2, SLACK_ELASTIC) == 1.0  # linear: 6/12 * 2
        assert late_loss(24 * _H, 2, SLACK_ELASTIC) == 2.0  # capped at v

    def test_linear_mode_requires_positive_tolerance(self):
        with pytest.raises(ValueError):
            WaitCostProfile("bad", 0.0, 0.0, "linear", 0, "x")


class TestValueClosure:
    def _cols(self):
        return {
            "flow_unit_id": [1, 2, 3, 4],
            "due_atu": [10 * _H, 10 * _H, 10 * _H, 10 * _H],
            "size_units": [2, 3, 1, 5],
        }

    def _delivery(self):
        # unit 1 on time after 2h held; unit 2 late by 2h after 4h held
        return {
            "flow_unit_id": [1, 2],
            "delivered_atu": [9 * _H, 12 * _H],
            "held_atu": [2 * _H, 4 * _H],
            "wait_atu": [1 * _H, 0],
        }

    def _strands(self):
        return {"flow_unit_id": [3], "reason": [2], "held_atu": [5 * _H]}

    @pytest.mark.parametrize("p", [DEMO_ZERO, STEEP_SCARCE, SLACK_ELASTIC])
    def test_zero_unexplained_mass(self, p):
        r = value_report(self._cols(), self._delivery(), self._strands(), p)
        assert r.closes(), (
            f"value closure violated under {p.name}: "
            f"{r.total_value} != {r.v_realized_total} + {r.wait_loss} + "
            f"{r.late_loss} + {r.strand_loss} + {r.censored_loss}")
        # unit 4 is in neither log -> censored, typed
        assert r.censored_loss == 5.0
        assert r.strand_loss_by_reason == {"capacity_exhausted": 1.0}

    def test_wait_axis_split_labels_sum_to_the_priced_loss(self):
        r = value_report(self._cols(), self._delivery(), self._strands(),
                         STEEP_SCARCE)
        assert (r.wait_loss_capacity_queue + r.wait_loss_schedule_dwell
                == pytest.approx(r.wait_loss))
        # unit 1: held 2h of which 1h queue -> half the priced loss is
        # labelled queue; unit 2: 0 queue -> all dwell.
        assert r.wait_loss_capacity_queue == pytest.approx(
            wait_cost(2 * _H, 2, STEEP_SCARCE) * 0.5)

    def test_restrict_ids_gives_the_admitted_set_view(self):
        r = value_report(self._cols(), self._delivery(), self._strands(),
                         DEMO_ZERO, restrict_ids={1, 2})
        assert r.n_units == 2
        assert r.total_value == 5.0
        assert r.v_realized_total == 2.0  # unit 1 on time; unit 2 late


class TestPlanSide:
    def test_plan_times_reconstructs_the_ready_chain(self):
        class Ctx:
            # one lane (id 7): dur 30min; schedule slots at 2h and 5h.
            adjacency = [[(1, 7, 30 * 60_000, 0, 0, 0)], []]
            schedule_departures = {7: [(2 * _H, 10), (5 * _H, 10)]}

        held, delivered = plan_times([(7, 1)], appear_atu=1 * _H, ctx=Ctx())
        assert held == 4 * _H            # ready 1h -> depart 5h
        assert delivered == 5 * _H + 30 * 60_000

    def test_unit_value_is_the_lattice_currency(self):
        assert unit_value(4) == 4.0
