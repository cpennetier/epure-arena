"""Topology generators for the testkit.

Each generator returns `(nodes, lanes)` as plain Python lists of dicts whose
keys match the schemas in `serializer.py`. All generators are deterministic
in their `seed` argument: identical seed → identical output (including dict
key order, which matters for byte-stable IPC).

Topologies:
    star        — one center, n−1 leaves; 2(n−1) directed lanes.
    backbone   — k = round(sqrt(n)) gateways, the rest spread as spokes.
    grid        — sqrt(n)² grid, 4-neighborhood (no diagonals).
    tree        — complete binary tree, parent↔child both directions.
    mesh        — Erdős–Rényi G(n, p), p chosen by `density`.

All graphs are *strongly connected* (you can reach every leaf from every
node), so the StaticRouter and TimeAwareRouter both have something to
return for any (origin, destination) demand pair.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

# A truck moving 50 km/h covers 1 km in 72 s = 72_000 ATU.
_ATU_PER_KM_TRUCK: int = 72_000
# A USD 0.50/km lane → 500_000 microdollars/km.
_COST_MICROS_PER_KM: int = 500_000
# 100 g/km — placeholder; the engine treats CO2 as opaque accumulator.
_CO2_GRAMS_PER_KM: int = 100
# Max lane duration in ATU. With a 24-hour horizon and routes of 3+ hops, a
# realistic 50 km/h-on-continental-distance computation produces lanes longer
# than the horizon, leaving every flow_unit in flight. Cap at 30 minutes so
# the testkit produces tractable scenarios. Cost and CO2 still scale with
# real distance — only the *time* dimension is bounded.
_MAX_EDGE_DURATION_ATU: int = 1_800_000  # 30 minutes
_MIN_EDGE_DURATION_ATU: int = 60_000     # 1 minute

_LANE_MODE_TRUCK: int = 0
_PHASE: str = "trunk"

# Tier mix for synthetic networks. The Rust StaticRouter reverse-Dijkstras
# from every "leaf" node, so destinations in demand are restricted to
# leafs. We tag at least one node as leaf in every topology.
_TIER_GATEWAY: str = "gateway"
_TIER_REGIONAL: str = "regional"
_TIER_LEAF: str = "leaf"


# ── Geographic placement ────────────────────────────────────────────────

def _place_nodes_on_grid(n: int, seed: int) -> list[tuple[float, float]]:
    """Deterministic lat/lon placement on a synthetic North-America-ish box."""
    rng = random.Random(seed ^ 0xC0FFEE)
    lats: list[float] = []
    lons: list[float] = []
    for _ in range(n):
        lats.append(30.0 + rng.random() * 20.0)   # 30–50 N
        lons.append(-120.0 + rng.random() * 50.0)  # -120 to -70 W
    return list(zip(lats, lons))


def _great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in kilometres. Always > 0 for distinct nodes."""
    r_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a))


# ── Node/lane dict builders ──────────────────────────────────────────────

def _make_node(node_id: int, lat: float, lon: float, tier: str) -> dict[str, Any]:
    # h3_index is a synthetic u64; the DES engine doesn't read it but the
    # production schema requires uint64.
    h3_synthetic = (0x881F0D2400000000 | (node_id & 0xFFFFFFFF))
    return {
        "node_id": node_id,
        "h3_index": h3_synthetic,
        "lat": lat,
        "lon": lon,
        "tier": tier,
        "territory_id": 0,
        "demand_gravity": 1.0,
        "has_airport": False,
        "has_port": False,
        "has_rail": False,
        "landmass_id": 0,
    }


def _make_lane(
    lane_id: int,
    src: dict[str, Any],
    dst: dict[str, Any],
) -> dict[str, Any]:
    dist_km = max(_great_circle_km(src["lat"], src["lon"], dst["lat"], dst["lon"]), 1.0)
    raw_duration_atu = int(dist_km * _ATU_PER_KM_TRUCK)
    duration_atu = max(_MIN_EDGE_DURATION_ATU, min(_MAX_EDGE_DURATION_ATU, raw_duration_atu))
    cost_micros = int(dist_km * _COST_MICROS_PER_KM)
    co2_g = int(dist_km * _CO2_GRAMS_PER_KM)
    return {
        "lane_id": lane_id,
        "src_node_id": src["node_id"],
        "dst_node_id": dst["node_id"],
        "src_lat": src["lat"],
        "src_lon": src["lon"],
        "dst_lat": dst["lat"],
        "dst_lon": dst["lon"],
        "lane_mode": _LANE_MODE_TRUCK,
        "duration_sec": float(duration_atu) / 1_000.0,
        "duration_atu": duration_atu,
        "cost_usd": float(cost_micros) / 1_000_000.0,
        "cost_usd_micros": cost_micros,
        "co2_g": co2_g,
        "phase": _PHASE,
    }


def _assign_tiers(n_nodes: int, n_gateways: int, n_regional: int) -> list[str]:
    """Deterministic tier assignment: first k gateways, then regionals, rest leafs."""
    tiers = [_TIER_GATEWAY] * n_gateways
    tiers += [_TIER_REGIONAL] * n_regional
    tiers += [_TIER_LEAF] * (n_nodes - n_gateways - n_regional)
    return tiers[:n_nodes]


# ── Topology generators ─────────────────────────────────────────────────

def make_star(n_nodes: int, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One central node (gateway, id=0), n−1 leaves (leafs), bidirectional spokes."""
    if n_nodes < 2:
        raise ValueError("star requires n_nodes >= 2")
    coords = _place_nodes_on_grid(n_nodes, seed)
    tiers = [_TIER_GATEWAY] + [_TIER_LEAF] * (n_nodes - 1)
    nodes = [_make_node(i, lat, lon, tier) for i, ((lat, lon), tier) in enumerate(zip(coords, tiers))]
    lanes: list[dict[str, Any]] = []
    eid = 0
    for i in range(1, n_nodes):
        lanes.append(_make_lane(eid, nodes[0], nodes[i])); eid += 1
        lanes.append(_make_lane(eid, nodes[i], nodes[0])); eid += 1
    return nodes, lanes


def make_backbone(
    n_nodes: int, seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """k = round(sqrt(n)) gateways fully connected; remaining nodes spoke off them."""
    if n_nodes < 4:
        raise ValueError("backbone requires n_nodes >= 4")
    k = max(2, round(math.sqrt(n_nodes)))
    n_regional = max(0, n_nodes // 4 - k)
    n_leafs = n_nodes - k - n_regional
    if n_leafs <= 0:
        n_regional = max(0, n_regional - 1)
        n_leafs = n_nodes - k - n_regional

    coords = _place_nodes_on_grid(n_nodes, seed)
    tiers = _assign_tiers(n_nodes, k, n_regional)
    nodes = [_make_node(i, lat, lon, tier) for i, ((lat, lon), tier) in enumerate(zip(coords, tiers))]

    lanes: list[dict[str, Any]] = []
    eid = 0

    # Fully connect gateways to each other (both directions).
    for i in range(k):
        for j in range(k):
            if i != j:
                lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1

    # Round-robin spokes from each non-gateway to a gateway.
    for h in range(k, n_nodes):
        gw = nodes[h % k]
        lanes.append(_make_lane(eid, gw, nodes[h])); eid += 1
        lanes.append(_make_lane(eid, nodes[h], gw)); eid += 1

    return nodes, lanes


def make_grid(n_nodes: int, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """sqrt(n) × sqrt(n) grid (truncated to n_nodes), 4-neighborhood, both directions."""
    side = max(2, int(math.sqrt(n_nodes)))
    if side * side < n_nodes:
        side += 1
    cells = side * side
    coords = _place_nodes_on_grid(cells, seed)
    n = min(cells, n_nodes)
    tiers = [_TIER_LEAF] * n
    nodes = [_make_node(i, *coords[i], tier) for i, tier in enumerate(tiers)]

    lanes: list[dict[str, Any]] = []
    eid = 0
    for r in range(side):
        for c in range(side):
            i = r * side + c
            if i >= n:
                continue
            # Right neighbour
            if c + 1 < side and (r * side + c + 1) < n:
                j = r * side + c + 1
                lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1
                lanes.append(_make_lane(eid, nodes[j], nodes[i])); eid += 1
            # Down neighbour
            if r + 1 < side and ((r + 1) * side + c) < n:
                j = (r + 1) * side + c
                lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1
                lanes.append(_make_lane(eid, nodes[j], nodes[i])); eid += 1
    return nodes, lanes


def make_tree(n_nodes: int, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Complete binary tree. Node 0 is the root; child(i) = 2i+1, 2i+2."""
    if n_nodes < 2:
        raise ValueError("tree requires n_nodes >= 2")
    coords = _place_nodes_on_grid(n_nodes, seed)
    # Root + internal nodes mostly regional; leaves are leafs.
    tiers: list[str] = []
    for i in range(n_nodes):
        first_leaf = (n_nodes - 1) // 2  # standard heap leaf range start
        tier = _TIER_LEAF if i >= first_leaf else (_TIER_GATEWAY if i == 0 else _TIER_REGIONAL)
        tiers.append(tier)
    nodes = [_make_node(i, *coords[i], tiers[i]) for i in range(n_nodes)]

    lanes: list[dict[str, Any]] = []
    eid = 0
    for i in range(n_nodes):
        for j in (2 * i + 1, 2 * i + 2):
            if j < n_nodes:
                lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1
                lanes.append(_make_lane(eid, nodes[j], nodes[i])); eid += 1
    return nodes, lanes


def _density_to_p(density: str, n_nodes: int) -> float:
    """Map density label to Erdős–Rényi p."""
    # Aim for E ≈ k·N lanes.
    if density == "sparse":
        target_avg_degree = 2.0
    elif density == "moderate":
        target_avg_degree = 5.0
    elif density == "dense":
        target_avg_degree = 10.0
    else:
        raise ValueError(f"unknown density: {density}")
    return min(1.0, target_avg_degree / max(1, n_nodes - 1))


def make_mesh(
    n_nodes: int, seed: int, density: str = "moderate",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Erdős–Rényi-ish random graph + a spanning ring to guarantee connectivity."""
    if n_nodes < 2:
        raise ValueError("mesh requires n_nodes >= 2")
    rng = random.Random(seed ^ 0xBADF00D)
    coords = _place_nodes_on_grid(n_nodes, seed)
    n_gateways = max(1, n_nodes // 10)
    n_regional = max(0, n_nodes // 4)
    tiers = _assign_tiers(n_nodes, n_gateways, n_regional)
    nodes = [_make_node(i, *coords[i], tiers[i]) for i in range(n_nodes)]

    lanes: list[dict[str, Any]] = []
    eid = 0

    # Spanning ring: 0→1→…→n−1→0 in both directions, ensures strong connectivity.
    for i in range(n_nodes):
        j = (i + 1) % n_nodes
        lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1
        lanes.append(_make_lane(eid, nodes[j], nodes[i])); eid += 1

    # Erdős–Rényi extra lanes. Iterate (i, j) in a fixed order for determinism.
    p = _density_to_p(density, n_nodes)
    for i in range(n_nodes):
        for j in range(n_nodes):
            if i == j or abs(i - j) == 1 or {i, j} == {0, n_nodes - 1}:
                continue  # skip ring lanes
            if rng.random() < p:
                lanes.append(_make_lane(eid, nodes[i], nodes[j])); eid += 1
    return nodes, lanes


# ── Dispatch table ──────────────────────────────────────────────────────

TOPOLOGY_FNS: dict[str, Callable[[int, int], tuple[list[dict[str, Any]], list[dict[str, Any]]]]] = {
    "star": make_star,
    "backbone": make_backbone,
    "grid": make_grid,
    "tree": make_tree,
    # mesh takes density; bound below in scenarios.py.
}
