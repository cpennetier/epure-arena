"""StaticPlanner — Dijkstra shortest-path baseline.

Ignores schedules, capacity, and congestion predictions.
Uses duration_atu as lane weight. Produces the same paths as
the Rust StaticRouter (oracle baseline for comparison).

This is the fallback of last resort: if CBS and A* both fail,
Dijkstra always produces a path (if the graph is connected).
"""

from __future__ import annotations

import heapq
import logging

from epure_arena.optimize.interface import Planner, PlanContext, PlanRequest, PlanResult

logger = logging.getLogger(__name__)


class StaticPlanner(Planner):
    """Dijkstra shortest-path planner (baseline)."""

    @property
    def name(self) -> str:
        return "static"

    def plan(
        self,
        requests: list[PlanRequest],
        context: PlanContext,
    ) -> list[PlanResult]:
        """Route each flow_unit via shortest path (duration_atu).

        Args:
            requests: FlowUnits to route.
            context: Graph and schedule data.

        Returns:
            PlanResults with lane_id paths.
        """
        results: list[PlanResult] = []
        for req in requests:
            path, cost, duration = self._dijkstra(
                req.origin, req.destination, context,
            )
            results.append(PlanResult(
                flow_unit_id=req.flow_unit_id,
                path=path,
                estimated_cost_micros=cost,
                estimated_duration_atu=duration,
                status="planned" if path else "failed",
                planner_name=self.name,
            ))
        return results

    def _dijkstra(
        self,
        src: int,
        dst: int,
        ctx: PlanContext,
    ) -> tuple[list[int], int, int]:
        """Dijkstra shortest path from src to dst.

        Returns:
            (lane_id_path, total_cost_micros, total_duration_atu).
            Empty path if unreachable.
        """
        MAX_DIST = 10**15
        dist = [MAX_DIST] * ctx.n_nodes
        prev_lane: list[int | None] = [None] * ctx.n_nodes
        prev_node: list[int | None] = [None] * ctx.n_nodes
        dist[src] = 0

        heap = [(0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            if u == dst:
                break
            for v, eid, dur, _cost, _co2, _mode in ctx.adjacency[u]:
                nd = d + dur
                if nd < dist[v]:
                    dist[v] = nd
                    prev_lane[v] = eid
                    prev_node[v] = u
                    heapq.heappush(heap, (nd, v))

        if dist[dst] >= MAX_DIST:
            return [], 0, 0

        # Reconstruct path
        path: list[int] = []
        total_cost = 0
        node = dst
        while prev_lane[node] is not None:
            eid = prev_lane[node]
            assert eid is not None
            path.append(eid)
            # Look up cost from adjacency
            parent = prev_node[node]
            assert parent is not None
            for v, e, _dur, cost, _co2, _mode in ctx.adjacency[parent]:
                if e == eid:
                    total_cost += cost
                    break
            node = parent

        path.reverse()
        return path, total_cost, dist[dst]
