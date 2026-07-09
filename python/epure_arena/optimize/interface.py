"""Planner interface contract.

Defines the abstract base class all planners must implement, plus the
data structures for plan requests, results, and planning context.

All time is i64 ATU milliseconds (Sacred Contract G-1).
Lane indices are CSR-order u32 (Sacred Contract B-1).
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any

# Sacred Contract G-1: Time = i64 ATU milliseconds
Atu = int
# Sacred Contract B-1: CSR lane index = u32
LaneIdx = int
NodeId = int
FlowUnitId = int


@dataclasses.dataclass(frozen=True)
class PlanRequest:
    """A single flow_unit to be routed.

    All time fields are i64 ATU milliseconds.

    Attributes:
        flow_unit_id: Unique flow_unit identifier.
        origin: Origin node ID (dense 0..N-1).
        destination: Destination node ID (dense 0..N-1).
        appear_atu: Time the flow_unit appears at origin.
        due_atu: SLA deadline (delivery must happen before this).
        size_units: Abstract capacity units (u32).
        priority_class: 0=VIP, 1=Express, 2=Standard, 3=Economy.
    """

    flow_unit_id: FlowUnitId
    origin: NodeId
    destination: NodeId
    appear_atu: Atu
    due_atu: Atu
    size_units: int
    priority_class: int


@dataclasses.dataclass
class PlanResult:
    """Result of planning a single flow_unit.

    Attributes:
        flow_unit_id: The planned flow_unit.
        path: Ordered list of CSR lane indices (origin → destination).
                Empty if no path found.
        estimated_cost_micros: Estimated total cost (sum of lane costs).
        estimated_duration_atu: Estimated total transit time.
        status: 'planned' | 'fallback' | 'failed'.
        planner_name: Which planner produced this result.
    """

    flow_unit_id: FlowUnitId
    path: list[LaneIdx]
    estimated_cost_micros: int = 0
    estimated_duration_atu: Atu = 0
    status: str = "planned"
    planner_name: str = ""
    # Typed failure reason for status == 'failed' (planner-level string; maps
    # onto epure_arena.reasons codes — CAPACITY_BLOCKED pends the LP
    # certificate). None for planned/fallback results. Additive (Step 3).
    reason: str | None = None
    # Planned arrival ATU for committed plans (0 if failed). Additive (Step 3);
    # the join key for the Step-4 plan-fidelity instrument.
    planned_arrival_atu: Atu = 0


@dataclasses.dataclass
class PlanContext:
    """Shared context available to all planners.

    Provides graph structure, schedule data, optional congestion predictions,
    and the lane_id → CSR index mapping for Rust engine compatibility.

    Attributes:
        n_nodes: Number of nodes.
        n_lanes: Number of lanes.
        adjacency: Adjacency list: adj[u] = [(v, lane_id, duration_atu, cost_micros, co2_g, lane_mode), ...].
        schedule_departures: Per-lane departure times: sched[lane_id] = sorted list of (depart_atu, capacity).
        lane_id_to_csr: Maps Python lane_id → Rust CSR index.
        distances: Precomputed shortest-path distances: dist[u][v] (or None).
        cost_map: Neural cost map (T, N) array from Tier 2 (or None).
        bucket_atu: Time bucket duration for cost map indexing.
    """

    n_nodes: int
    n_lanes: int
    adjacency: list[list[tuple[int, int, int, int, int, int]]]
    schedule_departures: dict[int, list[tuple[int, int]]]
    lane_id_to_csr: list[int]
    distances: list[list[int]] | None = None
    cost_map: Any = None  # numpy (T, N) float32 or None
    bucket_atu: int = 3_600_000
    # alpha used by A*: inflated_time = base_time * (1 + alpha * cost_map[t, node]).
    # Defaults to the historical NeuralCostMap default (0.5) so existing
    # callers that don't set it see the original behaviour.
    cost_map_weight: float = 0.5
    # Per-region alpha deltas added to the planner's global alpha. The
    # capacity-A* planner reads them via the source-region rule:
    # lane (u,v) uses delta = alpha_deltas[node_region_ids[u]]. Both
    # fields default None, in which case the planner behaves as if
    # all deltas were zero (legacy behaviour preserved).
    node_region_ids: list[int] | None = None
    alpha_deltas: list[float] | None = None
    # Per-lane alpha deltas (N4): keyed by lane_id, additive on top
    # of the global alpha and any per-region delta. Lanes absent from
    # the dict get no per-lane contribution. Used by the per-lane
    # top-K action mode; default None preserves legacy behaviour.
    lane_alpha_deltas: dict[int, float] | None = None


class Planner(ABC):
    """Abstract base class for all routing planners.

    Every planner implementation must satisfy this interface.
    The registry instantiates planners and the selector dispatches
    requests to the appropriate planner based on priority class.

    Future implementations (DQN, PPO, IL+RL, Diffusion) must
    implement this same interface without changes to existing code.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique planner name for registry lookup."""

    @abstractmethod
    def plan(
        self,
        requests: list[PlanRequest],
        context: PlanContext,
    ) -> list[PlanResult]:
        """Route a batch of flow_units.

        Args:
            requests: FlowUnits to route.
            context: Shared planning context with graph, schedules, cost map.

        Returns:
            One PlanResult per request, in the same order.
        """

    def supports_multi_agent(self) -> bool:
        """Whether this planner considers inter-agent conflicts (e.g., CBS)."""
        return False
