"""Schedule generators — produce slot dicts (lane_id, depart_atu, capacity).

Cadences:
    hourly    — 24 slots per lane per day (every ATU_PER_HOUR).
    every_4h  — 6 slots/day.
    daily     — 1 slot/day at t=0.
    mixed     — per-lane random pick from {1, 6, 24} slots/day.

Capacity per slot is left as a placeholder (capacity=1 here); the regime
applier in `scenarios.py` rewrites capacities so the *aggregate* across
all slots matches the requested regime.
"""

from __future__ import annotations

import random
from typing import Any

_ATU_PER_HOUR: int = 3_600_000


def _slots_for_cadence(cadence: str, horizon_hours: int) -> list[int]:
    """Departure ATUs for a single lane at the given cadence."""
    if cadence == "hourly":
        step_h = 1
    elif cadence == "every_4h":
        step_h = 4
    elif cadence == "daily":
        step_h = 24
    else:
        raise ValueError(f"unknown cadence: {cadence}")
    return [h * _ATU_PER_HOUR for h in range(0, horizon_hours, step_h)]


def make_schedules(
    lanes: list[dict[str, Any]],
    cadence: str = "hourly",
    horizon_hours: int = 24,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Build a flat slot list. Capacities default to 1; rewrite in `apply_capacity`."""
    rng = random.Random(seed ^ 0xC0DE)
    out: list[dict[str, Any]] = []
    for e in lanes:
        if cadence == "mixed":
            per_lane_cadence = rng.choice(("hourly", "every_4h", "daily"))
        else:
            per_lane_cadence = cadence
        for depart_atu in _slots_for_cadence(per_lane_cadence, horizon_hours):
            out.append({
                "lane_id": e["lane_id"],
                "depart_atu": depart_atu,
                "capacity": 1,
            })
    return out
