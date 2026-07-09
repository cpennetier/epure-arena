"""Seeded synthetic disruption suite (C2 Phase 1).

Generates deterministic disruption schedules over a ScenarioBundle using ONLY
the existing shock machinery: lane-outage windows, node-outage windows, slot
cancellations (a capacity shock IS a deterministic set of per-slot outage
windows ``[depart, depart+1)`` — no new engine mutation), and demand surges
(extra demand injected at onset via ``PyEngine.inject_demand``).

REALISM, DECLARED: these generators produce *abstract* shocks — uniformly
drawn affected sets, rectangular time windows, instantaneous onset with
perfect harness knowledge of the window. They do NOT represent: gradual
degradation, partial capacity loss on an in-flight vehicle, correlated
cascades (weather fronts), forecast uncertainty about duration, or
recovery-time variance. Conclusions are about decision structure under
clean shocks, not about real-world disruption statistics.

Main exports: :class:`Disruption`, :func:`make_disruptions`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

import pyarrow.ipc as ipc

from epure_arena.scenarios.demand import make_demand
from epure_arena.scenarios.serializer import to_demand_ipc

_H = 3_600_000

# Severity knobs: (n_targets, duration_hours, slot_cancel_fraction, surge_fraction)
_SEVERITY = {
    "mild": (2, 2, 0.25, 0.05),
    "moderate": (5, 4, 0.50, 0.15),
    "severe": (10, 8, 0.75, 0.30),
}

# Surge flow_unit_ids live far above any base demand id.
SURGE_ID_BASE = 10_000_000


@dataclass(frozen=True)
class Disruption:
    """One typed disruption event (the unit of a decision epoch)."""

    kind: str  # lane_outage | node_outage | capacity_shock | demand_surge
    onset_atu: int
    end_atu: int
    lane_ids: tuple[int, ...] = ()
    node_ids: tuple[int, ...] = ()
    # capacity_shock: one (lane_id, start, end) window per CANCELLED slot.
    slot_windows: tuple[tuple[int, int, int], ...] = ()
    surge_ipc: bytes | None = None
    descriptor: dict[str, Any] = field(default_factory=dict)


def make_disruptions(
    bundle,
    *,
    kinds: tuple[str, ...] = ("lane_outage",),
    severity: str = "moderate",
    horizon_hours: int = 24,
    seed: int = 0,
    events_per_kind: int = 1,
) -> list[Disruption]:
    """Deterministic disruption schedule for a bundle (``events_per_kind``
    events per kind — multi-epoch suites for the C2 budgeted arms; onsets
    staggered inside [0.2, 0.6] × horizon so the loop has room to respond).
    Identical (bundle, kinds, severity, seed, events_per_kind) ⇒ identical
    schedule.
    """
    n_targets, dur_h, cancel_frac, surge_frac = _SEVERITY[severity]
    rng = random.Random(seed ^ 0xD15C0)

    lanes_tbl = ipc.open_stream(bundle.lane_ipc).read_all()
    lane_ids_all = sorted(int(e) for e in lanes_tbl.column("lane_id").to_pylist())
    nodes_tbl = ipc.open_stream(bundle.node_ipc).read_all()
    node_ids_all = sorted(int(h) for h in nodes_tbl.column("node_id").to_pylist())
    sched_tbl = ipc.open_stream(bundle.schedule_ipc).read_all()
    sched = list(zip(
        (int(x) for x in sched_tbl.column("lane_id").to_pylist()),
        (int(x) for x in sched_tbl.column("depart_atu").to_pylist()),
    ))

    out: list[Disruption] = []
    onset_lo, onset_hi = int(0.2 * horizon_hours), int(0.6 * horizon_hours)
    for kind in kinds:
      for _rep in range(events_per_kind):
          onset = rng.randint(onset_lo, max(onset_lo, onset_hi)) * _H
          end = onset + dur_h * _H
          if kind == "lane_outage":
              targets = tuple(sorted(rng.sample(lane_ids_all,
                                                min(n_targets, len(lane_ids_all)))))
              out.append(Disruption(
                  kind=kind, onset_atu=onset, end_atu=end, lane_ids=targets,
                  descriptor={"kind": kind, "severity": severity,
                              "lanes": list(targets), "onset_h": onset // _H,
                              "dur_h": dur_h},
              ))
          elif kind == "node_outage":
              targets = tuple(sorted(rng.sample(node_ids_all,
                                                min(max(1, n_targets // 2),
                                                    len(node_ids_all)))))
              out.append(Disruption(
                  kind=kind, onset_atu=onset, end_atu=end, node_ids=targets,
                  descriptor={"kind": kind, "severity": severity,
                              "nodes": list(targets), "onset_h": onset // _H,
                              "dur_h": dur_h},
              ))
          elif kind == "capacity_shock":
              targets = tuple(sorted(rng.sample(lane_ids_all,
                                                min(n_targets, len(lane_ids_all)))))
              windows: list[tuple[int, int, int]] = []
              for eid in targets:
                  slots_in = sorted(
                      dep for lid, dep in sched
                      if lid == eid and onset <= dep < end
                  )
                  k = int(len(slots_in) * cancel_frac)
                  for dep in slots_in[:k]:  # deterministic: earliest k
                      windows.append((eid, dep, dep + 1))
              out.append(Disruption(
                  kind=kind, onset_atu=onset, end_atu=end, lane_ids=targets,
                  slot_windows=tuple(windows),
                  descriptor={"kind": kind, "severity": severity,
                              "lanes": list(targets),
                              "slots_cancelled": len(windows),
                              "onset_h": onset // _H, "dur_h": dur_h},
              ))
          elif kind == "demand_surge":
              tiers = nodes_tbl.column("tier").to_pylist()
              nodes_rows = [
                  {"node_id": int(h), "tier": t, "demand_gravity": 1.0}
                  for h, t in zip(nodes_tbl.column("node_id").to_pylist(), tiers)
              ]
              n_extra = max(1, int(surge_frac
                                   * ipc.open_stream(bundle.demand_ipc)
                                   .read_all().num_rows))
              window_h = max(1, dur_h)
              events = make_demand(
                  nodes_rows, [], n_extra, pattern="uniform",
                  priority_mix="mixed", horizon_hours=window_h,
                  seed=seed ^ 0x5E1F ^ (_rep * 0x9E37),
              )
              for i, ev in enumerate(events):
                  # distinct id range per repetition (multi-epoch suites)
                  ev["flow_unit_id"] = SURGE_ID_BASE + _rep * 1_000_000 + i
                  ev["appear_atu"] += onset
                  ev["due_atu"] += onset
              events.sort(key=lambda e: (e["appear_atu"], e["flow_unit_id"]))
              out.append(Disruption(
                  kind=kind, onset_atu=onset, end_atu=end,
                  surge_ipc=to_demand_ipc(events),
                  descriptor={"kind": kind, "severity": severity,
                              "n_extra": n_extra, "onset_h": onset // _H,
                              "dur_h": dur_h},
              ))
          else:
              raise ValueError(f"unknown disruption kind {kind!r}")
    out.sort(key=lambda d: (d.onset_atu, d.kind))
    return out
