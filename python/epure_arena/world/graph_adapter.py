"""Arrow IPC → schedule-aware planner graph.

Builds adjacency lists and schedule lookup tables from the same Arrow IPC
buffers the Rust DES engine consumes. Ensures lane ordering matches the
Python-side lane_id (sequential 0..E-1).

The Rust CSR sorts lanes by target within each node's range, so CSR indices
differ from Python lane_ids. The lane_id_to_csr mapping bridges them.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

from epure_arena.optimize.interface import PlanContext

logger = logging.getLogger(__name__)


def _read_ipc_table(buf: bytes) -> pa.Table:
    """Read Arrow IPC stream buffer into a PyArrow table."""
    reader = ipc.open_stream(buf)
    return reader.read_all()


def build_plan_context(
    node_ipc: bytes,
    lane_ipc: bytes,
    schedule_ipc: bytes,
    lane_id_to_csr: list[int],
    cost_map: Any = None,
    bucket_atu: int = 3_600_000,
) -> PlanContext:
    """Build PlanContext from Arrow IPC buffers.

    Args:
        node_ipc: Arrow IPC bytes for node table.
        lane_ipc: Arrow IPC bytes for lane table.
        schedule_ipc: Arrow IPC bytes for schedule table.
        lane_id_to_csr: Mapping from Python lane_id → Rust CSR index.
        cost_map: Optional externally-loaded cost surface (T, N). Core takes
            it as data; loading any learned model is an adapter concern
            (dependency direction: core never imports an adapter).
        bucket_atu: Time bucket duration for cost map indexing.

    Returns:
        PlanContext with adjacency, schedule data, and CSR mapping.
    """
    node_table = _read_ipc_table(node_ipc)
    lane_table = _read_ipc_table(lane_ipc)
    schedule_table = _read_ipc_table(schedule_ipc)

    n_nodes = node_table.num_rows
    n_lanes = lane_table.num_rows

    # ── Build adjacency list ──
    # adj[u] = [(v, lane_id, duration_atu, cost_micros, co2_g, lane_mode)]
    src_ids = lane_table.column("src_node_id").to_numpy()
    dst_ids = lane_table.column("dst_node_id").to_numpy()
    lane_ids = lane_table.column("lane_id").to_numpy()
    durations = lane_table.column("duration_atu").to_numpy()
    costs = lane_table.column("cost_usd_micros").to_numpy()
    co2 = lane_table.column("co2_g").to_numpy()
    modes = lane_table.column("lane_mode").to_numpy()

    adjacency: list[list[tuple[int, int, int, int, int, int]]] = [[] for _ in range(n_nodes)]
    for i in range(n_lanes):
        u = int(src_ids[i])
        v = int(dst_ids[i])
        eid = int(lane_ids[i])
        dur = int(durations[i])
        cost = int(costs[i])
        c = int(co2[i])
        mode = int(modes[i])
        if u < n_nodes:
            adjacency[u].append((v, eid, dur, cost, c, mode))

    # ── Build schedule lookup ──
    # sched[lane_id] = sorted list of (depart_atu, capacity)
    sched_lane_ids = schedule_table.column("lane_id").to_numpy()
    sched_departs = schedule_table.column("depart_atu").to_numpy()
    sched_caps = schedule_table.column("capacity").to_numpy()

    schedule_departures: dict[int, list[tuple[int, int]]] = {}
    for i in range(schedule_table.num_rows):
        lid = int(sched_lane_ids[i])
        dep = int(sched_departs[i])
        cap = int(sched_caps[i])
        schedule_departures.setdefault(lid, []).append((dep, cap))

    # Sort each lane's departures by time
    for lid in schedule_departures:
        schedule_departures[lid].sort(key=lambda x: x[0])

    # ── Precompute shortest-path distances (Dijkstra from all leafs) ──
    # For large graphs (>1000 nodes), skip full all-pairs and use on-demand
    distances: list[list[int]] | None = None
    if n_nodes <= 1000:
        distances = _compute_all_pairs_shortest(n_nodes, adjacency)

    logger.info(
        "PlanContext built: %d nodes, %d lanes, %d scheduled lanes, distances=%s, cost_map=%s",
        n_nodes, n_lanes, len(schedule_departures),
        "precomputed" if distances is not None else "on-demand",
        "loaded" if cost_map is not None else "none",
    )

    return PlanContext(
        n_nodes=n_nodes,
        n_lanes=n_lanes,
        adjacency=adjacency,
        schedule_departures=schedule_departures,
        lane_id_to_csr=list(lane_id_to_csr),
        distances=distances,
        cost_map=cost_map,
        bucket_atu=bucket_atu,
    )


def _compute_all_pairs_shortest(
    n: int,
    adjacency: list[list[tuple[int, int, int, int, int, int]]],
) -> list[list[int]]:
    """Compute all-pairs shortest path distances using Dijkstra.

    Uses duration_atu as lane weight. O(N * (N + E) log N).

    Args:
        n: Number of nodes.
        adjacency: Adjacency list.

    Returns:
        dist[u][v] = shortest duration_atu from u to v. MAX_INT if unreachable.
    """
    import heapq

    MAX_DIST = 10**15
    dist = [[MAX_DIST] * n for _ in range(n)]

    for src in range(n):
        d = dist[src]
        d[src] = 0
        heap = [(0, src)]
        while heap:
            cost, u = heapq.heappop(heap)
            if cost > d[u]:
                continue
            for v, _eid, dur, _cost, _co2, _mode in adjacency[u]:
                new_cost = cost + dur
                if new_cost < d[v]:
                    d[v] = new_cost
                    heapq.heappush(heap, (new_cost, v))

    return dist
