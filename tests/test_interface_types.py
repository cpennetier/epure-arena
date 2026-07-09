"""Tests for Tier 3 planner data contracts.

Validates PlanRequest, PlanResult, PlanContext, and type aliases
conform to the Sacred Contract (G-1: time=i64 ATU, B-1: lane=u32).
"""

from __future__ import annotations

import dataclasses

import pytest

from epure_arena.optimize.interface import (
    Atu,
    LaneIdx,
    NodeId,
    PlanContext,
    PlanRequest,
    PlanResult,
    Planner,
    FlowUnitId,
)


class TestPlanRequest:
    """PlanRequest data contract tests."""

    def test_valid_construction(self) -> None:
        req = PlanRequest(
            flow_unit_id=1,
            origin=0,
            destination=5,
            appear_atu=0,
            due_atu=86_400_000,
            size_units=10,
            priority_class=2,
        )
        assert req.flow_unit_id == 1
        assert req.origin == 0
        assert req.destination == 5
        assert req.appear_atu == 0
        assert req.due_atu == 86_400_000
        assert req.size_units == 10
        assert req.priority_class == 2

    def test_frozen(self) -> None:
        req = PlanRequest(
            flow_unit_id=1, origin=0, destination=1,
            appear_atu=0, due_atu=1000, size_units=1, priority_class=0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.origin = 99  # type: ignore[misc]

    def test_all_fields_are_int(self) -> None:
        req = PlanRequest(
            flow_unit_id=1, origin=0, destination=1,
            appear_atu=0, due_atu=86_400_000, size_units=10, priority_class=2,
        )
        for field in dataclasses.fields(req):
            assert isinstance(getattr(req, field.name), int), (
                f"Field {field.name} must be int (Sacred Contract G-1)"
            )

    def test_priority_classes(self) -> None:
        for p in (0, 1, 2, 3):
            req = PlanRequest(
                flow_unit_id=1, origin=0, destination=1,
                appear_atu=0, due_atu=1000, size_units=1, priority_class=p,
            )
            assert req.priority_class == p


class TestPlanResult:
    """PlanResult data contract tests."""

    def test_planned_result(self) -> None:
        result = PlanResult(
            flow_unit_id=1,
            path=[0, 2],
            estimated_cost_micros=20_000_000,
            estimated_duration_atu=200_000,
            status="planned",
            planner_name="static",
        )
        assert result.status == "planned"
        assert len(result.path) == 2
        assert result.estimated_cost_micros >= 0
        assert result.planner_name == "static"

    def test_no_path_result(self) -> None:
        result = PlanResult(flow_unit_id=1, path=[], status="failed")
        assert result.status == "failed"
        assert result.path == []
        assert result.estimated_cost_micros == 0

    def test_fallback_result(self) -> None:
        result = PlanResult(
            flow_unit_id=1, path=[0], status="fallback",
            planner_name="cbs->astar",
        )
        assert result.status == "fallback"

    def test_valid_statuses(self) -> None:
        for status in ("planned", "fallback", "failed"):
            r = PlanResult(flow_unit_id=1, path=[], status=status)
            assert r.status == status


class TestPlanContext:
    """PlanContext data contract tests."""

    def test_creation(self, diamond_ctx: PlanContext) -> None:
        assert diamond_ctx.n_nodes == 4
        assert diamond_ctx.n_lanes == 4
        assert len(diamond_ctx.adjacency) == 4
        assert len(diamond_ctx.lane_id_to_csr) == 4

    def test_adjacency_structure(self, diamond_ctx: PlanContext) -> None:
        for u, neighbors in enumerate(diamond_ctx.adjacency):
            for v, eid, dur, cost, co2, mode in neighbors:
                assert isinstance(v, int)
                assert isinstance(eid, int)
                assert dur > 0, "duration_atu must be positive"
                assert cost >= 0, "cost_micros must be non-negative"
                assert co2 >= 0, "co2_g must be non-negative"

    def test_schedules_sorted(self, diamond_ctx: PlanContext) -> None:
        for eid, deps in diamond_ctx.schedule_departures.items():
            times = [d[0] for d in deps]
            assert times == sorted(times), f"Lane {eid} departures not sorted"

    def test_default_bucket_atu(self) -> None:
        ctx = PlanContext(
            n_nodes=1, n_lanes=0, adjacency=[[]],
            schedule_departures={}, lane_id_to_csr=[],
        )
        assert ctx.bucket_atu == 3_600_000, "Default bucket = 1 hour"


class TestTypeAliases:
    """Verify type aliases are int (Sacred Contract G-1, B-1)."""

    def test_atu_is_int(self) -> None:
        assert Atu is int

    def test_lane_idx_is_int(self) -> None:
        assert LaneIdx is int

    def test_node_id_is_int(self) -> None:
        assert NodeId is int

    def test_flow_unit_id_is_int(self) -> None:
        assert FlowUnitId is int


class TestPlannerABC:
    """Verify Planner ABC contract."""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            Planner()  # type: ignore[abstract]

    def test_supports_multi_agent_default_false(self) -> None:
        class DummyPlanner(Planner):
            @property
            def name(self) -> str:
                return "dummy"

            def plan(self, requests, context):
                return []

        p = DummyPlanner()
        assert p.supports_multi_agent() is False
