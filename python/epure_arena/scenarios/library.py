"""ScenarioSpec → ScenarioBundle orchestration.

A `ScenarioSpec` is the seven-tuple that drives the scenario generator.
`generate_scenario` is the only entry point most callers need.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from epure_arena.scenarios.demand import make_demand
from epure_arena.scenarios.generators import (
    TOPOLOGY_FNS,
    make_mesh,
)
from epure_arena.scenarios.schedules import make_schedules
from epure_arena.scenarios.serializer import (
    to_demand_ipc,
    to_lane_ipc,
    to_node_ipc,
    to_schedule_ipc,
)

_VALID_TOPOLOGIES = ("star", "backbone", "grid", "tree", "mesh")
_VALID_DENSITIES = ("sparse", "moderate", "dense")
_VALID_CAPACITIES = ("tight", "balanced", "loose")
_VALID_DEMAND = ("uniform", "concentrated", "diurnal", "spike")
_VALID_CADENCES = ("hourly", "every_4h", "daily", "mixed")
_VALID_PRIORITY_MIXES = ("all_standard", "heavy_vip", "mixed")

# Capacity-regime multipliers vs the *baseline* (= average flow units per slot).
# Calibrated empirically against backbone @ n_nodes=200, n_events=10k, diurnal,
# hourly cadence so that:
#   tight   → ~20–40% stranded (queues active, mean_wait grows)
#   balanced→ ~5–15% stranded  (occasional queueing)
#   loose   → ~0–5% stranded   (queues rare)
# The strand-vs-capacity curve is threshold-like — small multiplier swings
# around 1.0×baseline produce large strand-rate deltas.
_CAPACITY_MULT: dict[str, float] = {
    "tight": 1.15,
    "balanced": 1.85,
    "loose": 3.0,
}


@dataclass(frozen=True)
class ScenarioSpec:
    """Specification of one synthetic DES scenario.

    All fields are required except where noted. Two specs that compare
    equal produce byte-identical `ScenarioBundle.{node,lane,schedule,demand}_ipc`.
    """

    topology: str
    n_nodes: int
    density: str
    capacity: str
    demand_pattern: str
    schedule_cadence: str
    priority_mix: str
    n_demand_events: int
    horizon_hours: int = 24
    seed: int = 42

    def __post_init__(self) -> None:
        if self.topology not in _VALID_TOPOLOGIES:
            raise ValueError(f"topology must be one of {_VALID_TOPOLOGIES}")
        if self.n_nodes < 2:
            raise ValueError("n_nodes must be >= 2")
        if self.density not in _VALID_DENSITIES:
            raise ValueError(f"density must be one of {_VALID_DENSITIES}")
        if self.capacity not in _VALID_CAPACITIES:
            raise ValueError(f"capacity must be one of {_VALID_CAPACITIES}")
        if self.demand_pattern not in _VALID_DEMAND:
            raise ValueError(f"demand_pattern must be one of {_VALID_DEMAND}")
        if self.schedule_cadence not in _VALID_CADENCES:
            raise ValueError(f"schedule_cadence must be one of {_VALID_CADENCES}")
        if self.priority_mix not in _VALID_PRIORITY_MIXES:
            raise ValueError(f"priority_mix must be one of {_VALID_PRIORITY_MIXES}")
        if self.n_demand_events < 0:
            raise ValueError("n_demand_events must be >= 0")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be > 0")


@dataclass(frozen=True)
class ScenarioBundle:
    """Output of `generate_scenario` — Arrow IPC bytes + an aggregate manifest."""

    node_ipc: bytes
    lane_ipc: bytes
    schedule_ipc: bytes
    demand_ipc: bytes
    manifest: dict[str, Any] = field(default_factory=dict)


def _generate_topology(
    topology: str, n_nodes: int, density: str, seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Dispatch to the topology generator. `mesh` takes density."""
    if topology == "mesh":
        return make_mesh(n_nodes, seed, density=density)
    return TOPOLOGY_FNS[topology](n_nodes, seed)


def _avg_path_length(topology: str, n_nodes: int) -> int:
    """Approximate mean shortest-path length (in hops) across random OD pairs.

    Used by `_apply_capacity` to budget per-slot capacity against the *flow*
    each flow_unit produces, not just the demand event count.

    The previous constant `2` underestimated for topologies where most
    OD pairs traverse 4+ lanes (backbone, tree, large grids), which made
    the `tight` and `balanced` regimes over-strand.
    """
    if topology == "star":
        return 2  # leaf → center → leaf
    if topology == "backbone":
        return 4  # leaf → gw → gw → leaf, possibly via a regional
    if topology == "grid":
        # Mean Manhattan distance on √n × √n with uniform OD ≈ (2/3) · side.
        side = max(2, int(math.sqrt(n_nodes)))
        return max(2, (2 * side) // 3)
    if topology == "tree":
        # Two flow_units' paths meet at a common ancestor; depth ≈ log2(n).
        return max(2, 2 * max(1, int(math.log2(max(2, n_nodes)))))
    if topology == "mesh":
        # Spanning ring + ER extras: small-world. Diameter is small.
        return 3
    return 3


def _apply_capacity(
    lanes: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
    events: list[dict[str, Any]],
    regime: str,
    topology: str,
    n_nodes: int,
) -> int:
    """Rewrite slot capacities so total throughput vs demand matches `regime`.

    Capacity budget is `multiplier × (total_demand × avg_path_len) / n_slots`,
    where `avg_path_len` is topology-aware (see `_avg_path_length`).
    Returns the per-slot capacity actually used (uniform across slots).
    """
    if not schedules:
        return 0
    total_demand_units = sum(e["size_units"] for e in events)
    max_event_size = max((e["size_units"] for e in events), default=1)
    avg_path_len = _avg_path_length(topology, n_nodes)
    total_flow_units = max(1, total_demand_units * avg_path_len)
    n_slots = len(schedules)
    # Use float division so small workloads don't truncate to 0.
    baseline = max(1.0, total_flow_units / n_slots)
    raw_cap = int(round(baseline * _CAPACITY_MULT[regime]))
    # Sanity floor: any slot must fit the largest single flow_unit, otherwise
    # those flow_units are unboardable on any schedule and strand as
    # NoFutureDeparture regardless of regime — which would conflate
    # capacity-induced strand with a generator pathology.
    floor = max_event_size if regime != "tight" else max(1, max_event_size // 2)
    cap_per_slot = max(1, max(floor, raw_cap))
    for s in schedules:
        s["capacity"] = cap_per_slot
    return cap_per_slot


def generate_scenario(spec: ScenarioSpec) -> ScenarioBundle:
    """Generate a complete DES-ready Arrow IPC bundle from a `ScenarioSpec`."""
    nodes, lanes = _generate_topology(spec.topology, spec.n_nodes, spec.density, spec.seed)
    if not lanes:
        raise RuntimeError(
            f"topology '{spec.topology}' with n_nodes={spec.n_nodes} produced 0 lanes",
        )

    events = make_demand(
        nodes=nodes,
        lanes=lanes,
        n_events=spec.n_demand_events,
        pattern=spec.demand_pattern,
        priority_mix=spec.priority_mix,
        horizon_hours=spec.horizon_hours,
        seed=spec.seed,
    )

    schedules = make_schedules(
        lanes=lanes,
        cadence=spec.schedule_cadence,
        horizon_hours=spec.horizon_hours,
        seed=spec.seed,
    )
    cap_per_slot = _apply_capacity(
        lanes, schedules, events, spec.capacity, spec.topology, spec.n_nodes,
    )

    bundle = ScenarioBundle(
        node_ipc=to_node_ipc(nodes),
        lane_ipc=to_lane_ipc(lanes),
        schedule_ipc=to_schedule_ipc(schedules),
        demand_ipc=to_demand_ipc(events),
        manifest={
            "spec": {
                "topology": spec.topology, "n_nodes": spec.n_nodes,
                "density": spec.density, "capacity": spec.capacity,
                "demand_pattern": spec.demand_pattern,
                "schedule_cadence": spec.schedule_cadence,
                "priority_mix": spec.priority_mix,
                "n_demand_events": spec.n_demand_events,
                "horizon_hours": spec.horizon_hours, "seed": spec.seed,
            },
            "n_nodes": len(nodes),
            "n_lanes": len(lanes),
            "n_schedules": len(schedules),
            "n_demand": len(events),
            "total_demand_units": sum(e["size_units"] for e in events),
            "cap_per_slot": cap_per_slot,
            "horizon_atu": spec.horizon_hours * 3_600_000,
        },
    )
    return bundle
