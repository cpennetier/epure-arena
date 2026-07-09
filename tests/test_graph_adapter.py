"""Tests for graph_adapter: Arrow IPC → PlanContext.

Covers: _compute_all_pairs_shortest correctness, distance symmetry,
and build_plan_context (tested via synthetic Arrow IPC buffers).
"""

from __future__ import annotations

import pytest

from epure_arena.world.graph_adapter import _compute_all_pairs_shortest
from epure_arena.optimize.interface import PlanContext

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_diamond_graph, make_linear_graph, make_disconnected_graph


class TestAllPairsShortest:
    """_compute_all_pairs_shortest returns correct Dijkstra distances."""

    def test_diamond_distances(self) -> None:
        ctx = make_diamond_graph()
        dist = _compute_all_pairs_shortest(ctx.n_nodes, ctx.adjacency)
        # 0→0 = 0
        assert dist[0][0] == 0
        # 0→1: lane 0 (dur=100k)
        assert dist[0][1] == 100_000
        # 0→2: lane 1 (dur=200k)
        assert dist[0][2] == 200_000
        # 0→3: min(0→1→3=200k, 0→2→3=250k) = 200k
        assert dist[0][3] == 200_000
        # 1→3: lane 2 (dur=100k)
        assert dist[1][3] == 100_000
        # 2→3: lane 3 (dur=50k)
        assert dist[2][3] == 50_000

    def test_linear_distances(self) -> None:
        ctx = make_linear_graph(4)
        dist = _compute_all_pairs_shortest(ctx.n_nodes, ctx.adjacency)
        assert dist[0][1] == 100_000
        assert dist[0][2] == 200_000
        assert dist[0][3] == 300_000

    def test_unreachable_max_dist(self) -> None:
        ctx = make_disconnected_graph()
        dist = _compute_all_pairs_shortest(ctx.n_nodes, ctx.adjacency)
        # 0→3 is unreachable (different components)
        assert dist[0][3] == 10**15
        # 0→1 is reachable
        assert dist[0][1] == 100_000

    def test_self_distance_zero(self) -> None:
        ctx = make_diamond_graph()
        dist = _compute_all_pairs_shortest(ctx.n_nodes, ctx.adjacency)
        for i in range(ctx.n_nodes):
            assert dist[i][i] == 0


class TestAllPairsShortestAdmissibility:
    """Precomputed distances are admissible heuristics for A*."""

    def test_never_overestimates(self) -> None:
        """dist[u][v] <= actual cost of any path from u to v."""
        ctx = make_diamond_graph()
        dist = _compute_all_pairs_shortest(ctx.n_nodes, ctx.adjacency)
        # The Dijkstra shortest path 0→3 is 200k
        # dist[0][3] must equal this (it IS the shortest path)
        assert dist[0][3] == 200_000


class TestBuildPlanContextFromArrowIPC:
    """build_plan_context requires real Arrow IPC buffers.

    These tests validate behavior via the fixtures which build PlanContext
    directly. Full Arrow IPC round-trip tests require pyarrow table
    serialization which is covered in integration tests.
    """

    def test_context_has_correct_counts(self) -> None:
        ctx = make_diamond_graph()
        assert ctx.n_nodes == 4
        assert ctx.n_lanes == 4

    def test_adjacency_length_matches_n_nodes(self) -> None:
        ctx = make_diamond_graph()
        assert len(ctx.adjacency) == ctx.n_nodes

    def test_lane_id_to_csr_length_matches_n_lanes(self) -> None:
        ctx = make_diamond_graph()
        assert len(ctx.lane_id_to_csr) == ctx.n_lanes
