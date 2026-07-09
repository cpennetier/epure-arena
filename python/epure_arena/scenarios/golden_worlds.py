"""Hand-designed adversarial scenarios for Tier-3 calibration.

The §B random-scenario generator is good for benchmarking, but it
masks the specific failure modes of greedy/online routing. Phase-1
calibration needs *small, completely understood* instances where the
optimal answer can be hand-checked and the gap to greedy is visible.

Each builder returns a ``ScenarioBundle`` (the same struct
``generate_scenario`` returns) so it can be fed straight into
the engine or to the planner. Every scenario
carries a ``manifest`` dict with:

  * ``name`` — short identifier (``"bottleneck_bridge"``, …).
  * ``optimal_stranded`` — number of flow_units the optimal solution
    must strand on this instance. Use as the ground-truth lower bound
    for the optimality-gap experiment.
  * ``description`` — one-line natural-language explanation.
  * ``expected_greedy_failure`` — qualitative note on what the
    earliest-arrival router does wrong.

All scenarios are deliberately tiny (≤ 8 nodes, ≤ 50 flow_units) so the
ILP solver in ``epure_arena.lattice.ilp_solver`` can find the
provable optimum in seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from epure_arena.scenarios.library import ScenarioBundle
from epure_arena.scenarios.serializer import (
    to_demand_ipc,
    to_lane_ipc,
    to_node_ipc,
    to_schedule_ipc,
)

_ATU_PER_HOUR = 3_600_000


# ──────────────────────────────────────────────────────────────────────
#  Helpers — minimal valid payloads for the Arrow schemas
# ──────────────────────────────────────────────────────────────────────


def _node(node_id: int, lat: float = 0.0, lon: float = 0.0) -> dict[str, Any]:
    return {
        "node_id": node_id, "h3_index": node_id,
        "lat": lat, "lon": lon, "tier": "leaf",
        "territory_id": 0, "demand_gravity": 1.0,
        "has_airport": False, "has_port": False, "has_rail": False,
        "landmass_id": 0,
    }


def _lane(
    lane_id: int, src: int, dst: int, *,
    duration_atu: int = _ATU_PER_HOUR,
    cost_usd_micros: int = 1_000_000,
    co2_g: int = 100,
    lane_mode: int = 0,
) -> dict[str, Any]:
    return {
        "lane_id": lane_id, "src_node_id": src, "dst_node_id": dst,
        "src_lat": 0.0, "src_lon": 0.0, "dst_lat": 0.0, "dst_lon": 0.0,
        "lane_mode": lane_mode,
        "duration_sec": duration_atu / 1000.0,
        "duration_atu": duration_atu,
        "cost_usd": cost_usd_micros / 1_000_000.0,
        "cost_usd_micros": cost_usd_micros,
        "co2_g": co2_g, "phase": "trunk",
    }


def _slot(lane_id: int, depart_atu: int, capacity: int) -> dict[str, Any]:
    return {
        "lane_id": lane_id, "depart_atu": depart_atu, "capacity": capacity,
    }


def _flow_unit(
    sid: int, *, origin: int, dest: int, appear_atu: int,
    due_atu: int, size_units: int = 1, priority_class: int = 2,
) -> dict[str, Any]:
    return {
        "flow_unit_id": sid, "appear_atu": appear_atu, "due_atu": due_atu,
        "origin_node_id": origin, "dest_node_id": dest,
        "origin_cell_idx": 0, "dest_cell_idx": 0,
        "size_units": size_units, "priority_class": priority_class,
        "handling_flags": 0,
    }


def _bundle(
    name: str,
    nodes: list[dict],
    lanes: list[dict],
    schedules: list[dict],
    demand: list[dict],
    *,
    horizon_hours: int,
    optimal_stranded: int,
    description: str,
    expected_greedy_failure: str,
    extra_manifest: dict[str, Any] | None = None,
) -> ScenarioBundle:
    """Stamp ``ScenarioBundle`` from raw row dicts."""
    demand_sorted = sorted(demand, key=lambda e: (e["appear_atu"], e["flow_unit_id"]))
    manifest = {
        "name": name,
        "n_nodes": len(nodes),
        "n_lanes": len(lanes),
        "n_schedule_slots": len(schedules),
        "n_demand_events": len(demand),
        "horizon_hours": horizon_hours,
        "optimal_stranded": optimal_stranded,
        "description": description,
        "expected_greedy_failure": expected_greedy_failure,
        "kind": "adversarial",
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    return ScenarioBundle(
        node_ipc=to_node_ipc(nodes),
        lane_ipc=to_lane_ipc(lanes),
        schedule_ipc=to_schedule_ipc(schedules),
        demand_ipc=to_demand_ipc(demand_sorted),
        manifest=manifest,
    )


# ──────────────────────────────────────────────────────────────────────
#  Scenario A — Bottleneck Bridge
# ──────────────────────────────────────────────────────────────────────


def bottleneck_bridge(
    *,
    n_flow_units: int = 10,
    n_vip: int = 2,
    short_path_capacity: int = 2,
    long_path_capacity: int = 10,
    horizon_hours: int = 6,
) -> ScenarioBundle:
    """Two paths from ``0 → 3``: a short low-capacity one and a long high-cap one.

    Topology::

        0 ──[e0, dur=1h, cap=short]── 1 ──[e1, dur=1h, cap=10]── 3
        │                                                          │
        └──[e2, dur=1h, cap=long]── 2 ──[e3, dur=1h, cap=10]──────┘

    All flow_units appear at node 0 at t=0, must reach node 3 by t=6h.

    **Greedy failure mode.** The earliest-arrival router sees both
    paths as equal duration (2 hops, 2h each) and routes everyone via
    the first lane in CSR order, which is e0. e0 has only
    ``short_path_capacity`` slots in the first hour, so the rest
    strand or wait several hours.

    **Optimal solution.** Send ``short_path_capacity`` flow_units via
    e0/e1 and the rest via e2/e3. With
    ``n_flow_units ≤ short_path_capacity + long_path_capacity`` everyone
    delivers; ``optimal_stranded = max(0, n_flow_units - (sc + lc))``.
    """
    if n_vip > n_flow_units:
        raise ValueError("n_vip must be <= n_flow_units")
    nodes = [_node(i) for i in range(4)]
    lanes = [
        _lane(0, 0, 1, duration_atu=_ATU_PER_HOUR),
        _lane(1, 1, 3, duration_atu=_ATU_PER_HOUR),
        _lane(2, 0, 2, duration_atu=_ATU_PER_HOUR),
        _lane(3, 2, 3, duration_atu=_ATU_PER_HOUR),
    ]
    # Each lane has one slot per hour over the horizon. Capacity is the
    # number of flow_units that fit in that single slot.
    schedules: list[dict] = []
    cap_for_lane = {
        0: short_path_capacity, 1: 10,
        2: long_path_capacity,  3: 10,
    }
    for lane_id, cap in cap_for_lane.items():
        for h in range(horizon_hours):
            schedules.append(_slot(lane_id, h * _ATU_PER_HOUR, cap))

    demand = []
    for i in range(n_flow_units):
        priority = 0 if i < n_vip else 3
        demand.append(_flow_unit(
            sid=i, origin=0, dest=3,
            appear_atu=0, due_atu=horizon_hours * _ATU_PER_HOUR,
            size_units=1, priority_class=priority,
        ))

    # Both paths are 2 hops at 1h each. With horizon H, the last legal
    # first-hop launch bucket is H - 2 (so the flow_unit arrives at dest
    # by H). That gives (H - 2 + 1) = H - 1 launch opportunities per
    # path. Per-path throughput = min(first_hop_cap, second_hop_cap).
    # For our lane layout the second-hop cap is fixed at 10 on both
    # paths so the binding constraint is the first-hop cap on the
    # short path and 10 on the long path.
    n_launch_buckets = max(0, horizon_hours - 1)
    short_throughput = min(short_path_capacity, 10)
    long_throughput = min(long_path_capacity, 10)
    capacity_total = (short_throughput + long_throughput) * n_launch_buckets
    optimal_stranded = max(0, n_flow_units - capacity_total)
    return _bundle(
        name="bottleneck_bridge",
        nodes=nodes, lanes=lanes, schedules=schedules, demand=demand,
        horizon_hours=horizon_hours,
        optimal_stranded=optimal_stranded,
        description=(
            f"4 nodes, 2 paths from 0→3. Short path cap={short_path_capacity}, "
            f"long path cap={long_path_capacity}. {n_flow_units} flow_units "
            f"({n_vip} VIP) appear at t=0."
        ),
        expected_greedy_failure=(
            "Earliest-arrival router picks the first lane in CSR order, "
            "overloading the short path's slot capacity."
        ),
        extra_manifest={
            "short_path_capacity": short_path_capacity,
            "long_path_capacity": long_path_capacity,
            "n_vip": n_vip,
        },
    )


# ──────────────────────────────────────────────────────────────────────
#  Scenario B — Time Conflict
# ──────────────────────────────────────────────────────────────────────


def time_conflict(
    *,
    n_flow_units: int = 3,
    horizon_hours: int = 6,
) -> ScenarioBundle:
    """Single lane ``0 → 1`` with one slot of capacity 1 per hour.

    The greedy/optimal gap here is small (FIFO actually finds the
    optimal allocation when capacity = 1 per slot), but the scenario
    is the simplest test of "does the planner respect schedule
    windows" and "does the ILP give the same answer as the greedy
    router on a trivially priced instance".

    All ``n_flow_units`` appear at t=0 and need to reach node 1 by
    horizon. With ``horizon_hours`` slots of capacity 1 each, exactly
    ``min(horizon_hours, n_flow_units)`` deliver. Any excess strands.
    """
    nodes = [_node(0), _node(1)]
    lanes = [_lane(0, 0, 1, duration_atu=_ATU_PER_HOUR)]
    schedules = [
        _slot(0, h * _ATU_PER_HOUR, 1)
        for h in range(horizon_hours)
    ]
    demand = []
    for i in range(n_flow_units):
        priority = i % 4   # spread across VIP / Express / Std / Eco
        demand.append(_flow_unit(
            sid=i, origin=0, dest=1,
            appear_atu=0,
            due_atu=horizon_hours * _ATU_PER_HOUR,
            priority_class=priority,
        ))
    optimal_stranded = max(0, n_flow_units - horizon_hours)
    return _bundle(
        name="time_conflict",
        nodes=nodes, lanes=lanes, schedules=schedules, demand=demand,
        horizon_hours=horizon_hours,
        optimal_stranded=optimal_stranded,
        description=(
            f"Single 0→1 lane, capacity 1 per hour, horizon "
            f"{horizon_hours}h. {n_flow_units} flow_units appear at t=0."
        ),
        expected_greedy_failure=(
            "FIFO is optimal when capacity = 1 per slot; this scenario "
            "exists to lock in that the ILP and greedy agree on a "
            "trivially priced instance."
        ),
    )


# ──────────────────────────────────────────────────────────────────────
#  Scenario C — Cascade
# ──────────────────────────────────────────────────────────────────────


def cascade(
    *,
    n_flow_units: int = 100,
    fast_capacity: int = 5,
    medium_capacity: int = 20,
    slow_capacity: int = 50,
    horizon_hours: int = 6,
) -> ScenarioBundle:
    """Three parallel paths from ``0 → 7`` with different cost / capacity.

    Topology — each "path" is a chain of distinct nodes::

        Fast (2 hops):   0 ─[e0,1h,cap=fast]── 1 ─[e1,1h,cap=fast]── 7
        Medium (3 hops): 0 ─[e2,1h,cap=med]── 2 ─[e3,1h,cap=med]── 3 ─[e4,1h,cap=med]── 7
        Slow (4 hops):   0 ─[e5,1h,cap=slow]── 4 ─[e6,1h,cap=slow]── 5 ─[e7,1h,cap=slow]── 6 ─[e8,1h,cap=slow]── 7

    **Greedy failure mode.** Earliest-arrival fills the fast path
    first (everyone wants 2 hops), then medium, then slow — but each
    path has *per-slot* capacity, so when the first slot of fast
    fills, the next batch goes to medium's first slot, and so on.
    Because the fast path has so few slots, flow_units queue at node 0
    and many strand.

    **Optimal solution.** Distribute load proportional to per-path
    throughput from the start. With ``horizon_hours`` slots per
    lane and 1 unit per slot, the per-hour throughputs are
    ``(fast, medium, slow) = (cap_fast, cap_medium, cap_slow)``.
    Total capacity over horizon =
    ``horizon * (cap_fast + cap_medium + cap_slow)``. The optimum
    strands ``max(0, n_flow_units - total_capacity)``.
    """
    nodes = [_node(i) for i in range(8)]
    lanes = [
        # Fast path: 0 → 1 → 7
        _lane(0, 0, 1, duration_atu=_ATU_PER_HOUR),
        _lane(1, 1, 7, duration_atu=_ATU_PER_HOUR),
        # Medium path: 0 → 2 → 3 → 7
        _lane(2, 0, 2, duration_atu=_ATU_PER_HOUR),
        _lane(3, 2, 3, duration_atu=_ATU_PER_HOUR),
        _lane(4, 3, 7, duration_atu=_ATU_PER_HOUR),
        # Slow path: 0 → 4 → 5 → 6 → 7
        _lane(5, 0, 4, duration_atu=_ATU_PER_HOUR),
        _lane(6, 4, 5, duration_atu=_ATU_PER_HOUR),
        _lane(7, 5, 6, duration_atu=_ATU_PER_HOUR),
        _lane(8, 6, 7, duration_atu=_ATU_PER_HOUR),
    ]
    cap_for_lane = {
        0: fast_capacity, 1: fast_capacity,
        2: medium_capacity, 3: medium_capacity, 4: medium_capacity,
        5: slow_capacity, 6: slow_capacity, 7: slow_capacity,
        8: slow_capacity,
    }
    schedules: list[dict] = []
    for lane_id, cap in cap_for_lane.items():
        for h in range(horizon_hours):
            schedules.append(_slot(lane_id, h * _ATU_PER_HOUR, cap))

    demand = [
        _flow_unit(
            sid=i, origin=0, dest=7,
            appear_atu=0,
            due_atu=horizon_hours * _ATU_PER_HOUR,
            priority_class=2,
        )
        for i in range(n_flow_units)
    ]
    # Per-path effective capacity = per_hour * (horizon - hops + 1),
    # because a flow_unit needs `hops` consecutive buckets to traverse.
    fast_cap   = fast_capacity   * max(0, horizon_hours - 2 + 1)
    medium_cap = medium_capacity * max(0, horizon_hours - 3 + 1)
    slow_cap   = slow_capacity   * max(0, horizon_hours - 4 + 1)
    total_capacity = fast_cap + medium_cap + slow_cap
    optimal_stranded = max(0, n_flow_units - total_capacity)
    return _bundle(
        name="cascade",
        nodes=nodes, lanes=lanes, schedules=schedules, demand=demand,
        horizon_hours=horizon_hours,
        optimal_stranded=optimal_stranded,
        description=(
            f"3 parallel paths 0→7 with capacities "
            f"({fast_capacity}, {medium_capacity}, {slow_capacity}) "
            f"per hour. {n_flow_units} flow_units appear at t=0."
        ),
        expected_greedy_failure=(
            "Earliest-arrival fills the shortest path first. Without "
            "load-balancing across paths, flow_units strand even though "
            "total system capacity is sufficient."
        ),
        extra_manifest={
            "fast_capacity": fast_capacity,
            "medium_capacity": medium_capacity,
            "slow_capacity": slow_capacity,
        },
    )


# ──────────────────────────────────────────────────────────────────────
#  Scenario D — Fallback Required (plan report §13.2 golden world)
# ──────────────────────────────────────────────────────────────────────


def fallback_required(
    *,
    n_flow_units: int = 5,
    horizon_hours: int = 8,
) -> ScenarioBundle:
    """Delivery REQUIRES the alternative path for all but one flow_unit.

    Topology::

        0 ──[e0, dur=1h, ONE slot @t=0, cap=1]── 1 ──[e1, hourly, cap=10]── 3
        └──[e2, dur=2h, hourly, cap=10]───────── 2 ──[e3, dur=2h, hourly]── 3

    All flow_units appear at node 0 at t=0, due at horizon. The fast path
    (0→1→3, 2h) admits exactly ONE flow_unit — e0 has a single departure of
    capacity 1 and no later slot. The slow path (0→2→3, 4h) admits everyone.

    **Greedy failure mode (known).** The earliest-arrival router sends ALL
    flow_units to e0; one boards; the rest queue on e0, the wake finds no later
    slot, and the whole queue strands (QueueDeath) — even though the slow
    path delivers them with hours of slack. ``optimal_stranded = 0``.

    **Planner requirement.** Commit 1 via the fast path and n-1 via the slow
    path; zero failures.
    """
    nodes = [_node(i) for i in range(4)]
    lanes = [
        _lane(0, 0, 1, duration_atu=_ATU_PER_HOUR),
        _lane(1, 1, 3, duration_atu=_ATU_PER_HOUR),
        _lane(2, 0, 2, duration_atu=2 * _ATU_PER_HOUR),
        _lane(3, 2, 3, duration_atu=2 * _ATU_PER_HOUR),
    ]
    schedules: list[dict] = [_slot(0, 0, 1)]  # the trap: one fast slot, cap 1
    for lane_id in (1, 2, 3):
        for h in range(horizon_hours):
            schedules.append(_slot(lane_id, h * _ATU_PER_HOUR, 10))
    demand = [
        _flow_unit(sid=i, origin=0, dest=3, appear_atu=0,
                  due_atu=horizon_hours * _ATU_PER_HOUR)
        for i in range(n_flow_units)
    ]
    return _bundle(
        name="fallback_required",
        nodes=nodes, lanes=lanes, schedules=schedules, demand=demand,
        horizon_hours=horizon_hours,
        optimal_stranded=0,
        description=(
            f"{n_flow_units} flow_units 0→3; fast path admits exactly 1 (single "
            f"cap-1 slot on e0, no later departure); slow path admits all."
        ),
        expected_greedy_failure=(
            "All flow_units routed to e0's single slot; the queue dies with no "
            "future slot (strand site QueueDeath) despite the feasible slow path."
        ),
    )


# ──────────────────────────────────────────────────────────────────────
#  Scenario E — Time-Window Trap (plan report §13.2 golden world)
# ──────────────────────────────────────────────────────────────────────


def time_window_trap(
    *,
    n_flow_units: int = 3,
    horizon_hours: int = 8,
) -> ScenarioBundle:
    """Capacity admits one ON-TIME flow_unit; the rest can only travel LATE.

    Topology: single lane ``0 → 1`` (dur 1h) with two departures —
    t=0 (capacity 1) and t=4h (capacity 10). All flow_units appear at t=0 with
    due = 3h.

    The capacity-free oracle says every flow_unit is FEASIBLE_ON_TIME (the t=0
    slot exists in the timetable), so a failed flow_unit here is the canonical
    CAPACITY_BLOCKED case — and a TRUE capacity shortfall (R1 once the LP
    certifies it), not planner myopia: only one flow_unit can ever make the
    deadline. ``optimal_stranded`` (on-time) ``= n_flow_units - 1``.

    **Greedy behavior (known).** One boards at t=0; the rest queue, wake at
    t=4h, board, and deliver LATE (no strand) — the greedy converts a
    capacity shortfall into silent SLA misses.
    """
    nodes = [_node(0), _node(1)]
    lanes = [_lane(0, 0, 1, duration_atu=_ATU_PER_HOUR)]
    schedules = [_slot(0, 0, 1), _slot(0, 4 * _ATU_PER_HOUR, 10)]
    demand = [
        _flow_unit(sid=i, origin=0, dest=1, appear_atu=0,
                  due_atu=3 * _ATU_PER_HOUR)
        for i in range(n_flow_units)
    ]
    return _bundle(
        name="time_window_trap",
        nodes=nodes, lanes=lanes, schedules=schedules, demand=demand,
        horizon_hours=horizon_hours,
        optimal_stranded=n_flow_units - 1,
        description=(
            f"{n_flow_units} flow_units 0→1 due 3h; slots t=0 (cap 1) and t=4h "
            f"(cap 10): exactly one on-time delivery exists."
        ),
        expected_greedy_failure=(
            "Queued flow_units board the 4h slot and deliver late — the capacity "
            "shortfall surfaces as silent SLA misses, not typed failures."
        ),
    )


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────


SCENARIO_BUILDERS = {
    "bottleneck_bridge": bottleneck_bridge,
    "time_conflict": time_conflict,
    "cascade": cascade,
    "fallback_required": fallback_required,
    "time_window_trap": time_window_trap,
}


def all_scenarios() -> list[tuple[str, ScenarioBundle]]:
    """Convenience: return one instance of every named adversarial scenario."""
    return [(name, builder()) for name, builder in SCENARIO_BUILDERS.items()]
