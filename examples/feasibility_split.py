#!/usr/bin/env python
"""Split a run's ``schedule_infeasible`` strands into GENUINE shortage vs
ROUTER-INDUCED (recoverable) vs DEADLINE-bound, using the offline feasibility
oracle (capacity ignored; see node_engine.analysis.feasibility_oracle).

Generates a DES-ready scenario via the testkit, runs it through the engine, and
classifies. Read-only analysis — no routing/strand behavior is changed.

Usage (from repo root, node-engine venv):
    python examples/feasibility_split.py
    python examples/feasibility_split.py --nodes 300 --demand 80000 --cadence every_4h
"""

from __future__ import annotations

import argparse
import json

from epure_arena import _engine

from epure_arena.harness.feasibility_oracle import (
    classify_engine,
    genuine_seeds,
    router_induced_waste,
)
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario

_ATU_PER_HOUR = 3_600_000
_REASON = {0: "topology_unreachable", 1: "schedule_infeasible", 2: "capacity_exhausted", 3: "deadline_missed"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topology", default="backbone")
    ap.add_argument("--nodes", type=int, default=200)
    ap.add_argument("--density", default="moderate")
    ap.add_argument("--demand", type=int, default=40000)
    ap.add_argument("--cadence", default="every_4h")
    ap.add_argument("--capacity", default="loose")
    ap.add_argument("--pattern", default="diurnal")
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    spec = ScenarioSpec(
        topology=args.topology, n_nodes=args.nodes, density=args.density,
        capacity=args.capacity, demand_pattern=args.pattern,
        schedule_cadence=args.cadence, priority_mix="mixed",
        n_demand_events=args.demand, horizon_hours=args.horizon, seed=args.seed,
    )
    bundle = generate_scenario(spec)

    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        _ATU_PER_HOUR, "time_aware",
    )
    eng.set_enforce_capacity(True)
    eng.run(args.horizon * _ATU_PER_HOUR)

    kpis = json.loads(eng.get_kpis())
    injected = kpis["total_injected"]
    stranded = kpis["stranded_flow_units"]

    # Per-reason strand counts (from the additive strand log).
    sr = eng.strand_records()
    from collections import Counter
    by_reason = Counter(sr["reason"])

    report = classify_engine(eng)

    print("=" * 70)
    print(f"SCENARIO: {args.topology} nodes={args.nodes} demand={args.demand} "
          f"cadence={args.cadence} capacity={args.capacity} horizon={args.horizon}h")
    print(f"injected={injected}  stranded={stranded} ({100*stranded/max(injected,1):.1f}%)")
    print("strand reasons:", {_REASON.get(r, r): c for r, c in by_reason.most_common()})
    cap_refusals = by_reason.get(2, 0)
    print(f"capacity_exhausted strands = {cap_refusals} "
          f"(oracle IGNORES capacity by design — this run is schedule-bound)")
    print("-" * 70)
    n = report.n_schedule_infeasible
    print(f"schedule_infeasible HEADLINE SPLIT (n={n}):")
    print(f"  ROUTER-INDUCED (feasible on time) : {report.router_induced:7d}  "
          f"({report.pct(report.router_induced):5.1f}%)")
    print(f"  DEADLINE-bound (feasible late)    : {report.deadline_bound:7d}  "
          f"({report.pct(report.deadline_bound):5.1f}%)")
    print(f"  GENUINE shortage (no path)        : {report.genuine:7d}  "
          f"({report.pct(report.genuine):5.1f}%)")
    print("-" * 70)
    seeds = genuine_seeds(report, top_k=10)
    print("GENUINE shortage seed — top destination sinks (dest_node, count):")
    print("  ", seeds["by_dest"][:10])
    print("GENUINE shortage seed — top O-D pairs ((origin,dest), count):")
    print("  ", seeds["by_od"][:10])
    print("-" * 70)
    waste = router_induced_waste(report, top_k=10)
    print(f"ROUTER-INDUCED waste — count={waste['count']}; "
          f"nodes where recoverable flow_units were wasted (strand_node, count):")
    print("  ", waste["by_strand_node"][:10])
    print("=" * 70)


if __name__ == "__main__":
    main()
