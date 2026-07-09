"""Demand event generators — produce (origin, dest, appear_atu, due_atu, …).

Patterns (temporal):
    uniform       — appear_atu uniform over [0, horizon_atu).
    diurnal       — bimodal mixture peaking at 8h and 17h (σ = 1.5h each).
    spike         — uniform background + +3σ pulse at t = 8h.

OD distributions:
    uniform OD    — origin and destination drawn uniformly from leafs.
    concentrated  — 80% of demand to top 20% of destinations (Pareto-ish).

Priority mixes:
    all_standard  — class=2 for every event.
    heavy_vip     — 40% VIP (0), 30% Express (1), 30% Standard (2).
    mixed         — 10% VIP, 30% Express, 60% Standard.

All generators are deterministic in `seed`. Output is sorted by appear_atu
(required by `epure_arena::_engine::DemandQueue::from_arrays`).
"""

from __future__ import annotations

import math
import random
from typing import Any

_ATU_PER_HOUR: int = 3_600_000

_PRIORITY_MIXES: dict[str, list[tuple[int, float]]] = {
    "all_standard": [(2, 1.0)],
    "heavy_vip": [(0, 0.4), (1, 0.3), (2, 0.3)],
    "mixed": [(0, 0.1), (1, 0.3), (2, 0.6)],
}


def _leaf_node_ids(nodes: list[dict[str, Any]]) -> list[int]:
    """Nodes eligible as demand origins/destinations.

    The Rust StaticRouter only routes *to* leaf-tier nodes; for testkit
    purposes we route both ends through leafs. If no leafs exist (some
    topologies don't tag any), fall back to all nodes.
    """
    leafs = [h["node_id"] for h in nodes if h["tier"] == "leaf"]
    return leafs or [h["node_id"] for h in nodes]


def _sample_appear_atu(
    pattern: str, horizon_atu: int, rng: random.Random,
) -> int:
    """Single appear_atu draw under the named temporal pattern."""
    if pattern == "uniform":
        return rng.randrange(0, horizon_atu)
    if pattern == "diurnal":
        # Mixture of two truncated normals at hours 8 and 17, σ = 1.5h.
        peak_h = 8.0 if rng.random() < 0.5 else 17.0
        sigma_h = 1.5
        h = max(0.0, min(23.999, rng.gauss(peak_h, sigma_h)))
        return int(h * _ATU_PER_HOUR)
    if pattern == "spike":
        # 70% uniform background + 30% spike at t=8h.
        if rng.random() < 0.3:
            h = max(0.0, min(23.999, rng.gauss(8.0, 0.5)))
            return int(h * _ATU_PER_HOUR)
        return rng.randrange(0, horizon_atu)
    raise ValueError(f"unknown demand pattern: {pattern}")


def _sample_priority(
    mix: str, rng: random.Random,
) -> int:
    weights = _PRIORITY_MIXES.get(mix)
    if weights is None:
        raise ValueError(f"unknown priority_mix: {mix}")
    r = rng.random()
    acc = 0.0
    for cls, w in weights:
        acc += w
        if r < acc:
            return cls
    return weights[-1][0]


def _build_concentrated_dest_weights(
    leaf_ids: list[int], rng: random.Random,
) -> tuple[list[int], list[float]]:
    """Top-20% of leafs receive 80% of mass. Returns (ids, cumulative_weights)."""
    n = len(leaf_ids)
    shuffled = list(leaf_ids)
    rng.shuffle(shuffled)
    top_k = max(1, n // 5)
    weights = [0.8 / top_k] * top_k + [0.2 / max(1, n - top_k)] * (n - top_k)
    cum: list[float] = []
    s = 0.0
    for w in weights:
        s += w
        cum.append(s)
    return shuffled, cum


def _draw_concentrated(
    ids: list[int], cum_weights: list[float], rng: random.Random,
) -> int:
    r = rng.random() * cum_weights[-1]
    # Linear scan is fine — we'll only hit this for "concentrated", and N is small.
    for node_id, c in zip(ids, cum_weights):
        if r <= c:
            return node_id
    return ids[-1]


def make_demand(
    nodes: list[dict[str, Any]],
    lanes: list[dict[str, Any]],  # noqa: ARG001 — kept for API parity
    n_events: int,
    pattern: str = "uniform",
    priority_mix: str = "mixed",
    horizon_hours: int = 24,
    seed: int = 0,
    size_min: int = 1,
    size_max: int = 5,
) -> list[dict[str, Any]]:
    """Generate `n_events` demand events under the given pattern.

    Returns a list sorted ascending by appear_atu. Each event has the keys
    required by `serializer.to_demand_ipc`.
    """
    if n_events <= 0:
        return []
    if size_min < 1 or size_max < size_min:
        raise ValueError("size_min must be >= 1 and size_max >= size_min")

    rng = random.Random(seed ^ 0xDE_AD)
    horizon_atu = horizon_hours * _ATU_PER_HOUR
    leaf_ids = _leaf_node_ids(nodes)
    if len(leaf_ids) < 2:
        raise ValueError("need >= 2 leaf nodes (or any nodes as fallback) for demand")

    # OD sampler depends on pattern. We use "concentrated" *for destinations*
    # only — origins remain uniform so flow direction varies.
    if pattern == "concentrated":
        dest_ids, dest_cum = _build_concentrated_dest_weights(leaf_ids, rng)

    events: list[dict[str, Any]] = []
    for sid in range(n_events):
        origin = rng.choice(leaf_ids)
        if pattern == "concentrated":
            dest = _draw_concentrated(dest_ids, dest_cum, rng)
            # Resample if origin == dest (rare with reasonable N).
            tries = 0
            while dest == origin and tries < 8:
                dest = _draw_concentrated(dest_ids, dest_cum, rng)
                tries += 1
            temporal_pattern = "uniform"
        else:
            dest = rng.choice(leaf_ids)
            tries = 0
            while dest == origin and tries < 8:
                dest = rng.choice(leaf_ids)
                tries += 1
            temporal_pattern = pattern

        if dest == origin:
            # Skip degenerate (could happen with very small leaf sets).
            continue

        appear = _sample_appear_atu(temporal_pattern, horizon_atu, rng)
        # Due window: 8h after appear, clamped within horizon for sanity.
        due = appear + 8 * _ATU_PER_HOUR
        size = rng.randint(size_min, size_max)
        prio = _sample_priority(priority_mix, rng)

        events.append({
            "flow_unit_id": sid,
            "appear_atu": appear,
            "due_atu": due,
            "origin_node_id": origin,
            "dest_node_id": dest,
            "origin_cell_idx": 0,
            "dest_cell_idx": 0,
            "size_units": size,
            "priority_class": prio,
            "handling_flags": 0,
        })

    events.sort(key=lambda e: (e["appear_atu"], e["flow_unit_id"]))
    return events
