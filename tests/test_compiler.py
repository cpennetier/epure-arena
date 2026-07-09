"""Comprehensive tests for compile_for_engine.

Covers: valid compilation, empty input, lane mapping, out-of-range
lane IDs, fallback status inclusion, and CSR index consistency.
"""

from __future__ import annotations

import pytest

from epure_arena.optimize.interface import PlanContext, PlanResult
from epure_arena.optimize.compiler import compile_for_engine

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_diamond_graph


class TestCompileValid:
    """compile_for_engine produces correct CSR index pairs."""

    def test_identity_mapping(self) -> None:
        ctx = make_diamond_graph()  # lane_id_to_csr = [0,1,2,3]
        results = [
            PlanResult(flow_unit_id=1, path=[0, 2], status="planned"),
            PlanResult(flow_unit_id=2, path=[1, 3], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert len(compiled) == 2
        assert compiled[0] == (1, [0, 2])
        assert compiled[1] == (2, [1, 3])

    def test_non_identity_mapping(self) -> None:
        ctx = make_diamond_graph()
        ctx.lane_id_to_csr = [10, 20, 30, 40]  # Non-trivial mapping
        results = [
            PlanResult(flow_unit_id=1, path=[0, 2], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert compiled == [(1, [10, 30])]

    def test_single_lane_path(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[0], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert compiled == [(1, [0])]


class TestCompileEmpty:
    """Empty and no-path inputs produce empty output."""

    def test_empty_results(self) -> None:
        ctx = make_diamond_graph()
        compiled = compile_for_engine([], ctx)
        assert compiled == []

    def test_all_failed(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[], status="failed"),
            PlanResult(flow_unit_id=2, path=[], status="failed"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert compiled == []


class TestCompileFiltering:
    """Compiler skips failed results and includes fallback results."""

    def test_skips_failed(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[0, 2], status="planned"),
            PlanResult(flow_unit_id=2, path=[], status="failed"),
            PlanResult(flow_unit_id=3, path=[1, 3], status="fallback"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert len(compiled) == 2
        ids = [c[0] for c in compiled]
        assert 1 in ids
        assert 3 in ids
        assert 2 not in ids

    def test_skips_empty_path_non_failed(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert compiled == []


class TestCompileLaneMapping:
    """Lane ID → CSR index translation is correct."""

    def test_lane_mapping_consistency(self) -> None:
        ctx = make_diamond_graph()
        ctx.lane_id_to_csr = [5, 10, 15, 20]
        results = [
            PlanResult(flow_unit_id=1, path=[0, 1, 2, 3], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert compiled == [(1, [5, 10, 15, 20])]

    def test_out_of_range_lane_id(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[0, 99], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        # Out-of-range lane should cause this result to be skipped (invalid)
        assert len(compiled) == 0

    def test_negative_lane_id(self) -> None:
        ctx = make_diamond_graph()
        results = [
            PlanResult(flow_unit_id=1, path=[-1, 0], status="planned"),
        ]
        compiled = compile_for_engine(results, ctx)
        assert len(compiled) == 0
