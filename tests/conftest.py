"""Shared fixtures for Tier 3 planner tests.

Provides synthetic graph fixtures at various sizes and complexity levels.
All fixtures use deterministic data — no randomness without an explicit seed.
"""

from __future__ import annotations

import pytest

from epure_arena.optimize.interface import PlanContext, PlanRequest


# ── Graph fixtures ──────────────────────────────────────────────────────────


def make_diamond_graph() -> PlanContext:
    """4-node diamond: 0→1→3, 0→2→3.

    Lane 0: 0→1 (dur=100k, cost=10M)
    Lane 1: 0→2 (dur=200k, cost=5M)
    Lane 2: 1→3 (dur=100k, cost=10M)
    Lane 3: 2→3 (dur=50k,  cost=5M)

    Shortest by duration: 0→1→3 (200k ATU, 20M micros)
    Cheapest by cost:     0→2→3 (250k ATU, 10M micros)
    """
    adj: list[list[tuple[int, int, int, int, int, int]]] = [
        [(1, 0, 100_000, 10_000_000, 100, 2), (2, 1, 200_000, 5_000_000, 50, 2)],
        [(3, 2, 100_000, 10_000_000, 100, 2)],
        [(3, 3, 50_000, 5_000_000, 50, 2)],
        [],
    ]
    schedules: dict[int, list[tuple[int, int]]] = {
        0: [(0, 100), (3_600_000, 100)],
        1: [(0, 100), (3_600_000, 100)],
        2: [(100_000, 100), (3_600_000, 100)],
        3: [(200_000, 100), (3_600_000, 100)],
    }
    return PlanContext(
        n_nodes=4,
        n_lanes=4,
        adjacency=adj,
        schedule_departures=schedules,
        lane_id_to_csr=[0, 1, 2, 3],
    )


def make_linear_graph(n: int = 5) -> PlanContext:
    """Linear chain: 0→1→2→...→(n-1). One path only.

    Each lane: dur=100k, cost=1M, co2=10, mode=2.
    """
    adj: list[list[tuple[int, int, int, int, int, int]]] = [[] for _ in range(n)]
    schedules: dict[int, list[tuple[int, int]]] = {}
    for i in range(n - 1):
        adj[i].append((i + 1, i, 100_000, 1_000_000, 10, 2))
        schedules[i] = [(0, 100), (3_600_000, 100)]
    return PlanContext(
        n_nodes=n,
        n_lanes=n - 1,
        adjacency=adj,
        schedule_departures=schedules,
        lane_id_to_csr=list(range(n - 1)),
    )


def make_disconnected_graph() -> PlanContext:
    """4 nodes, two disconnected components: {0,1} and {2,3}.

    Lane 0: 0→1, Lane 1: 2→3.
    """
    adj: list[list[tuple[int, int, int, int, int, int]]] = [
        [(1, 0, 100_000, 1_000_000, 10, 2)],
        [],
        [(3, 1, 100_000, 1_000_000, 10, 2)],
        [],
    ]
    schedules: dict[int, list[tuple[int, int]]] = {
        0: [(0, 100)],
        1: [(0, 100)],
    }
    return PlanContext(
        n_nodes=4,
        n_lanes=2,
        adjacency=adj,
        schedule_departures=schedules,
        lane_id_to_csr=[0, 1],
    )


def make_request(
    sid: int = 1,
    origin: int = 0,
    dest: int = 3,
    priority: int = 2,
    appear: int = 0,
    due: int = 86_400_000,
    size: int = 10,
) -> PlanRequest:
    """Factory for PlanRequest with sensible defaults."""
    return PlanRequest(
        flow_unit_id=sid,
        origin=origin,
        destination=dest,
        appear_atu=appear,
        due_atu=due,
        size_units=size,
        priority_class=priority,
    )


# ── Pytest fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def diamond_ctx() -> PlanContext:
    return make_diamond_graph()


@pytest.fixture()
def linear_ctx() -> PlanContext:
    return make_linear_graph(5)


@pytest.fixture()
def disconnected_ctx() -> PlanContext:
    return make_disconnected_graph()
