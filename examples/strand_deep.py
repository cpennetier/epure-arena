#!/usr/bin/env python
"""Deep per-flow_unit dissection of one scenario cell's strand buckets.

Promoted from the 2026-06-10 investigation scratch driver (plan report §3.2).
For one regime it reports: the strand-site split, where ROUTER-INDUCED flow_units
were wasted (origin vs mid-route, slack percentiles, top waste nodes), the
capacity bucket's schedule-supply check, the GENUINE cliff/structural split,
and (optionally) the capacity-off ablation — emitted as a run_id folder.

Usage (node-engine venv, repo root):
    python examples/strand_deep.py --cadence hourly --capacity tight
    python examples/strand_deep.py --cadence hourly --capacity tight --ablate
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from epure_arena import _engine

from epure_arena.harness.feasibility_oracle import (
    GENUINE,
    ROUTER_INDUCED,
    classify_engine,
)
from epure_arena.harness.runlake import emit_run
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario

_ATU_PER_HOUR = 3_600_000
_SITE = {0: "RouterNone", 1: "ChosenLaneNoSlot", 2: "QueueDeath",
         3: "TopologyPrecheck", 4: "NodeOutageArrival", 5: "DeadlineFinalize"}


def _pctile(xs: list[float], p: float) -> float:
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def dissect(bundle, horizon: int, enforce_capacity: bool) -> dict:
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        _ATU_PER_HOUR, "time_aware",
    )
    eng.set_enforce_capacity(enforce_capacity)
    eng.run(horizon * _ATU_PER_HOUR)
    kpis = json.loads(eng.get_kpis())
    sr = eng.strand_records()
    n = len(sr["reason"])

    report = classify_engine(eng, reasons=(1, 2, 3))
    ri = [v for v in report.verdicts if v.reason == 1 and v.verdict == ROUTER_INDUCED]
    slacks = sorted((v.due_atu - v.arrival_atu) / _ATU_PER_HOUR for v in ri)
    cap = [v for v in report.verdicts if v.reason == 2]
    gen = [v for v in report.verdicts if v.verdict == GENUINE]

    return {
        "enforce_capacity": enforce_capacity,
        "injected": kpis["total_injected"],
        "stranded": kpis["stranded_flow_units"],
        "by_site": dict(Counter(_SITE.get(s, s) for s in sr["site"])),
        "router_induced": {
            "n": len(ri),
            "stranded_at_origin": sum(1 for v in ri if v.strand_node == v.origin),
            "stranded_mid_route": sum(1 for v in ri if v.strand_node != v.origin),
            "slack_hours_p10": round(_pctile(slacks, 0.10), 2),
            "slack_hours_p50": round(_pctile(slacks, 0.50), 2),
            "slack_hours_p90": round(_pctile(slacks, 0.90), 2),
            "top_waste_nodes": Counter(v.strand_node for v in ri).most_common(10),
        },
        "capacity_bucket_supply": dict(Counter(v.verdict for v in cap)),
        "genuine": {
            "n": len(gen),
            "cliff": report.genuine_cliff,
            "structural": report.genuine_structural,
            "top_dest_sinks": Counter(v.dest for v in gen).most_common(10),
        },
        "schema_fingerprint": eng.get_schema_fingerprint(),
        "n_strand_records": n,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topology", default="backbone")
    ap.add_argument("--nodes", type=int, default=200)
    ap.add_argument("--density", default="moderate")
    ap.add_argument("--capacity", default="tight")
    ap.add_argument("--cadence", default="hourly")
    ap.add_argument("--pattern", default="diurnal")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ablate", action="store_true",
                    help="also run the capacity-off ablation")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    spec = ScenarioSpec(
        topology=args.topology, n_nodes=args.nodes, density=args.density,
        capacity=args.capacity, demand_pattern=args.pattern,
        schedule_cadence=args.cadence, priority_mix="mixed",
        n_demand_events=args.demand, horizon_hours=args.horizon, seed=args.seed,
    )
    bundle = generate_scenario(spec)

    rows = [dissect(bundle, args.horizon, True)]
    if args.ablate:
        rows.append(dissect(bundle, args.horizon, False))
    for r in rows:
        print(json.dumps(r))

    if not args.no_emit:
        folder = emit_run(
            name="strand-deep",
            config=vars(args),
            seed=args.seed,
            schema_fingerprint=rows[0]["schema_fingerprint"],
            metrics_rows=rows,
            repro_cmd="python examples/strand_deep.py " + " ".join(sys.argv[1:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
