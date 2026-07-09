"""Unit tests for the typed reason-code taxonomy (epure_arena.reasons).

Pins the §5 mapping table of the plan report: every (strand_reason, verdict,
genuine_kind) combination maps to exactly one ReasonCode or raises — no
silent fall-through (Law 4: every failure must compile).
"""

from __future__ import annotations

import pytest

from epure_arena.reasons import (
    CAPACITY_EXHAUSTED,
    DEADLINE_MISSED,
    EDGE_OUTAGE,
    FEASIBLE_LATE,
    FEASIBLE_ON_TIME,
    GENUINE_CLIFF,
    GENUINE_STRUCTURAL,
    HUB_OUTAGE,
    INFEASIBLE,
    SCHEDULE_INFEASIBLE,
    TOPOLOGY_UNREACHABLE,
    RClass,
    ReasonCode,
    attribute_strand,
    attribution_table,
)


class TestAttributeStrand:
    def test_router_deadend_is_r3(self):
        code = attribute_strand(SCHEDULE_INFEASIBLE, FEASIBLE_ON_TIME)
        assert code is ReasonCode.ALGO_DEADEND
        assert code.r_class is RClass.R3_ALGORITHM

    def test_capacity_with_on_time_supply_defaults_to_r3_myopia(self):
        code = attribute_strand(CAPACITY_EXHAUSTED, FEASIBLE_ON_TIME)
        assert code is ReasonCode.ALGO_CAPACITY_MYOPIA
        assert code.r_class is RClass.R3_ALGORITHM

    def test_capacity_lp_certified_becomes_r1_world(self):
        code = attribute_strand(
            CAPACITY_EXHAUSTED, FEASIBLE_ON_TIME, lp_certified_infeasible=True
        )
        assert code is ReasonCode.WORLD_CAPACITY_SHORTFALL
        assert code.r_class is RClass.R1_WORLD

    def test_feasible_late_is_envelope_deadline(self):
        for reason in (SCHEDULE_INFEASIBLE, CAPACITY_EXHAUSTED, DEADLINE_MISSED):
            assert attribute_strand(reason, FEASIBLE_LATE) is ReasonCode.ENVELOPE_DEADLINE

    def test_genuine_cliff_is_r2_envelope(self):
        code = attribute_strand(SCHEDULE_INFEASIBLE, INFEASIBLE, GENUINE_CLIFF)
        assert code is ReasonCode.ENVELOPE_CLIFF
        assert code.r_class is RClass.R2_ENVELOPE

    def test_genuine_structural_is_r1_world(self):
        code = attribute_strand(SCHEDULE_INFEASIBLE, INFEASIBLE, GENUINE_STRUCTURAL)
        assert code is ReasonCode.WORLD_STRUCTURAL
        assert code.r_class is RClass.R1_WORLD

    def test_topology_needs_no_oracle(self):
        assert attribute_strand(TOPOLOGY_UNREACHABLE, None) is ReasonCode.WORLD_STRUCTURAL

    def test_outages_are_envelope_shock(self):
        assert attribute_strand(EDGE_OUTAGE, None) is ReasonCode.ENVELOPE_SHOCK
        assert attribute_strand(HUB_OUTAGE, None) is ReasonCode.ENVELOPE_SHOCK

    def test_infeasible_without_kind_raises(self):
        with pytest.raises(ValueError, match="genuine_kind"):
            attribute_strand(SCHEDULE_INFEASIBLE, INFEASIBLE, None)

    def test_oracle_reason_without_verdict_raises(self):
        with pytest.raises(ValueError, match="oracle verdict"):
            attribute_strand(SCHEDULE_INFEASIBLE, None)

    def test_unknown_reason_raises(self):
        with pytest.raises(ValueError, match="unknown strand_reason"):
            attribute_strand(99, FEASIBLE_ON_TIME)


class TestAttributionTable:
    def test_zero_unexplained_mass_and_rclass_totals(self):
        rows = [
            (SCHEDULE_INFEASIBLE, FEASIBLE_ON_TIME, None),  # R3
            (SCHEDULE_INFEASIBLE, FEASIBLE_ON_TIME, None),  # R3
            (CAPACITY_EXHAUSTED, FEASIBLE_ON_TIME, None),  # R3
            (SCHEDULE_INFEASIBLE, INFEASIBLE, GENUINE_CLIFF),  # R2
            (SCHEDULE_INFEASIBLE, FEASIBLE_LATE, None),  # R2
            (SCHEDULE_INFEASIBLE, INFEASIBLE, GENUINE_STRUCTURAL),  # R1
            (TOPOLOGY_UNREACHABLE, None, None),  # R1
        ]
        t = attribution_table(rows)
        assert t["ALGO_DEADEND"] == 2
        assert t["ALGO_CAPACITY_MYOPIA"] == 1
        assert t["ENVELOPE_CLIFF"] == 1
        assert t["ENVELOPE_DEADLINE"] == 1
        assert t["WORLD_STRUCTURAL"] == 2
        assert t["R1"] == 2 and t["R2"] == 2 and t["R3"] == 3
        # zero unexplained mass: codes sum to the row count
        code_total = sum(t[c.value] for c in ReasonCode)
        assert code_total == len(rows)
        assert t["R1"] + t["R2"] + t["R3"] == len(rows)

    def test_every_code_has_an_r_class(self):
        for code in ReasonCode:
            assert code.r_class in RClass
