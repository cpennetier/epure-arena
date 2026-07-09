"""The bottleneck knob: a data-side capacity overwrite on a chosen cut.

The engine enforces capacity per (lane, departure) slot
(``slot_usage + reserved + size <= slot.capacity``); generated worlds are
additive by construction because ``_apply_capacity`` writes a uniform
per-slot capacity of >= 1.15x mean flow. This module manufactures
controlled contention on ONE named cut while holding everything else
fixed:

  * ``free_flow_slot_state`` — measure the unperturbed per-slot assignment
    D(s) (valid as *free flow* only when the baseline run strands zero
    flow units on capacity; the caller must assert this precondition).
  * ``select_cut`` — S_cut = the slots of the top-K lanes by in-window
    usage whose depart_atu falls in the window and whose D(s) > 0.
  * ``apply_rho`` — overwrite capacity[s] := max(1, ceil(D(s)/rho)) for
    s in S_cut; every other row of the schedule table is preserved
    verbatim (same rows, same order, only the capacity column of cut
    rows differs). rho is the target utilization of the free-flow
    assignment on the cut: rho < 1 leaves headroom, rho > 1 makes the
    free-flow assignment infeasible on the cut and forces queueing or
    detours.

D(s) = 0 slots inside the window are deliberately NOT patched: writing
``ceil(0/rho) = 0`` would structurally cancel the slot (capacity 0 is a
cancelled slot per contract I-C-010), which changes the schedule — and
the schedule is held fixed across the sweep.

Data-side only: no engine, pin, or generator code is touched; the knob
rewrites Arrow bytes between generation and the engine.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import pyarrow.ipc as ipc

from epure_arena import _engine
from epure_arena.scenarios.library import ScenarioBundle
from epure_arena.scenarios.serializer import to_schedule_ipc

_H = 3_600_000


@dataclass(frozen=True)
class Cut:
    """A named (lanes x departure-window) cut with its free-flow demand.

    Attributes:
        lane_ids: The lanes in the cut, sorted ascending.
        window_atu: Half-open departure window [lo, hi) in ATU.
        demand: (lane_id, depart_atu) -> D(s), the size-weighted free-flow
            units boarded on that slot in the unperturbed world. Only
            slots with D(s) > 0 are members of S_cut.
    """

    lane_ids: tuple[int, ...]
    window_atu: tuple[int, int]
    demand: dict[tuple[int, int], int]

    def total_demand(self) -> int:
        return sum(self.demand.values())


def free_flow_slot_state(
    bundle: ScenarioBundle, horizon_hours: int = 24,
    *, enforce_capacity: bool = True,
) -> tuple[dict[str, list], dict[str, Any]]:
    """Run the plain DES and return (slot_state, kpis).

    ``slot_state['usage']`` is the size-weighted booked units per slot.
    With ``enforce_capacity=True`` (default, Gate-1 semantics) it equals
    the free-flow assignment ONLY when the run strands nothing on
    capacity — the caller must check
    ``kpis['strand_reasons']['capacity_exhausted']['count'] == 0``.
    With ``enforce_capacity=False`` the run is the UNCENSORED free-flow
    assignment (Oracle-Baseline mode): valid D(s) on any world, at the
    cost of usage possibly exceeding the printed capacities.
    """
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        bundle.demand_ipc, _H, "time_aware",
    )
    eng.set_enforce_capacity(enforce_capacity)
    eng.run(horizon_hours * _H)
    return eng.slot_state(), json.loads(eng.get_kpis())


def free_flow_slot_demand(
    bundle: ScenarioBundle, horizon_hours: int = 24,
) -> dict[str, list]:
    """UNCENSORED free-flow slot demand via the hop trace.

    Capacity-free engine mode does not book ``slot_usage`` (it bypasses
    the booking path), so the uncensored per-slot assignment is
    reconstructed from ``hop_records`` (one record per realized boarding)
    joined with unit sizes. Returns a ``slot_state``-shaped dict of
    parallel lists (``lane_id``, ``depart_atu``, ``usage``) suitable for
    ``select_cut`` — valid as D(s) on ANY world, congested or not.
    """
    import pyarrow.ipc as ipc

    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc,
        bundle.demand_ipc, _H, "time_aware",
    )
    eng.set_enforce_capacity(False)
    eng.set_record_hop_trace(True)
    eng.run(horizon_hours * _H)
    hops = eng.hop_records()
    e2c = eng.get_lane_id_to_csr()
    csr_to_lane = {int(c): int(lid) for lid, c in enumerate(e2c)}
    dem = ipc.open_stream(bundle.demand_ipc).read_all()
    size_of = dict(zip(dem.column("flow_unit_id").to_pylist(),
                       dem.column("size_units").to_pylist()))
    agg: dict[tuple[int, int], int] = {}
    for sid, csr, dep in zip(hops["flow_unit_id"], hops["csr_lane"],
                             hops["depart_atu"]):
        key = (csr_to_lane[int(csr)], int(dep))
        agg[key] = agg.get(key, 0) + int(size_of.get(int(sid), 0))
    keys = sorted(agg)
    return {
        "lane_id": [k[0] for k in keys],
        "depart_atu": [k[1] for k in keys],
        "usage": [agg[k] for k in keys],
    }


def select_cut(
    slot_state: dict[str, list],
    *,
    window_h: tuple[int, int] = (6, 12),
    n_lanes: int = 3,
) -> Cut:
    """Pick the top-``n_lanes`` lanes by in-window free-flow usage.

    Deterministic: ties broken by ascending lane_id. S_cut = the chosen
    lanes' in-window slots with usage > 0.
    """
    lo, hi = window_h[0] * _H, window_h[1] * _H
    per_lane: dict[int, int] = {}
    for u, d, l in zip(slot_state["usage"], slot_state["depart_atu"],
                       slot_state["lane_id"]):
        if lo <= d < hi and u > 0:
            per_lane[l] = per_lane.get(l, 0) + u
    if not per_lane:
        raise ValueError(f"no free-flow usage in window {window_h}")
    chosen = sorted(sorted(per_lane), key=lambda l: -per_lane[l])[:n_lanes]
    lane_set = set(chosen)
    demand = {
        (l, d): u
        for u, d, l in zip(slot_state["usage"], slot_state["depart_atu"],
                           slot_state["lane_id"])
        if l in lane_set and lo <= d < hi and u > 0
    }
    return Cut(tuple(sorted(chosen)), (lo, hi), demand)


def apply_rho(
    bundle: ScenarioBundle, cut: Cut, rho: float,
) -> ScenarioBundle:
    """Return a new bundle with capacity[s] := max(1, ceil(D(s)/rho)) on S_cut.

    All non-cut schedule rows are preserved verbatim (checked); node,
    lane, and demand IPC are shared by reference (bytes are immutable).

    Raises:
        ValueError: if rho <= 0, or any cut slot is missing from the
            schedule table (a cut/bundle mismatch).
    """
    if rho <= 0:
        raise ValueError(f"rho must be > 0, got {rho}")
    tbl = ipc.open_stream(bundle.schedule_ipc).read_all()
    lane = tbl.column("lane_id").to_pylist()
    dep = tbl.column("depart_atu").to_pylist()
    cap = tbl.column("capacity").to_pylist()

    new_cap = list(cap)
    patched: dict[tuple[int, int], int] = {}
    for i, (l, d) in enumerate(zip(lane, dep)):
        dem = cut.demand.get((l, d))
        if dem is not None:
            new_cap[i] = max(1, math.ceil(dem / rho))
            patched[(l, d)] = new_cap[i]
    if len(patched) != len(cut.demand):
        missing = sorted(set(cut.demand) - set(patched))
        raise ValueError(f"cut slots missing from schedule: {missing[:5]}")

    rows = [
        {"lane_id": l, "depart_atu": d, "capacity": c}
        for l, d, c in zip(lane, dep, new_cap)
    ]
    schedule_ipc = to_schedule_ipc(rows)

    # Self-check: decode round-trip; rows/order identical, non-cut
    # capacities untouched.
    chk = ipc.open_stream(schedule_ipc).read_all()
    assert chk.column("lane_id").to_pylist() == lane
    assert chk.column("depart_atu").to_pylist() == dep
    chk_cap = chk.column("capacity").to_pylist()
    for i, (l, d) in enumerate(zip(lane, dep)):
        if (l, d) not in cut.demand:
            assert chk_cap[i] == cap[i], (l, d)

    return ScenarioBundle(
        node_ipc=bundle.node_ipc,
        lane_ipc=bundle.lane_ipc,
        schedule_ipc=schedule_ipc,
        demand_ipc=bundle.demand_ipc,
        manifest={
            **bundle.manifest,
            "bottleneck_knob": {
                "rho": rho,
                "lane_ids": list(cut.lane_ids),
                "window_atu": list(cut.window_atu),
                "n_slots_patched": len(patched),
                "cut_free_flow_units": cut.total_demand(),
                "patched_capacities": {
                    f"{l}:{d}": c for (l, d), c in sorted(patched.items())
                },
            },
        },
    )


def cut_utilization(
    bundle: ScenarioBundle, cut: Cut, horizon_hours: int = 24,
) -> dict[str, Any]:
    """Run the plain DES on ``bundle`` and report realized cut utilization.

    Returns per-slot (usage, capacity) on S_cut plus the aggregate
    utilization and the strand/queue KPI subset needed by the
    self-report.
    """
    ss, kpis = free_flow_slot_state(bundle, horizon_hours)
    per_slot = []
    tot_u = tot_c = 0
    for u, c, d, l in zip(ss["usage"], ss["capacity"], ss["depart_atu"],
                          ss["lane_id"]):
        if (l, d) in cut.demand:
            per_slot.append(
                {"lane_id": l, "hour": d // _H, "usage": u, "capacity": c,
                 "util": u / c if c else None},
            )
            tot_u += u
            tot_c += c
    sr = kpis["strand_reasons"]
    return {
        "per_slot": per_slot,
        "cut_usage": tot_u,
        "cut_capacity": tot_c,
        "cut_util": tot_u / tot_c if tot_c else None,
        "on_time": kpis["total_on_time"],
        "late": kpis["total_late"],
        "stranded": kpis["stranded_flow_units"],
        "cap_exhausted": sr["capacity_exhausted"]["count"],
        "sched_infeasible": sr["schedule_infeasible"]["count"],
        "mean_cap_wait_atu": sr["capacity_exhausted"]["mean_wait_atu"],
    }
